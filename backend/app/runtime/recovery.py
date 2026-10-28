"""Recovery is conservative whenever a write may have crossed the network boundary."""

from datetime import timedelta

from sqlalchemy import delete, select, update

from app.models import Approval, AuditEvent, Run, RunEvent, ToolCall, utcnow
from app.runtime.store import finish


def recover(state):
    enqueue = []
    with state.session_factory() as db:
        now = utcnow()
        expired = db.scalars(
            select(Run).where(
                Run.status == "awaiting_confirmation",
                Run.id.in_(
                    select(Approval.run_id).where(
                        Approval.expires_at <= now, Approval.decision.is_(None)
                    )
                ),
            )
        ).all()
        for run in expired:
            db.execute(
                update(ToolCall)
                .where(ToolCall.run_id == run.id, ToolCall.status == "planned")
                .values(status="expired")
            )
            finish(db, run, "expired", "Confirmation expired")
        stale = db.scalars(
            select(Run)
            .where(
                Run.status.in_(["running", "resume_queued", "awaiting_confirmation"]),
                Run.lease_expires_at <= now,
            )
            .with_for_update(skip_locked=True)
        ).all()
        for run in stale:
            if run.status == "awaiting_confirmation":
                run.lease_token = None
                run.lease_expires_at = None
                continue
            submitted = db.scalars(
                select(ToolCall).where(ToolCall.run_id == run.id, ToolCall.status == "submitted")
            ).all()
            uncertain = False
            for call in submitted:
                if call.effect == "write":
                    call.status = "outcome_unknown"
                    uncertain = True
                else:
                    call.status, call.result = "failed", "Read interrupted; no result available."
            if uncertain:
                finish(
                    db, run, "outcome_unknown", "Tool outcome requires administrator verification"
                )
            elif run.cancel_requested:
                finish(db, run, "cancelled")
            else:
                # Time spent before a worker crash counts against the active budget.
                run.elapsed_seconds += 90
                run.status = "resume_queued"
                run.lease_token = None
                run.lease_expires_at = None
        db.commit()
        # A DB run is a durable outbox. Redis dispatch outages are recovered here.
        enqueue = [
            (run.id, run.status == "resume_queued")
            for run in db.scalars(select(Run).where(Run.status.in_(["queued", "resume_queued"])))
        ]
    for run_id, resume in enqueue:
        from app.routers.conversations import dispatch

        dispatch(state, run_id, resume)
    return {"queued": len(enqueue), "expired": len(expired), "recovered": len(stale)}


def cleanup(state):
    """Retain chat/ledger by explicit user deletion; prune logs/checkpoints at 30d, audit at 180d."""
    from app.runtime.checkpoints import delete_checkpoints

    with state.session_factory() as db:
        ids = list(
            db.scalars(
                select(Run.id).where(
                    Run.finished_at < utcnow() - timedelta(days=30), Run.status != "outcome_unknown"
                )
            )
        )
        delete_checkpoints(state, ids)
        if ids:
            db.execute(delete(RunEvent).where(RunEvent.run_id.in_(ids)))
        db.execute(delete(AuditEvent).where(AuditEvent.created_at < utcnow() - timedelta(days=180)))
        db.commit()
    return {"runs_pruned": len(ids)}
