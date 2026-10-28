"""durable file intents and transitive run source provenance"""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "3b9e2f8174ac"
down_revision = "0670a71142ff"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "file_cleanup",
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("storage_key", sa.String(100), nullable=False),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_table(
        "run_sources",
        sa.Column(
            "run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "citation_id",
            sa.String(36),
            sa.ForeignKey("citations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "citation_id", name="uq_run_source"),
    )
    op.create_index("ix_run_sources_run_id", "run_sources", ["run_id"])
    # Older runs have no explicit history provenance. Conservatively inherit earlier
    # citations in their conversation, including tombstones and paused checkpoints.
    connection = op.get_bind()
    pairs = (
        connection.execute(
            sa.text(
                "SELECT r.id AS run_id, c.id AS citation_id, r.created_at "
                "FROM runs r JOIN citations c ON c.conversation_id = r.conversation_id "
                "WHERE c.run_id != r.id AND c.created_at < r.created_at"
            )
        )
        .mappings()
        .all()
    )
    if pairs:
        table = sa.table(
            "run_sources",
            sa.column("id"),
            sa.column("run_id"),
            sa.column("citation_id"),
            sa.column("created_at"),
        )
        connection.execute(table.insert(), [{**dict(pair), "id": str(uuid4())} for pair in pairs])


def downgrade():
    op.drop_index("ix_run_sources_run_id", table_name="run_sources")
    op.drop_table("run_sources")
    op.drop_table("file_cleanup")
