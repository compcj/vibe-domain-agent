import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select, update

from app.dependencies import DB, Admin
from app.models import (
    Agent,
    AgentVersion,
    Approval,
    Group,
    Message,
    Run,
    ToolCall,
    ToolDefinition,
    utcnow,
)
from app.routers.conversations import Member, dispatch
from app.runtime.dto import Decision, RunAccepted
from app.runtime.store import aware, event, finish, live_access, lock_run
from app.runtime.tool_schemas import DiscoverInput, ResolveInput, ToolInput
from app.runtime.tools import call_config, validate_frozen
from app.runtime.transports import mcp_session
from app.security import audit, encrypt_secret

router = APIRouter(tags=["tools"])
FIELDS = tuple(ToolInput.model_fields.keys() - {"headers"})


def read_tool(tool):
    return {
        "id": tool.id,
        **{field: getattr(tool, field) for field in FIELDS},
        "has_headers": bool(tool.headers_encrypted),
    }


def tool_object(db, identifier):
    tool = db.get(ToolDefinition, str(identifier))
    if not tool:
        raise HTTPException(404, "Tool not found")
    return tool


def apply(db, tool, data, settings, set_headers=True):
    if set(data.allowed_group_ids) - set(db.scalars(select(Group.id))):
        raise HTTPException(422, "Unknown group")
    for field in FIELDS:
        setattr(tool, field, getattr(data, field))
    if set_headers:
        try:
            tool.headers_encrypted = (
                encrypt_secret(json.dumps(data.headers), settings) if data.headers else None
            )
        except ValueError:
            raise HTTPException(503, "Server encryption is not configured") from None


@router.get("/admin/tools")
def list_tools(db: DB, admin: Admin):
    return [read_tool(tool) for tool in db.scalars(select(ToolDefinition))]


@router.post("/admin/tools", status_code=201)
def create_tool(data: ToolInput, request: Request, db: DB, admin: Admin):
    tool = ToolDefinition()
    apply(db, tool, data, request.app.state.settings)
    db.add(tool)
    db.flush()
    audit(db, admin, "create_policy", "tool", tool.id)
    db.commit()
    return read_tool(tool)


@router.patch("/admin/tools/{identifier}")
def patch_tool(identifier: UUID, request: Request, db: DB, admin: Admin, data: dict = Body()):
    tool = tool_object(db, identifier)
    current = {field: getattr(tool, field) for field in FIELDS}
    if "effect" in data and "policy" not in data:
        current["policy"] = None
    try:
        parsed = ToolInput.model_validate({**current, **data})
    except ValidationError:
        raise HTTPException(422, "Invalid tool configuration") from None
    apply(db, tool, parsed, request.app.state.settings, set_headers="headers" in data)
    audit(db, admin, "update_policy", "tool", tool.id)
    db.commit()
    return read_tool(tool)


@router.delete("/admin/tools/{identifier}", status_code=204)
def delete_tool(identifier: UUID, db: DB, admin: Admin):
    tool = tool_object(db, identifier)
    references = [a.draft for a in db.scalars(select(Agent))] + [
        v.snapshot for v in db.scalars(select(AgentVersion))
    ]
    if any(tool.id in config.get("tool_ids", []) for config in references) or db.scalar(
        select(ToolCall.id).where(ToolCall.tool_id == tool.id).limit(1)
    ):
        raise HTTPException(409, "Tool is referenced; disable it instead")
    audit(db, admin, "delete", "tool", tool.id)
    db.delete(tool)
    db.commit()


@router.post("/admin/tools/discover")
def discover(data: DiscoverInput, db: DB, admin: Admin):
    try:
        result = asyncio.run(mcp_session(data.endpoint, data.headers, 30))
        if len(json.dumps(result)) > 200000:
            raise ValueError()
    except Exception:
        raise HTTPException(502, "MCP discovery failed") from None
    audit(db, admin, "discover", "tool")
    db.commit()
    return result


