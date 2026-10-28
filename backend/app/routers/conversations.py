import asyncio
import json
from datetime import UTC
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select, update

from app.dependencies import DB, Admin, CurrentUser, can_use_agent, require_csrf
from app.models import Agent, Approval, Conversation, Feedback, Message, Run, RunEvent, ToolCall
from app.runtime.dto import (
    ConversationInput,
    ConversationRead,
    FeedbackInput,
    MessageRead,
    RunAccepted,
    RunInput,
    RunRead,
)
from app.runtime.store import TERMINAL, create_run, finish, lock_run, owned
from app.security import audit

router = APIRouter(tags=["conversations"])
Member = Annotated[object, Depends(require_csrf)]


def dispatch(state, run_id, resume=False):
    if state.dispatch_run:
        state.dispatch_run(run_id, resume=resume)
    else:
        from app.worker import execute_run, resume_run

        try:
            (resume_run if resume else execute_run).delay(run_id)
        except Exception:
            # The committed queued run is the durable outbox; beat retries dispatch.
            return False


@router.get("/conversations", response_model=list[ConversationRead])
def conversations(db: DB, user: CurrentUser):
    return db.scalars(
        select(Conversation)
        .where(Conversation.owner_id == user.id)
        .order_by(Conversation.created_at.desc())
    ).all()


@router.post("/conversations", response_model=ConversationRead, status_code=201)
def add_conversation(data: ConversationInput, db: DB, user: Member):
    agent = db.get(Agent, str(data.agent_id))
    if not agent or not can_use_agent(user, agent):
        raise HTTPException(404, "Agent not found")
    conversation = Conversation(owner_id=user.id, agent_id=agent.id, title=data.title)
    db.add(conversation)
    db.commit()
    return conversation


@router.get("/conversations/{identifier}", response_model=ConversationRead)
def get_conversation(identifier: UUID, db: DB, user: CurrentUser):
    return owned(db, Conversation, identifier, user)


@router.delete("/conversations/{identifier}", status_code=204)
def delete_conversation(identifier: UUID, request: Request, db: DB, user: Member):
    conversation = owned(db, Conversation, identifier, user)
    available = db.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id, Conversation.active_run_id.is_(None))
        .values(title=Conversation.title)
    ).rowcount
    if not available:
        raise HTTPException(409, "Cancel the active run before deleting the conversation")
    run_ids = list(db.scalars(select(Run.id).where(Run.conversation_id == conversation.id)))
    from app.runtime.checkpoints import delete_checkpoints

    delete_checkpoints(request.app.state, run_ids)
    for hook in request.app.state.conversation_cleanup_hooks:
        hook(db, conversation.id)
    audit(db, user, "delete", "conversation", conversation.id)
    db.execute(delete(Conversation).where(Conversation.id == conversation.id))
    db.commit()


@router.get("/conversations/{identifier}/messages", response_model=list[MessageRead])
def messages(identifier: UUID, db: DB, user: CurrentUser):
    owned(db, Conversation, identifier, user)
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == str(identifier))
        .order_by(Message.created_at)
    ).all()
    from app.knowledge.access import message_content, visible_citations

    result = []
    for row in rows:
        value = MessageRead.model_validate(row).model_dump(mode="json")
        value["citations"] = visible_citations(db, user, row.citations)
        value["content"] = message_content(db, user, row)
        result.append(value)
    return result


@router.post("/conversations/{identifier}/runs", response_model=RunAccepted, status_code=202)
def add_run(identifier: UUID, data: RunInput, request: Request, db: DB, user: Member):
    conversation = owned(db, Conversation, identifier, user)
    run = create_run(db, request.app.state, user, conversation, data)
    dispatch(request.app.state, run.id)
    return {"id": run.id, "status": run.status}


@router.post("/admin/agents/{identifier}/debug", response_model=RunAccepted, status_code=202)
def debug(identifier: UUID, data: RunInput, request: Request, db: DB, admin: Admin):
    agent = db.get(Agent, str(identifier))
    if not agent:
        raise HTTPException(404, "Agent not found")
    conversation = Conversation(owner_id=admin.id, agent_id=agent.id, title="Debug")
    db.add(conversation)
    db.flush()
    run = create_run(db, request.app.state, admin, conversation, data, debug=True)
    dispatch(request.app.state, run.id)
    return {"id": run.id, "status": run.status}


