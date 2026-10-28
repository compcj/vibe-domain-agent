"""Flatten source provenance of all assistant history consumed by each run."""

from fastapi import HTTPException
from sqlalchemy import select

from app.knowledge.models import Citation, RunSource


def inherited_references(db, run_id):
    return db.scalars(select(RunSource).where(RunSource.run_id == run_id)).all()


def validate_inherited(db, run, user):
    from app.knowledge.access import citation_allowed

    for source in inherited_references(db, run.id):
        citation = db.get(Citation, source.citation_id) if source.citation_id else None
        if not citation_allowed(db, citation, user):
            raise HTTPException(403, "Reference material is no longer available")


def inherit_history(db, run, user, history):
    from app.knowledge.access import message_content, references_hidden

    messages = []
    ids = set()
    for message in history:
        content = message_content(db, user, message)
        messages.append({"role": message.role, "content": content})
        if message.role != "assistant" or references_hidden(db, user, message.run_id):
            continue
        ids.update(db.scalars(select(Citation.id).where(Citation.run_id == message.run_id)))
        ids.update(source.citation_id for source in inherited_references(db, message.run_id))
    existing = {source.citation_id for source in inherited_references(db, run.id)}
    for citation_id in ids - existing:
        db.add(RunSource(run_id=run.id, citation_id=citation_id))
    db.flush()
    validate_inherited(db, run, user)
    return messages
