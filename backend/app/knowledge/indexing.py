"""Durable at-least-once indexing with revision and claim fencing."""

import time
from datetime import timedelta

from sqlalchemy import delete, select, update

from app.knowledge.embedding import EmbeddingFailure, binding, embed
from app.knowledge.models import Chunk, Document, KnowledgeBase
from app.knowledge.parsing import ParseFailure, parse_document
from app.knowledge.storage import path_for
from app.models import ModelConnection, new_id, utcnow
from app.runtime import budget


def dispatch(state, identifier):
    if getattr(state, "dispatch_index", None):
        state.dispatch_index(identifier)
    else:
        from app.knowledge.tasks import index_document_task

        try:
            index_document_task.delay(identifier)
        except Exception:
            pass  # Committed queued documents are a durable outbox, replayed by beat.


def index_document(state, identifier):
    deadline_token = budget.deadline.set(time.monotonic() + 300)
    try:
        return _index_document(state, identifier)
    finally:
        budget.deadline.reset(deadline_token)


def _index_document(state, identifier):
    token = new_id()
    with state.session_factory() as db:
        changed = db.execute(
            update(Document)
            .where(Document.id == identifier, Document.status == "queued")
            .values(
                status="processing",
                claim_token=token,
                claim_expires_at=utcnow() + timedelta(seconds=360),
            )
        ).rowcount
        db.commit()
        if not changed:
            return
        doc = db.get(Document, identifier)
        if not doc or doc.claim_token != token:
            return
        revision, name, storage_key, kid = (
            doc.revision,
            doc.name,
            doc.storage_key,
            doc.knowledge_base_id,
        )
        config, dimension = None, None
        if kid:
            kb = db.get(KnowledgeBase, kid)
            if not kb:
                return
            model = db.get(ModelConnection, kb.embedding_model_id)
            if model and model.enabled and model.kind == "embedding":
                config = binding(model)
                dimension = kb.embedding_dimensions
        db.commit()
    try:
        if kid and config is None:
            raise ParseFailure("Embedding model is disabled or unavailable")
        pieces = parse_document(name, path_for(state, storage_key).read_bytes())
        vectors = []
        if config:
            with state.session_factory() as db:
                db.execute(
                    update(Document)
                    .where(
                        Document.id == identifier,
                        Document.claim_token == token,
                        Document.revision == revision,
                    )
                    .values(status="indexing")
                )
                db.commit()
            for start in range(0, len(pieces), 32):
                with state.session_factory() as db:
                    current = db.get(ModelConnection, config["id"])
                    document = db.get(Document, identifier)
                    if (
                        not document
                        or document.claim_token != token
                        or document.revision != revision
                    ):
                        return
                    if (
                        not current
                        or not current.enabled
                        or current.kind != "embedding"
                        or binding(current)["fingerprint"] != config["fingerprint"]
                    ):
                        raise ParseFailure("Embedding model is disabled or changed")
                vectors.extend(
                    embed(
                        state, config, [text for text, _ in pieces[start : start + 32]], dimension
                    )
                )
                dimension = dimension or len(vectors[0])
        else:
            vectors = [None] * len(pieces)
        with state.session_factory() as db:
            # Lock KB before Document, matching upload/delete and preventing model-space races.
            kb = None
            if kid:
                db.execute(
                    update(KnowledgeBase)
                    .where(KnowledgeBase.id == kid)
                    .values(name=KnowledgeBase.name)
                )
                kb = db.get(KnowledgeBase, kid)
                if not kb:
                    return
            changed = db.execute(
                update(Document)
                .where(
                    Document.id == identifier,
                    Document.claim_token == token,
                    Document.revision == revision,
                    Document.claim_expires_at > utcnow(),
                    Document.status.in_(["processing", "indexing"]),
                )
                .values(status="ready", claim_token=None, claim_expires_at=None)
            ).rowcount
            if not changed:
                return
            if kb:
                model = db.get(ModelConnection, kb.embedding_model_id)
                if (
                    not model
                    or not model.enabled
                    or model.kind != "embedding"
                    or binding(model)["fingerprint"] != config["fingerprint"]
                    or model.id != config["id"]
                ):
                    raise ParseFailure("Embedding configuration changed; retry indexing")
                if kb.embedding_fingerprint and (
                    kb.embedding_fingerprint != config["fingerprint"]
                    or kb.embedding_dimensions != dimension
                ):
                    raise ParseFailure(
                        "Embedding space changed; remove indexed documents before reindexing"
                    )
                kb.embedding_fingerprint, kb.embedding_dimensions = config["fingerprint"], dimension
            db.execute(delete(Chunk).where(Chunk.document_id == identifier))
            for (text, locator), vector in zip(pieces, vectors, strict=True):
                db.add(
                    Chunk(
                        document_id=identifier,
                        revision=revision,
                        text=text,
                        locator=locator[:500],
                        vector=vector,
                    )
                )
            doc = db.get(Document, identifier)
            doc.error = None
            doc.embedding_fingerprint = config["fingerprint"] if config else None
            db.commit()
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, (ParseFailure, EmbeddingFailure))
            else "Document indexing failed safely"
        )
        with state.session_factory() as db:
            db.execute(
                update(Document)
                .where(
                    Document.id == identifier,
                    Document.claim_token == token,
                    Document.revision == revision,
                )
                .values(
                    status="failed", error=reason[:200], claim_token=None, claim_expires_at=None
                )
            )
            db.commit()


def recover_indexing(state):
    with state.session_factory() as db:
        db.execute(
            update(Document)
            .where(
                Document.status.in_(["processing", "indexing"]),
                Document.claim_expires_at < utcnow(),
            )
            .values(status="queued", claim_token=None, claim_expires_at=None)
        )
        ids = list(db.scalars(select(Document.id).where(Document.status == "queued")))
        db.commit()
    for identifier in ids:
        dispatch(state, identifier)
    return len(ids)
