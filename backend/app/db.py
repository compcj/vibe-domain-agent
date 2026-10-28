from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def make_engine(url: str):
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, pool_pre_ping=True, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

    return engine


def make_session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


def get_db(request: Request):
    with request.app.state.session_factory() as session:
        session.info["app"] = request.app.state
        try:
            yield session
        finally:
            keys = session.info.pop("upload_keys", set())
            if keys:
                # Failed/quota-rejected requests promptly reclaim their durable upload
                # intents. Crashes still leave them for the scheduled cleanup worker.
                from sqlalchemy.exc import SQLAlchemyError

                from app.knowledge.lifecycle import cleanup_files

                session.rollback()
                try:
                    cleanup_files(request.app.state, keys=keys)
                except (OSError, SQLAlchemyError):
                    pass
