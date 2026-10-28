import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect


def test_migration_upgrade_downgrade_matches_metadata(tmp_path):
    from app.db import make_engine
    from app.models import Base

    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "backend/migrations"))
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = make_engine(url)
    assert set(Base.metadata.tables).issubset(inspect(engine).get_table_names())
    command.check(config)
    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()


def test_migration_check_preserves_framework_checkpoint_tables(tmp_path):
    from app.db import make_engine
    from sqlalchemy import text

    config = Config("alembic.ini")
    url = f"sqlite:///{tmp_path / 'framework.db'}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = make_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE checkpoint_migrations (v INTEGER PRIMARY KEY)"))
    command.check(config)
    engine.dispose()


def test_provenance_backfill_bounds_fetches_and_inserts_without_losing_pairs():
    import importlib.util
    import sqlite3

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, event, text

    class BoundedCursor(sqlite3.Cursor):
        def fetchall(self):
            if self.description and self.description[0][0] == "run_id":
                raise AssertionError("Provenance backfill must not materialize every pair")
            return super().fetchall()

        def fetchmany(self, size=1):
            assert 0 < size <= 500, "Backfill fetch exceeds memory budget"
            return super().fetchmany(size)

    class BoundedConnection(sqlite3.Connection):
        def cursor(self, *args, **kwargs):
            return super().cursor(*args, factory=BoundedCursor, **kwargs)

    engine = create_engine(
        "sqlite://",
        creator=lambda: sqlite3.connect(
            ":memory:",
            factory=BoundedConnection,
        ),
    )
    inserts = []

    @event.listens_for(engine, "before_cursor_execute")
    def check_batch(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO run_sources"):
            count = len(parameters) if executemany else 1
            assert count <= 500, "Backfill insert exceeds memory budget"
            inserts.append(count)

    path = Path(__file__).resolve().parents[1] / "migrations/versions"
    spec = importlib.util.spec_from_file_location(
        "backfill",
        path / "3b9e2f8174ac_file_cleanup_and_source_provenance.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE runs (id TEXT PRIMARY KEY, conversation_id TEXT, created_at TEXT)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE citations (id TEXT PRIMARY KEY, run_id TEXT, "
                    "conversation_id TEXT, created_at TEXT)"
                )
            )
            connection.execute(text("INSERT INTO runs VALUES ('target', 'chat', '2026-02-01')"))
            citations = [
                dict(id=f"old-{i}", run="origin", chat="chat", time="2026-01-01")
                for i in range(1201)
            ]
            citations += [
                dict(id="own", run="target", chat="chat", time="2026-01-01"),
                dict(id="same-time", run="origin", chat="chat", time="2026-02-01"),
                dict(id="future", run="origin", chat="chat", time="2026-03-01"),
                dict(id="other-chat", run="origin", chat="other", time="2026-01-01"),
            ]
            connection.execute(
                text("INSERT INTO citations VALUES (:id, :run, :chat, :time)"), citations
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            actual = connection.execute(text("SELECT citation_id, created_at FROM run_sources"))
            assert set(actual) == {(f"old-{i}", "2026-02-01") for i in range(1201)}
            assert sum(inserts) == 1201 and len(inserts) >= 3
    finally:
        engine.dispose()


@pytest.mark.parametrize("database", ["sqlite", "postgres"])
def test_populated_provenance_upgrade_across_batches(tmp_path, database):
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.db import make_engine
    from app.knowledge.models import RunSource
    from app.models import Agent, Citation, Conversation, Run, User
    from sqlalchemy import event, select, text
    from sqlalchemy.engine import Engine, make_url
    from sqlalchemy.orm import Session
    from sqlalchemy.schema import CreateSchema, DropSchema

    if database == "postgres" and not os.getenv("TEST_POSTGRES_URL"):
        pytest.skip("TEST_POSTGRES_URL is not configured")
    base = engine = None
    schema = None
    url = None
    streamed_cursors = []

    def observe_backfill(connection, cursor, statement, parameters, context, executemany):
        # Alembic creates its own engine, so observe only this test's isolated URL.
        if connection.engine.url == url and statement.startswith("SELECT r.id AS run_id"):
            streamed_cursors.append(getattr(cursor, "name", None))

    event.listen(Engine, "before_cursor_execute", observe_backfill)
    try:
        if database == "postgres":
            base = make_engine(os.environ["TEST_POSTGRES_URL"])
            candidate = "backfill_test_" + uuid4().hex
            with base.begin() as connection:
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
                connection.execute(CreateSchema(candidate))
            schema = candidate
            url = make_url(os.environ["TEST_POSTGRES_URL"])
            url = url.set(
                query={
                    **url.query,
                    "options": f"-csearch_path={schema},public -clock_timeout=5s -cstatement_timeout=30s",
                }
            )
        else:
            url = make_url(f"sqlite:///{tmp_path / 'populated.db'}")
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
        )
        command.upgrade(config, "0670a71142ff")
        engine = make_engine(url.render_as_string(hide_password=False))
        assert "run_sources" not in inspect(engine).get_table_names()
        origin_time = datetime(2026, 1, 1, tzinfo=UTC)
        citation_time = datetime(2026, 1, 15, tzinfo=UTC)
        target_time = datetime(2026, 2, 1, tzinfo=UTC)
        with Session(engine) as db:
            user = User(email="backfill@example.test", display_name="Test", password_hash="unused")
            agent = Agent(name="Backfill", draft={})
            db.add_all([user, agent])
            db.flush()
            chat = Conversation(owner_id=user.id, agent_id=agent.id)
            other_chat = Conversation(owner_id=user.id, agent_id=agent.id)
            db.add_all([chat, other_chat])
            db.flush()
            origin = Run(
                owner_id=user.id,
                conversation_id=chat.id,
                snapshot={},
                status="completed",
                created_at=origin_time,
            )
            target = Run(
                owner_id=user.id,
                conversation_id=chat.id,
                snapshot={},
                status="awaiting_confirmation",
                created_at=target_time,
            )
            other = Run(
                owner_id=user.id,
                conversation_id=other_chat.id,
                snapshot={},
                status="completed",
                created_at=origin_time,
            )
            db.add_all([origin, target, other])
            db.flush()
            # Tombstones remain conservative provenance even without a live document.
            values = [(f"prior-{i}", origin, citation_time) for i in range(1201)]
            values += [
                ("own-run", target, citation_time),
                ("equal-time", origin, target_time),
                ("future", origin, datetime(2026, 3, 1, tzinfo=UTC)),
                ("other-chat", other, citation_time),
            ]
            db.add_all(
                [
                    Citation(
                        id=identifier,
                        owner_id=user.id,
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        document_id=None,
                        revision=1,
                        title="Synthetic source",
                        locator="",
                        excerpt="",
                        created_at=created_at,
                    )
                    for identifier, run, created_at in values
                ]
            )
            target_id = target.id
            db.commit()
        command.upgrade(config, "head")
        with engine.connect() as connection:
            actual = connection.execute(
                select(
                    RunSource.run_id,
                    RunSource.citation_id,
                    RunSource.created_at,
                )
            )
            assert {(rid, cid, created.replace(tzinfo=UTC)) for rid, cid, created in actual} == {
                (target_id, f"prior-{i}", target_time) for i in range(1201)
            }
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version")) == "3b9e2f8174ac"
            )
            if database == "postgres":
                assert len(streamed_cursors) == 1 and streamed_cursors[0], (
                    "Populated PostgreSQL backfill must exercise a real server cursor"
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n "
                            "ON n.oid = e.extnamespace WHERE e.extname = 'vector'"
                        )
                    )
                    == "public"
                )
    finally:
        event.remove(Engine, "before_cursor_execute", observe_backfill)
        if engine is not None:
            engine.dispose()
        if base is not None:
            try:
                if schema is not None:
                    with base.begin() as connection:
                        connection.execute(DropSchema(schema, cascade=True))
            finally:
                base.dispose()