def approval_dto(db, approval):
    call = db.get(ToolCall, approval.tool_call_id)
    run = db.get(Run, call.run_id)
    config = next(t for t in run.snapshot["tools"] if t["id"] == call.tool_id)
    return {
        "id": approval.id,
        "title": config["title"],
        "summary": f"{config['effect'].title()} operation: {config['name']}",
        "parameters": call.arguments,
        "expires_at": approval.expires_at,
    }


@router.get("/runs/{identifier}", response_model=RunRead)
def get_run(identifier: UUID, db: DB, user: CurrentUser):
    run = owned(db, Run, identifier, user)
    approvals = (
        db.scalars(
            select(Approval).where(Approval.run_id == run.id, Approval.decision.is_(None))
        ).all()
        if run.status == "awaiting_confirmation"
        else []
    )
    from app.knowledge.access import references_hidden

    if references_hidden(db, user, run.id):
        approvals = []
    return {
        "id": run.id,
        "status": run.status,
        "error": run.error,
        "created_at": run.created_at,
        "approvals": [approval_dto(db, a) for a in approvals],
    }


@router.post("/runs/{identifier}/cancel", response_model=RunAccepted)
def cancel(identifier: UUID, db: DB, user: Member):
    run = owned(db, Run, identifier, user)
    run = lock_run(db, run.id, worker=False)
    run.cancel_requested = True
    if run.status != "running":
        uncertain = db.scalars(
            select(ToolCall).where(
                ToolCall.run_id == run.id,
                ToolCall.effect == "write",
                ToolCall.status.in_(["submitted", "outcome_unknown"]),
            )
        ).all()
        if uncertain and run.status != "outcome_unknown":
            for call in uncertain:
                call.status = "outcome_unknown"
            finish(db, run, "outcome_unknown", "Tool outcome requires administrator verification")
        elif run.status not in TERMINAL:
            finish(db, run, "cancelled")
    audit(db, user, "cancel", "run", run.id)
    db.commit()
    return {"id": run.id, "status": run.status}


@router.get("/runs/{identifier}/events")
def events(identifier: UUID, request: Request, db: DB, user: CurrentUser, after: int = 0):
    owned(db, Run, identifier, user)
    try:
        cursor = max(0, after, int(request.headers.get("Last-Event-ID", "0")))
    except ValueError:
        raise HTTPException(422, "Invalid event cursor") from None
    owner_id, state = user.id, request.app.state
    login_session_id = request.state.login_session.id

    async def stream():
        nonlocal cursor
        while True:
            with state.session_factory() as session:
                from app.models import LoginSession, User, utcnow

                login_session = session.get(LoginSession, login_session_id)
                current_user = session.get(User, owner_id)
                run = session.get(Run, str(identifier))
                if (
                    not login_session
                    or login_session.user_id != owner_id
                    or login_session.expires_at.replace(tzinfo=UTC) <= utcnow()
                    or not current_user
                    or not current_user.active
                    or not run
                    or run.owner_id != owner_id
                ):
                    return
                rows = session.scalars(
                    select(RunEvent)
                    .where(RunEvent.run_id == run.id, RunEvent.seq > cursor)
                    .order_by(RunEvent.seq)
                    .limit(200)
                ).all()
                terminal = run.status in TERMINAL
                from app.knowledge.access import UNAVAILABLE_ANSWER, references_hidden

                hidden = references_hidden(session, current_user, run.id)
                for row in rows:
                    cursor = row.seq
                    if hidden and row.kind in {"delta", "citation", "approval"}:
                        continue
                    data = (
                        {"message": UNAVAILABLE_ANSWER}
                        if hidden and row.kind == "result"
                        else row.data
                    )
                    if row.kind == "citation":
                        from app.knowledge.access import visible_citations

                        if not visible_citations(session, current_user, [row.data]):
                            continue
                    yield f"id: {row.seq}\nevent: {row.kind}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
            if terminal and len(rows) < 200:
                return
            if await request.is_disconnected():
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/messages/{identifier}/feedback")
def feedback(identifier: UUID, data: FeedbackInput, db: DB, user: Member):
    message = db.get(Message, str(identifier))
    if not message:
        raise HTTPException(404, "Message not found")
    owned(db, Conversation, message.conversation_id, user)
    if message.role != "assistant":
        raise HTTPException(422, "Feedback requires an assistant message")
    record = db.scalar(select(Feedback).where(Feedback.message_id == message.id))
    if not record:
        record = Feedback(message_id=message.id, owner_id=user.id)
        db.add(record)
    record.rating, record.comment = data.rating, data.comment
    db.commit()
    return {"rating": record.rating}
