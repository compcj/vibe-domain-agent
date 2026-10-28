from pathlib import Path

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
