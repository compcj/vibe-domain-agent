"""Knowledge and owner attachments; private storage keys never cross DTO boundaries."""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, IdentityMixin


class KnowledgeBase(IdentityMixin, Base):
    __tablename__ = "knowledge_bases"
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    embedding_model_id: Mapped[str] = mapped_column(ForeignKey("model_connections.id"))
    embedding_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Document(IdentityMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("knowledge_base_id", "name", name="uq_kb_document_name"),)
    knowledge_base_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=True, index=True
    )
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    storage_key: Mapped[str] = mapped_column(String(100))
    checksum: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    embedding_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Chunk(IdentityMixin, Base):
    __tablename__ = "document_chunks"
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    locator: Mapped[str] = mapped_column(String(500))
    vector: Mapped[list | None] = mapped_column(
        JSON().with_variant(Vector(), "postgresql"), nullable=True
    )


class Citation(IdentityMixin, Base):
    __tablename__ = "citations"
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    locator: Mapped[str] = mapped_column(String(500))
    excerpt: Mapped[str] = mapped_column(Text)


class RunSource(IdentityMixin, Base):
    __tablename__ = "run_sources"
    __table_args__ = (UniqueConstraint("run_id", "citation_id", name="uq_run_source"),)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    citation_id: Mapped[str | None] = mapped_column(
        ForeignKey("citations.id", ondelete="SET NULL"), nullable=True
    )


class FileCleanup(IdentityMixin, Base):
    __tablename__ = "file_cleanup"
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    storage_key: Mapped[str] = mapped_column(String(100), unique=True)
