"""Frozen call planning, durable approval interrupts and side-effect ledger."""

import hashlib
import json
from datetime import timedelta

from fastapi import HTTPException
from jsonschema import Draft202012Validator
from langgraph.types import interrupt
from sqlalchemy import select, update

from app.models import Approval, ToolCall, User, new_id, utcnow
from app.runtime import budget
from app.runtime.provider import SafeFailure
from app.runtime.store import aware, event, finish, live_access, lock_run
from app.runtime.transports import TransportFailure, invoke
from app.security import audit, decrypt_secret


def arguments_hash(arguments):
    return hashlib.sha256(
        json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def call_config(run, call):
    return next(t for t in run.snapshot["tools"] if t["id"] == call.tool_id)


def validate_frozen(call):
    if arguments_hash(call.arguments) != call.arguments_hash:
        raise HTTPException(409, "Tool parameters have changed")


def plan_calls(state, run_id, calls):
    with state.session_factory() as db:
        run = lock_run(db, run_id)
        live_access(db, run)
        for raw in calls:
            try:
                config = next(
                    t for t in run.snapshot["tools"] if t["name"] == raw["function"]["name"]
                )
                arguments = json.loads(raw["function"]["arguments"])
                if not isinstance(arguments, dict) or not raw["id"] or len(raw["id"]) > 200:
                    raise ValueError()
                arguments = {**arguments, **config["fixed_arguments"]}
                Draft202012Validator(config["input_schema"]).validate(arguments)
            except Exception:
                raise SafeFailure("Model requested an invalid or unauthorized tool") from None
            live_access(db, run, config["id"])
            digest = arguments_hash(arguments)
            existing = db.scalar(
                select(ToolCall).where(ToolCall.run_id == run_id, ToolCall.call_key == raw["id"])
            )
            if existing:
                validate_frozen(existing)
                if existing.arguments_hash != digest or existing.tool_id != config["id"]:
                    raise SafeFailure("Tool call identifier was reused with different parameters")
                continue
            call = ToolCall(
                run_id=run_id,
                tool_id=config["id"],
                call_key=raw["id"],
                arguments=arguments,
                arguments_hash=digest,
                idempotency_key=new_id(),
                effect=config["effect"],
                policy=config["policy"],
            )
            db.add(call)
            db.flush()
            if config["policy"] == "confirm":
                approval = Approval(
                    run_id=run_id,
                    tool_call_id=call.id,
                    arguments_hash=digest,
                    expires_at=utcnow() + timedelta(minutes=10),
                )
                db.add(approval)
                db.flush()
                event(db, run_id, "approval", approval_payload(config, call, approval))
        db.commit()


def approval_payload(config, call, approval):
    return {
        "id": approval.id,
        "title": config["title"],
        "summary": f"{config['effect'].title()} operation: {config['name']}",
        "parameters": call.arguments,
        "expires_at": approval.expires_at.isoformat(),
    }


def approval_gate(state, run_id, calls):
    # This node is replayed on Command(resume=...). Never perform side effects here.
    with state.session_factory() as db:
        run = lock_run(db, run_id)
        live_access(db, run)
        approvals = db.scalars(select(Approval).where(Approval.run_id == run_id)).all()
        pending = False
        for approval in approvals:
            call = db.get(ToolCall, approval.tool_call_id)
            if call.call_key not in {raw["id"] for raw in calls}:
                continue
            validate_frozen(call)
            if approval.arguments_hash != call.arguments_hash:
                raise SafeFailure("Approval parameters have changed")
            if aware(approval.expires_at) <= utcnow():
                finish(db, run, "expired", "Confirmation expired")
                call.status = "expired"
                db.commit()
                raise SafeFailure("Confirmation expired")
            pending |= approval.decision is None
        if pending:
            run.status = "awaiting_confirmation"
            event(
                db,
                run_id,
                "status",
                {"status": "awaiting_confirmation", "message": "Confirmation required"},
            )
            db.commit()
    if pending:
        interrupt({"confirmation_required": True})


def execute_calls(state, run_id, calls):
    results = []
    for raw in calls:
        budget.heartbeat(state, run_id)
        budget.check()
        with state.session_factory() as db:
            run = lock_run(db, run_id)
            if run.cancel_requested:
                raise SafeFailure("Run cancelled")
            call = db.scalar(
                select(ToolCall).where(ToolCall.run_id == run_id, ToolCall.call_key == raw["id"])
            )
            live_access(db, run, call.tool_id)
            validate_frozen(call)
            config = call_config(run, call)
            if call.status in {"succeeded", "failed", "refused"}:
                results.append(
                    {
                        "role": "tool",
                        "tool_call_id": raw["id"],
                        "content": call.result or "Tool was not executed",
                    }
                )
                continue
            if call.status in {"submitted", "outcome_unknown"}:
                call.status = "outcome_unknown"
                finish(
                    db, run, "outcome_unknown", "Tool outcome requires administrator verification"
                )
                db.commit()
                raise SafeFailure("Tool outcome requires administrator verification")
            if call.policy == "confirm":
                approval = db.scalar(select(Approval).where(Approval.tool_call_id == call.id))
                if (
                    not approval
                    or approval.arguments_hash != call.arguments_hash
                    or aware(approval.expires_at) <= utcnow()
                ):
                    raise SafeFailure("Confirmation is invalid or expired")
                if approval.decision is False:
                    call.status, call.result = (
                        "refused",
                        "The user declined this operation. Do not repeat it.",
                    )
                    db.commit()
                    results.append(
                        {"role": "tool", "tool_call_id": raw["id"], "content": call.result}
                    )
                    continue
                if approval.decision is not True:
                    raise SafeFailure("Confirmation is required")
            if "headers_encrypted" not in config:
                raise SafeFailure("Run credential binding is unavailable; start a new request")
            headers = (
                json.loads(decrypt_secret(config["headers_encrypted"], state.settings))
                if config["headers_encrypted"]
                else {}
            )
            claimed = db.execute(
                update(ToolCall)
                .where(ToolCall.id == call.id, ToolCall.status == "planned")
                .values(status="submitted")
            ).rowcount
            if not claimed:
                raise SafeFailure("Tool call is already executing")
            audit(db, db.get(User, run.owner_id), "tool_submitted", "tool_call", call.id)
            db.commit()  # Submission is durable before ANY network side effect.
            call_id, arguments, key, effect = (
                call.id,
                call.arguments,
                call.idempotency_key,
                call.effect,
            )
        try:
            bounded_config = {
                **config,
                "timeout_seconds": min(config["timeout_seconds"], max(1, budget.remaining())),
            }
            result = invoke(state, bounded_config, arguments, headers, key)
            status = "succeeded"
        except TransportFailure:
            status = "outcome_unknown" if effect == "write" else "failed"
            result = (
                "Tool outcome requires administrator verification"
                if effect == "write"
                else "Tool service unavailable; you may answer without this result."
            )
        with state.session_factory() as db:
            run = lock_run(db, run_id)
            call = db.get(ToolCall, call_id)
            if call.status != "submitted" or call.resolved_by:
                raise SafeFailure("Tool call is no longer owned by this execution")
            call.status, call.result = status, result
            audit(db, None, "tool_" + status, "tool_call", call_id)
            if status == "outcome_unknown":
                finish(db, run, "outcome_unknown", result)
            db.commit()
        if status == "outcome_unknown":
            raise SafeFailure(result)
        results.append({"role": "tool", "tool_call_id": raw["id"], "content": result})
    return results
