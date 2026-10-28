import copy
from datetime import UTC

from fastapi import HTTPException
from sqlalchemy import update

from app.dependencies import can_use_agent
from app.models import (
    Agent,
    AgentVersion,
    Conversation,
    Message,
    ModelConnection,
    Run,
    RunEvent,
    ToolDefinition,
    User,
    new_id,
    utcnow,
)
from app.routers.agents import validate_publication

TERMINAL = {"completed", "failed", "cancelled", "expired", "outcome_unknown"}


def aware(value):
    return value.replace(tzinfo=UTC)


def owned(db, cls, identifier, user):
    obj = db.get(cls, str(identifier))
    if not obj or obj.owner_id != user.id:
        raise HTTPException(404, "Resource not found")
    return obj


def event(db, run_id, kind, data):
    from app.runtime.dto import EVENT_SCHEMAS

    data = EVENT_SCHEMAS[kind].model_validate(data).model_dump(mode="json")
    seq = db.scalar(
        update(Run)
        .where(Run.id == run_id)
        .values(next_event_seq=Run.next_event_seq + 1)
        .returning(Run.next_event_seq)
    )
    db.add(RunEvent(run_id=run_id, seq=seq, kind=kind, data=data))


def finish(db, run, status, error=None):
    run.status = status
    run.error = error
    run.finished_at = utcnow()
    run.lease_token = None
    run.lease_expires_at = None
    if error:
        event(db, run.id, "error", {"message": error})
    event(db, run.id, "done", {"status": status})
    db.execute(
        update(Conversation)
        .where(Conversation.id == run.conversation_id, Conversation.active_run_id == run.id)
        .values(active_run_id=None)
    )


def live_access(db, run, tool_id=None):
    from app.runtime.budget import lease_token

    token = lease_token.get()
    if token and (
        run.lease_token != token
        or not run.lease_expires_at
        or aware(run.lease_expires_at) <= utcnow()
    ):
        raise HTTPException(409, "Execution lease is no longer valid")
    user = db.get(User, run.owner_id)
    conversation = db.get(Conversation, run.conversation_id)
    agent = db.get(Agent, conversation.agent_id)
    if not user or not user.active or not agent or not agent.enabled:
        raise HTTPException(403, "Access is no longer available")
    if run.debug:
        if user.role != "admin":
            raise HTTPException(403, "Access is no longer available")
    elif not can_use_agent(user, agent):
        raise HTTPException(403, "Access is no longer available")
    model = db.get(ModelConnection, run.snapshot["model_id"])
    if not model or not model.enabled:
        raise HTTPException(403, "Model is no longer available")
    if tool_id:
        tool = db.get(ToolDefinition, tool_id)
        if (
            not tool
            or not tool.enabled
            or not set(user.group_ids).intersection(tool.allowed_group_ids)
        ):
            raise HTTPException(403, "Tool is no longer available")
    return user


def snapshot(db, agent, state, debug=False):
    config = copy.deepcopy(
        agent.draft if debug else db.get(AgentVersion, agent.published_version_id).snapshot
    )
    validate_publication(db, config, state)
    model = db.get(ModelConnection, config["model_id"])
    config["model"] = {"base_url": model.base_url, "model": model.model}
    config["tools"] = []
    for tid in config.get("tool_ids", []):
        tool = db.get(ToolDefinition, tid)
        config["tools"].append(
            {
                key: copy.deepcopy(getattr(tool, key))
                for key in (
                    "id",
                    "name",
                    "title",
                    "description",
                    "transport",
                    "endpoint",
                    "method",
                    "mcp_tool_name",
                    "input_schema",
                    "fixed_arguments",
                    "effect",
                    "policy",
                    "timeout_seconds",
                    "idempotency_header",
                )
            }
        )
    return config


def create_run(db, state, user, conversation, data, debug=False):
    agent = db.get(Agent, conversation.agent_id)
    if not debug and not can_use_agent(user, agent):
        raise HTTPException(403, "Agent is no longer available")
    if debug and (user.role != "admin" or not agent.enabled):
        raise HTTPException(403, "Agent is no longer available")
    if data.attachment_ids:
        validator = state.attachment_validator
        if validator is None:
            raise HTTPException(422, "Attachments are not available")
        validator(db, user, conversation, data.attachment_ids)
    config = snapshot(db, agent, state, debug)
    rid = new_id()
    claimed = db.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id, Conversation.active_run_id.is_(None))
        .values(active_run_id=rid)
    ).rowcount
    if not claimed:
        raise HTTPException(409, "Conversation already has an active run")
    run = Run(
        id=rid,
        owner_id=user.id,
        conversation_id=conversation.id,
        snapshot=config,
        debug=debug,
        attachment_ids=data.attachment_ids,
    )
    db.add(run)
    db.flush()
    db.add(Message(conversation_id=conversation.id, run_id=rid, role="user", content=data.message))
    event(db, rid, "status", {"status": "queued", "message": "Queued"})
    db.commit()
    return run
