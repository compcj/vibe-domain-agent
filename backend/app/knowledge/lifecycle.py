"""Durable, idempotent file cleanup; a ledger row also fences each new upload."""

from sqlalchemy import event, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.knowledge.models import Document, FileCleanup
from app.knowledge.storage import remove_file
from app.models import utcnow


def enqueue(db, state, key):
    if not db.scalar(select(FileCleanup.id).where(FileCleanup.storage_key == key)):
        db.add(FileCleanup(storage_key=key))
    db.info["file_cleanup_state"] = state
    db.info.setdefault("file_cleanup_keys", set()).add(key)


@event.listens_for(Session, "after_commit")
def after_commit(db):
    keys = db.info.pop("file_cleanup_keys", set())
    state = db.info.pop("file_cleanup_state", None)
    if state and keys:
        try:
            cleanup_files(state, keys=keys)
        except (OSError, SQLAlchemyError):
            # Committed intents remain for the scheduled worker, including DB outages.
            pass


@event.listens_for(Session, "after_rollback")
def after_rollback(db):
    db.info.pop("file_cleanup_keys", None)
    db.info.pop("file_cleanup_state", None)


def cleanup_files(state, *, keys=None, limit=100):
    removed = 0
    with state.session_factory() as db:
        query = (
            select(FileCleanup.id)
            .order_by(
                func.coalesce(FileCleanup.attempted_at, FileCleanup.created_at), FileCleanup.id
            )
            .limit(limit)
        )
        if keys is not None:
            query = query.where(FileCleanup.storage_key.in_(keys))
        ids = list(db.scalars(query))
    for identifier in ids:
        with state.session_factory() as db:
            if db.bind.dialect.name == "postgresql":
                row = db.scalar(
                    select(FileCleanup)
                    .where(FileCleanup.id == identifier)
                    .with_for_update(skip_locked=True)
                )
            else:
                changed = db.execute(
                    update(FileCleanup)
                    .where(FileCleanup.id == identifier)
                    .values(storage_key=FileCleanup.storage_key)
                ).rowcount
                row = db.get(FileCleanup, identifier) if changed else None
            if not row:
                continue
            # Never unlink a live reference even if an obsolete/stale intent is delivered.
            if db.scalar(select(Document.id).where(Document.storage_key == row.storage_key)):
                db.delete(row)
                db.commit()
                continue
            try:
                remove_file(state, row.storage_key)
            except OSError:
                # Rotate failures behind untouched work, without losing the intent.
                row.attempted_at = utcnow()
                db.commit()
                continue
            db.delete(row)
            db.commit()
            removed += 1
    return {"removed": removed}
