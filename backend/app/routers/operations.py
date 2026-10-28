from fastapi import APIRouter
from sqlalchemy import func, select

from app.dependencies import DB, Admin
from app.models import Agent, AuditEvent, Feedback, Group, ModelConnection, Run, ToolCall, User
from app.schemas import AuditRead

router = APIRouter(prefix="/admin", tags=["operations"])


@router.get("/audit", response_model=list[AuditRead])
def audit_events(db: DB, admin: Admin):
    return db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(500)).all()


@router.get("/metrics")
def metrics(db: DB, admin: Admin):
    result = {
        name: db.scalar(select(func.count()).select_from(model))
        for name, model in {
            "users": User,
            "groups": Group,
            "agents": Agent,
            "models": ModelConnection,
            "audit_events": AuditEvent,
            "runs": Run,
            "tool_calls": ToolCall,
            "feedback": Feedback,
        }.items()
    }

    result["run_status_counts"] = dict(
        db.execute(select(Run.status, func.count()).group_by(Run.status)).all()
    )
    result["tool_status_counts"] = dict(
        db.execute(select(ToolCall.status, func.count()).group_by(ToolCall.status)).all()
    )
    result["input_tokens"] = db.scalar(select(func.sum(Run.input_tokens)))
    result["output_tokens"] = db.scalar(select(func.sum(Run.output_tokens)))
    result["average_active_seconds"] = db.scalar(
        select(func.avg(Run.elapsed_seconds)).where(Run.finished_at.is_not(None))
    )
    result["feedback_ratings"] = dict(
        db.execute(select(Feedback.rating, func.count()).group_by(Feedback.rating)).all()
    )
    terminal_count = sum(
        result["run_status_counts"].get(status, 0)
        for status in ("completed", "failed", "cancelled", "expired", "outcome_unknown")
    )
    result["error_rate"] = (
        (
            (
                result["run_status_counts"].get("failed", 0)
                + result["run_status_counts"].get("outcome_unknown", 0)
            )
            / terminal_count
        )
        if terminal_count
        else None
    )
    result["total_runs"] = result["runs"]
    result["completed_runs"] = result["run_status_counts"].get("completed", 0)
    result["failed_runs"] = result["run_status_counts"].get("failed", 0)
    result["active_runs"] = sum(
        result["run_status_counts"].get(key, 0)
        for key in ("queued", "running", "resume_queued", "awaiting_confirmation")
    )
    return result
