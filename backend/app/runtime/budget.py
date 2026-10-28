"""Per-execution context, never shared across concurrent graph invocations."""

import time
from contextvars import ContextVar

deadline = ContextVar("run_deadline", default=None)
lease_token = ContextVar("run_lease_token", default=None)


def remaining():
    limit = deadline.get()
    return max(0, limit - time.monotonic()) if limit is not None else 300


def check():
    if remaining() <= 0:
        from app.runtime.provider import SafeFailure

        raise SafeFailure("Execution budget exhausted")


accounted_at = ContextVar("run_accounted_at", default=None)


def heartbeat(state, run_id, force=False):
    """Persist active time and renew the DB fence; waiting at interrupts is excluded."""
    from datetime import timedelta

    from sqlalchemy import update

    from app.models import Run, utcnow

    token, previous, now = lease_token.get(), accounted_at.get(), time.monotonic()
    if not token or previous is None or (not force and now - previous < 5):
        return
    with state.session_factory() as db:
        changed = db.execute(
            update(Run)
            .where(Run.id == run_id, Run.lease_token == token)
            .values(
                elapsed_seconds=Run.elapsed_seconds + now - previous,
                lease_expires_at=utcnow() + timedelta(seconds=90),
            )
        ).rowcount
        db.commit()
    if not changed:
        from app.runtime.provider import SafeFailure

        raise SafeFailure("Execution lease is no longer valid")
    accounted_at.set(now)
