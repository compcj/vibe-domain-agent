import sqlite3
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy.engine import make_url


@contextmanager
def checkpointer(state):
    url = make_url(state.settings.database_url)
    if url.get_backend_name() == "sqlite":
        path = Path(url.database).with_suffix(".checkpoints.db")
        with sqlite3.connect(str(path), check_same_thread=False, timeout=30) as connection:
            saver = SqliteSaver(connection)
            saver.setup()
            yield saver
    else:
        conn_string = url.set(drivername="postgresql").render_as_string(hide_password=False)
        with PostgresSaver.from_conn_string(conn_string) as saver:
            # Serialize SDK DDL initialization across worker processes. The SDK connection is autocommit.
            saver.conn.execute("SELECT pg_advisory_lock(742921601)")
            try:
                saver.setup()
            finally:
                saver.conn.execute("SELECT pg_advisory_unlock(742921601)")
            yield saver


def delete_checkpoints(state, run_ids):
    with checkpointer(state) as saver:
        for run_id in run_ids:
            saver.delete_thread(run_id)
