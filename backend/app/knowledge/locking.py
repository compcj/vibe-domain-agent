"""Shared locks for reference creation and knowledge-base deletion."""

from fastapi import HTTPException
from sqlalchemy import select, update

from app.knowledge.models import KnowledgeBase


def lock_kb(db, identifier, *, missing_status=404):
    with db.no_autoflush:
        if not db.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.id == str(identifier))
            .values(name=KnowledgeBase.name)
        ).rowcount:
            raise HTTPException(missing_status, "Knowledge base is unavailable")
        return db.scalar(
            select(KnowledgeBase)
            .where(KnowledgeBase.id == str(identifier))
            .execution_options(populate_existing=True)
        )


def lock_references(db, ids):
    # All reference writers take KB locks in the same order through commit.
    for identifier in sorted(set(map(str, ids))):
        lock_kb(db, identifier, missing_status=422)
