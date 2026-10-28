"""Checkpoint DDL belongs to deployment migration, never a run transaction."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from app.config import Settings
from app.runtime import checkpoints
from psycopg.errors import LockNotAvailable, UndefinedTable


@pytest.fixture
def postgres_saver(monkeypatch):
    statements = []
    connection = SimpleNamespace()
    saver = SimpleNamespace(conn=connection, setups=0)
    row = {
        "version": len(checkpoints.PostgresSaver.MIGRATIONS) - 1,
        "checkpoints": "checkpoints",
        "checkpoint_blobs": "checkpoint_blobs",
        "checkpoint_writes": "checkpoint_writes",
    }

    def execute(sql):
        statements.append(sql)
        if "checkpoint_migrations" in sql and getattr(saver, "missing", False):
            raise UndefinedTable("private database details")
        return SimpleNamespace(fetchone=lambda: row)

    def setup():
        saver.setups += 1
        if getattr(saver, "blocked", False):
            raise LockNotAvailable("private database details")

    connection.execute = execute
    saver.setup = setup

    @contextmanager
    def connect(url):
        saver.url = url
        yield saver

    monkeypatch.setattr(checkpoints.PostgresSaver, "from_conn_string", connect)
    settings = Settings(
        database_url="postgresql+psycopg://example/db", app_env="test", redis_url=""
    )
    return saver, statements, settings


def test_postgres_runtime_open_never_runs_schema_ddl(postgres_saver):
    saver, statements, settings = postgres_saver
    with checkpoints.checkpointer(SimpleNamespace(settings=settings)) as opened:
        assert opened is saver
    assert saver.setups == 0
    assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    assert not any("pg_advisory_lock" in sql for sql in statements)


def test_postgres_missing_schema_has_internal_migration_instruction(postgres_saver):
    saver, _, settings = postgres_saver
    saver.missing = True
    with pytest.raises(RuntimeError, match="app.cli migrate") as error:
        with checkpoints.checkpointer(SimpleNamespace(settings=settings)):
            pytest.fail("An uninitialized saver must not reach the runtime")
    assert "private database details" not in str(error.value)
    assert saver.setups == 0


def test_postgres_initialization_is_serialized_and_bounded(postgres_saver):
    saver, statements, settings = postgres_saver
    checkpoints.initialize_checkpoints(settings)
    assert saver.setups == 1
    assert statements.index("SET lock_timeout = '10s'") < statements.index(
        "SELECT pg_advisory_lock(742921601)"
    )
    assert statements.index("SET statement_timeout = '60s'") < statements.index(
        "SELECT pg_advisory_lock(742921601)"
    )
    assert statements[-1] == "SELECT pg_advisory_unlock(742921601)"
    assert "connect_timeout=10" in saver.url


def test_postgres_initialization_unlocks_and_sanitizes_timeout(postgres_saver):
    saver, statements, settings = postgres_saver
    saver.blocked = True
    with pytest.raises(RuntimeError, match="checkpoint initialization") as error:
        checkpoints.initialize_checkpoints(settings)
    assert statements[-1] == "SELECT pg_advisory_unlock(742921601)"
    assert "private database details" not in str(error.value)


def test_migrate_initializes_checkpoints_after_application_schema(tmp_path, monkeypatch):
    from app import cli
    from app.db import make_engine
    from sqlalchemy import inspect

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'migrate.db'}", app_env="test", redis_url=""
    )
    initialized = []

    def initialize(selected):
        assert selected is settings
        engine = make_engine(selected.database_url)
        try:
            assert {"runs", "tool_calls"}.issubset(inspect(engine).get_table_names())
        finally:
            engine.dispose()
        initialized.append(True)

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(checkpoints, "initialize_checkpoints", initialize, raising=False)
    monkeypatch.setattr("sys.argv", ["app.cli", "migrate"])
    cli.main()
    assert initialized == [True]
