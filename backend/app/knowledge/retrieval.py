"""Database-scoped cosine retrieval. PostgreSQL scores pgvector; SQLite is dev-only."""

import math

from fastapi import HTTPException
from pgvector.sqlalchemy import Vector
from sqlalchemy import case, cast, func, select

from app.knowledge.embedding import EmbeddingFailure, binding, embed
from app.knowledge.models import Chunk, Document, KnowledgeBase
from app.models import ModelConnection


def configuration(db, kb):
    model = db.get(ModelConnection, kb.embedding_model_id)
    if not model or not model.enabled or model.kind != "embedding":
        raise HTTPException(409, "Embedding model is disabled or unavailable")
    config = binding(model)
    if kb.embedding_fingerprint and kb.embedding_fingerprint != config["fingerprint"]:
        raise HTTPException(409, "Embedding space changed; reindex knowledge documents")
    return config


def matching_chunks(db, kid, fingerprint, vector):
    statement = (
        select(Chunk, Document)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Document.knowledge_base_id == kid,
            Document.status == "ready",
            Document.embedding_fingerprint == fingerprint,
            Chunk.revision == Document.revision,
            Chunk.vector.is_not(None),
        )
    )
    if db.bind.dialect.name == "postgresql":
        column = cast(Chunk.vector, Vector())
        # CASE guards evaluation even when PostgreSQL pushes scoring below the KB join.
        distance = case(
            (func.vector_dims(column) == len(vector), column.cosine_distance(vector)), else_=2
        )
        return db.execute(statement.where(distance <= 0.8).order_by(distance).limit(5)).all()

    def distance(row):
        values = row[0].vector
        if len(values) != len(vector):
            return 2
        return 1 - sum(a * b for a, b in zip(values, vector, strict=True)) / (
            math.sqrt(sum(v * v for v in values)) * math.sqrt(sum(v * v for v in vector))
        )

    rows = db.execute(statement).all()
    return sorted([row for row in rows if distance(row) <= 0.8], key=distance)[:5]


def search(state, db, kid, query, frozen=None):
    kb = db.get(KnowledgeBase, kid)
    if not kb:
        raise HTTPException(404, "Knowledge base not found")
    current = configuration(db, kb)
    config = frozen or current
    if config.get("fingerprint") != current["fingerprint"] or config.get("id") != current["id"]:
        raise HTTPException(409, "Embedding configuration changed; start a new request")
    dimension = kb.embedding_dimensions
    if not dimension:
        return []
    db.commit()  # No Run/KB lock or open transaction spans external embedding I/O.
    try:
        vector = embed(state, config, [query], dimension)[0]
    except EmbeddingFailure as exc:
        raise HTTPException(503, str(exc)) from None
    db.expire_all()
    kb = db.get(KnowledgeBase, kid)
    if not kb or configuration(db, kb)["fingerprint"] != config["fingerprint"]:
        raise HTTPException(409, "Knowledge configuration is no longer available")
    return matching_chunks(db, kid, config["fingerprint"], vector)