@router.post("/approvals/{identifier}/decision", response_model=RunAccepted)
def decide(identifier: UUID, data: Decision, request: Request, db: DB, user: Member):
    approval = db.get(Approval, str(identifier))
    if not approval:
        raise HTTPException(404, "Approval not found")
    # Serialize decisions for different approvals on the same run across workers.
    run = lock_run(db, approval.run_id, worker=False)
    db.refresh(approval)
    if run.owner_id != user.id:
        raise HTTPException(404, "Resource not found")
    call = db.get(ToolCall, approval.tool_call_id)
    if approval.decision is not None:
        if approval.decision != data.approved:
            raise HTTPException(409, "Approval already decided")
        return {"id": run.id, "status": run.status}
    if run.status != "awaiting_confirmation":
        raise HTTPException(409, "Run is not awaiting confirmation")
    live_access(db, run, call.tool_id)
    validate_frozen(call)
    if approval.arguments_hash != call.arguments_hash:
        raise HTTPException(409, "Approval parameters have changed")
    if aware(approval.expires_at) <= utcnow():
        call.status = "expired"
        finish(db, run, "expired", "Confirmation expired")
        db.commit()
        raise HTTPException(409, "Confirmation expired")
    updated = db.execute(
        update(Approval)
        .where(Approval.id == approval.id, Approval.decision.is_(None))
        .values(decision=data.approved, decided_by=user.id)
    ).rowcount
    if not updated:
        db.rollback()
        existing = db.get(Approval, approval.id)
        if existing.decision != data.approved:
            raise HTTPException(409, "Approval already decided")
        return {"id": run.id, "status": run.status}
    audit(db, user, "approve" if data.approved else "refuse", "tool_call", call.id)
    pending = db.scalar(
        select(Approval.id).where(Approval.run_id == run.id, Approval.decision.is_(None)).limit(1)
    )
    enqueue = not pending
    if enqueue:
        run.status = "resume_queued"
    db.commit()
    if enqueue:
        dispatch(request.app.state, run.id, resume=True)
    return {"id": run.id, "status": run.status}


@router.get("/admin/tool-calls")
def calls(db: DB, admin: Admin, status: str = "outcome_unknown"):
    rows = db.scalars(
        select(ToolCall)
        .where(ToolCall.status == status)
        .order_by(ToolCall.created_at.desc())
        .limit(500)
    )
    return [
        {
            "id": c.id,
            "run_id": c.run_id,
            "tool_id": c.tool_id,
            "title": call_config(db.get(Run, c.run_id), c)["title"],
            "tool_name": call_config(db.get(Run, c.run_id), c)["name"],
            "status": c.status,
            "effect": c.effect,
            "policy": c.policy,
            "created_at": c.created_at,
            "resolved_by": c.resolved_by,
        }
        for c in rows
    ]


@router.post("/admin/tool-calls/{identifier}/resolve")
def resolve(identifier: UUID, data: ResolveInput, db: DB, admin: Admin):
    call = db.get(ToolCall, str(identifier))
    if call:
        run = lock_run(db, call.run_id, worker=False)
        db.refresh(call)
    if not call or call.status != "outcome_unknown":
        raise HTTPException(409, "Tool call is not awaiting verification")
    changed = db.execute(
        update(ToolCall)
        .where(ToolCall.id == call.id, ToolCall.status == "outcome_unknown")
        .values(status=data.outcome, resolution_note=data.note, resolved_by=admin.id)
    ).rowcount
    if not changed:
        raise HTTPException(409, "Tool call is already resolved")
    run = db.get(Run, call.run_id)
    other = db.scalar(
        select(ToolCall.id)
        .where(ToolCall.run_id == run.id, ToolCall.status == "outcome_unknown")
        .limit(1)
    )
    if not other:
        message = "An administrator verified the pending tool operation. Start a new request for any follow-up."
        db.add(
            Message(
                conversation_id=run.conversation_id,
                run_id=run.id,
                role="assistant",
                content=message,
            )
        )
        event(db, run.id, "result", {"message": message})
        finish(db, run, "completed")
    audit(db, admin, "resolve_" + data.outcome, "tool_call", call.id)
    db.commit()
    return {"id": call.id, "status": data.outcome}


def validate_tools(db, ids):
    found = set(
        db.scalars(
            select(ToolDefinition.id).where(
                ToolDefinition.id.in_(ids), ToolDefinition.enabled.is_(True)
            )
        )
    )
    if set(ids) != found:
        raise HTTPException(422, "Tool reference is unavailable")


def cleanup_group(db, group_id):
    for tool in db.scalars(select(ToolDefinition)):
        if group_id in tool.allowed_group_ids:
            tool.allowed_group_ids = [gid for gid in tool.allowed_group_ids if gid != group_id]
