import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from urllib.parse import quote, urlencode

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from psycopg import Error as PostgresError
from sqlalchemy.engine import make_url


class CheckpointSchemaError(RuntimeError):
    """Internal deployment error; member runtime errors remain sanitized."""


def postgres_connection_url(url):
    # SQLAlchemy URLs use form-encoded query strings (+ for spaces). libpq URIs
    # require percent-encoding, including in credentials, and treat + literally.
    authority = ""
    if url.username is not None:
        authority = quote(url.username, safe="")
        if url.password is not None:
            authority += ":" + quote(str(url.password), safe="")
        authority += "@"
    host = url.host or ""
    authority += "[" + host + "]" if ":" in host else quote(host, safe="")
    if url.port is not None:
        authority += ":" + str(url.port)
    query = urlencode({**url.query, "connect_timeout": "10"}, doseq=True, quote_via=quote)
    return "postgresql://" + authority + "/" + quote(url.database or "", safe="") + "?" + query


def safe_sqlstate(error):
    code = error.sqlstate
    if (
        isinstance(code, str)
        and len(code) == 5
        and all(char in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" for char in code)
    ):
        return code
    return "unavailable"


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
    except PostgresError as error:
        raise CheckpointSchemaError(
            f"PostgreSQL checkpoint initialization failed (SQLSTATE {safe_sqlstate(error)}); "
            "stop conflicting database activity "
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
    except PostgresError as error:
        raise CheckpointSchemaError(f"{message} (SQLSTATE {safe_sqlstate(error)})") from None
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
