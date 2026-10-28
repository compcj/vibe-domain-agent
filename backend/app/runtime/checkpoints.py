import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from psycopg import Error as PostgresError
from sqlalchemy.engine import make_url


class CheckpointSchemaError(RuntimeError):
    """Internal deployment error; member runtime errors remain sanitized."""


def postgres_connection_url(url):
    return (
        url.set(drivername="postgresql")
        .update_query_dict({"connect_timeout": "10"})
        .render_as_string(hide_password=False)
    )


def initialize_checkpoints(settings):
    """Run SDK-owned PostgreSQL DDL during deployment, before business traffic.

    Application tables remain Alembic-owned. SQLite keeps lazy local setup.
    A connection owns both the advisory lock and bounded DDL session settings.
    """
    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite":
        return
    try:
        with PostgresSaver.from_conn_string(postgres_connection_url(url)) as saver:
            saver.conn.execute("SET lock_timeout = '10s'")
            saver.conn.execute("SET statement_timeout = '60s'")
            saver.conn.execute("SELECT pg_advisory_lock(742921601)")
            try:
                saver.setup()
            finally:
                saver.conn.execute("SELECT pg_advisory_unlock(742921601)")
    except PostgresError:
        raise CheckpointSchemaError(
            "PostgreSQL checkpoint initialization failed; stop conflicting database activity "
            "and rerun python -m app.cli migrate before starting API or workers"
        ) from None


def require_postgres_schema(saver):
    message = (
        "PostgreSQL checkpoint schema is unavailable or incompatible; "
        "run python -m app.cli migrate before starting API or workers"
    )
    try:
        row = saver.conn.execute(
            "SELECT (SELECT MAX(v) FROM checkpoint_migrations) AS version, "
            "to_regclass('checkpoints') AS checkpoints, "
            "to_regclass('checkpoint_blobs') AS checkpoint_blobs, "
            "to_regclass('checkpoint_writes') AS checkpoint_writes"
        ).fetchone()
    except PostgresError:
        raise CheckpointSchemaError(message) from None
    if (
        not row
        or row["version"] != len(PostgresSaver.MIGRATIONS) - 1
        or not all(row[name] for name in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"))
    ):
        raise CheckpointSchemaError(message)


@contextmanager
def checkpointer(state):
    url = make_url(state.settings.database_url)
    if url.get_backend_name() == "sqlite":
        path = Path(url.database).with_suffix(".checkpoints.db")
        with closing(sqlite3.connect(str(path), check_same_thread=False, timeout=30)) as connection:
            saver = SqliteSaver(connection)
            saver.setup()
            yield saver
    else:
        with PostgresSaver.from_conn_string(postgres_connection_url(url)) as saver:
            # No advisory lock or DDL on a run/cleanup path. Initialization is explicit.
            require_postgres_schema(saver)
            yield saver


def delete_checkpoints(state, run_ids):
    with checkpointer(state) as saver:
        for run_id in run_ids:
            saver.delete_thread(run_id)
