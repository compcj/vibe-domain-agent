"""Opt-in real-service tests for CI; dedicated isolated schemas/Redis keys only."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest


@pytest.mark.skipif(not os.getenv("TEST_REDIS_URL"), reason="TEST_REDIS_URL is not configured")
def test_real_redis_global_user_slots_and_token_release():
    from app.runtime.slots import RedisSlots

    prefix = "domain-agent-test:" + uuid4().hex

    class IsolatedSlots(RedisSlots):
        def keys(self, owner):
            return [prefix + ":global", prefix + ":user:" + owner]

    slots = IsolatedSlots(os.environ["TEST_REDIS_URL"])
    leases = []
    try:
        assert slots.acquire("member", "a")
        leases.append(("member", "a"))
        assert slots.acquire("member", "b")
        leases.append(("member", "b"))
        assert not slots.acquire("member", "c")
        slots.release("member", "wrong-token")
        assert not slots.acquire("member", "c")

        def compete(index):
            owner, token = "other-" + str(index), uuid4().hex
            if slots.acquire(owner, token):
                return owner, token
            return None

        with ThreadPoolExecutor(max_workers=12) as pool:
            accepted = [item for item in pool.map(compete, range(12)) if item]
        leases.extend(accepted)
        assert len(accepted) == 8
    finally:
        for owner, token in leases:
            slots.release(owner, token)
        slots.redis.close()


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL is not configured"
)
def test_real_postgres_migrations_atomic_claim_and_checkpoint():
    from alembic import command
    from alembic.config import Config
    from app.config import Settings
    from app.db import make_engine
    from app.main import create_app
    from app.models import Agent, Conversation, Run, User
    from app.runtime.checkpoints import checkpointer, initialize_checkpoints
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt
    from sqlalchemy import update
    from sqlalchemy.engine import make_url
    from sqlalchemy.schema import CreateSchema, DropSchema

    schema = "runtime_test_" + uuid4().hex
    base = make_engine(os.environ["TEST_POSTGRES_URL"])
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    url = make_url(os.environ["TEST_POSTGRES_URL"])
    url = url.set(
        query={
            **url.query,
            "options": "-csearch_path="
            + schema
            + ",public -clock_timeout=5s -cstatement_timeout=30s",
        }
    )
    app = create_app(
        Settings(
            database_url=url.render_as_string(hide_password=False), app_env="test", redis_url=""
        )
    )
    try:
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
        )
        command.upgrade(config, "head")
        initialize_checkpoints(app.state.settings)
        command.check(config)
        with app.state.session_factory() as db:
            user = User(email="test@example.test", display_name="Test", password_hash="unused")
            agent = Agent(name="Test", draft={})
            db.add_all([user, agent])
            db.flush()
            conv = Conversation(owner_id=user.id, agent_id=agent.id)
            db.add(conv)
            db.commit()
            cid, uid = conv.id, user.id

        def claim(_):
            with app.state.session_factory() as db:
                rid = str(uuid4())
                result = db.execute(
                    update(Conversation)
                    .where(Conversation.id == cid, Conversation.active_run_id.is_(None))
                    .values(active_run_id=rid)
                ).rowcount
                if result:
                    db.add(Run(id=rid, owner_id=uid, conversation_id=cid, snapshot={}))
                db.commit()
                return result

        with ThreadPoolExecutor(max_workers=4) as pool:
            assert sum(pool.map(claim, range(4))) == 1

        # Real pgvector storage and database-side cosine, including distinct embedding dimensions.
        from app.knowledge.models import Chunk, Document, KnowledgeBase
        from app.knowledge.retrieval import matching_chunks
        from app.models import ModelConnection

        with app.state.session_factory() as db:
            model = ModelConnection(
                name="embedding", kind="embedding", base_url="https://mock.test", model="mock"
            )
            db.add(model)
            db.flush()
            first = KnowledgeBase(name="first", embedding_model_id=model.id)
            other = KnowledgeBase(name="other", embedding_model_id=model.id)
            db.add_all([first, other])
            db.flush()
            ready = Document(
                knowledge_base_id=first.id,
                name="ready.txt",
                status="ready",
                storage_key="unused",
                checksum="x",
                embedding_fingerprint="space",
            )
            outside = Document(
                knowledge_base_id=other.id,
                name="other.txt",
                status="ready",
                storage_key="unused",
                checksum="x",
                embedding_fingerprint="other",
            )
            queued = Document(
                knowledge_base_id=first.id,
                name="queued.txt",
                status="queued",
                storage_key="unused",
                checksum="x",
                embedding_fingerprint="space",
            )
            db.add_all([ready, outside, queued])
            db.flush()
            db.add_all(
                [
                    Chunk(
                        document_id=ready.id,
                        revision=1,
                        text="matched",
                        locator="page 1",
                        vector=[1, 0, 0],
                    ),
                    Chunk(
                        document_id=outside.id,
                        revision=1,
                        text="outside",
                        locator="page 1",
                        vector=[1, 0],
                    ),
                    Chunk(
                        document_id=queued.id,
                        revision=1,
                        text="queued",
                        locator="page 1",
                        vector=[1, 0, 0],
                    ),
                    Chunk(
                        document_id=ready.id,
                        revision=0,
                        text="stale",
                        locator="page 1",
                        vector=[1, 0, 0],
                    ),
                ]
            )
            db.commit()
            assert [
                chunk.text for chunk, _ in matching_chunks(db, first.id, "space", [1, 0, 0])
            ] == ["matched"]

        def gate(state):
            return {"approved": interrupt("confirm")}

        graph = StateGraph(dict)
        graph.add_node("gate", gate)
        graph.add_edge(START, "gate")
        graph.add_edge("gate", END)
        run_config = {"configurable": {"thread_id": str(uuid4())}}
        with checkpointer(app.state) as saver:
            assert graph.compile(checkpointer=saver).invoke({"approved": False}, run_config)[
                "__interrupt__"
            ]
        with checkpointer(app.state) as saver:
            assert (
                graph.compile(checkpointer=saver).invoke(Command(resume=True), run_config)[
                    "approved"
                ]
                is True
            )
    finally:
        app.state.engine.dispose()
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()
