"""Recovery is conservative whenever a write may have crossed the network boundary."""

from datetime import timedelta

from sqlalchemy import delete, or_, select, update

from app.models import Approval, AuditEvent, Run, RunEvent, ToolCall, utcnow
from app.runtime.store import aware, finish, lock_run


def recover(state):
    enqueue = []
    expired, stale = [], []
    with state.session_factory() as db:
        now = utcnow()
        candidates = list(
            db.scalars(
                select(Run.id)
                .where(
                    or_(
                        Run.status.in_(["running", "resume_queued", "awaiting_confirmation"]),
                        Run.id.in_(select(ToolCall.run_id).where(ToolCall.status == "submitted")),
                    )
                )
                .order_by(Run.id)
            )
        )
        for run_id in candidates:
            run = lock_run(db, run_id, worker=False)
            # Re-read under the same lock used by claim, cancellation and results.
            lease_alive = run.lease_expires_at and aware(run.lease_expires_at) > now
            if lease_alive:
                continue
            submitted = db.scalars(
                select(ToolCall).where(ToolCall.run_id == run.id, ToolCall.status == "submitted")
            ).all()
            uncertain = any(call.effect == "write" for call in submitted)
            for call in submitted:
                call.status = "outcome_unknown" if call.effect == "write" else "failed"
                if call.effect != "write":
                    call.result = "Read interrupted; no result available."
            if uncertain:
                stale.append(run.id)
                finish(
                    db, run, "outcome_unknown", "Tool outcome requires administrator verification"
                )
                continue
            expired_approval = db.scalar(
                select(Approval.id)
                .where(
                    Approval.run_id == run.id,
                    Approval.expires_at <= now,
                    Approval.decision.is_(None),
                )
                .limit(1)
            )
            if run.status == "awaiting_confirmation" and expired_approval:
                expired.append(run.id)
                db.execute(
                    update(ToolCall)
                    .where(ToolCall.run_id == run.id, ToolCall.status == "planned")
                    .values(status="expired")
                )
                finish(db, run, "expired", "Confirmation expired")
            elif run.lease_expires_at and run.status in {
                "running",
                "resume_queued",
                "awaiting_confirmation",
            }:
                stale.append(run.id)
                if run.cancel_requested:
                    finish(db, run, "cancelled")
                else:
                    if run.status != "awaiting_confirmation":
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
