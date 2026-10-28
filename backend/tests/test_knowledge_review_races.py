"""Run against both SQLite and isolated PostgreSQL; no test-order dependency."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Event
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import event, select
from test_knowledge import setup_kb, upload
from test_runtime_review_fixes import race_env as race_env


def knowledge_env(race_env):
    client, app, _, _, _ = race_env
    storage = app.state.settings.storage_dir
    values = setup_kb((client, app))
    app.state.settings.storage_dir = storage
    return values


def admin(db):
    from app.models import User

    return db.scalar(select(User).where(User.role == "admin"))


def test_replacement_commit_while_delete_waits_for_kb_lock(race_env, monkeypatch):
    from app.knowledge import api
    from app.knowledge.models import Document
    from app.knowledge.storage import path_for

    client, app, headers, kb, _ = knowledge_env(race_env)
    doc = upload(client, headers, kb)
    changed, delete_waiting, release = Event(), Event(), Event()
    lock = api.lock_kb
    keys = []

    def observed_lock(db, identifier):
        if db.info.get("deleting"):
            delete_waiting.set()
        return lock(db, identifier)

    monkeypatch.setattr(api, "lock_kb", observed_lock)

    def replace():
        with app.state.session_factory() as db:

            def pause(session):
                row = session.get(Document, doc["id"])
                keys.append(row.storage_key)
                changed.set()
                assert release.wait(10)

            event.listen(db, "before_commit", pause, once=True)
            api.upload_document(
                UUID(kb["id"]),
                SimpleNamespace(app=app),
                db,
                admin(db),
                UploadFile(filename="fact.txt", file=BytesIO(b"replaced")),
            )

    def erase():
        with app.state.session_factory() as db:
            db.info["deleting"] = True
            api.delete_document(UUID(doc["id"]), SimpleNamespace(app=app), db, admin(db))

    with ThreadPoolExecutor(max_workers=2) as pool:
        replacement = pool.submit(replace)
        assert changed.wait(10)
        deletion = pool.submit(erase)
        try:
            assert delete_waiting.wait(10)
            assert not deletion.done()
        finally:
            release.set()
        replacement.result(timeout=10)
        deletion.result(timeout=10)
    with app.state.session_factory() as db:
        assert db.get(Document, doc["id"]) is None
    assert not path_for(app.state, keys[0]).exists()


@pytest.mark.parametrize("operation", ["draft", "publish"])
def test_binding_cannot_commit_behind_knowledge_delete(race_env, operation):
    from app.knowledge import api
    from app.knowledge.locking import lock_references
    from app.knowledge.models import KnowledgeBase
    from app.models import Agent, AgentVersion
    from app.routers.agents import apply_agent, lock_agent, validate_publication
    from app.schemas import AgentPatch

    _, app, _, kb, _ = knowledge_env(race_env)
    aid = race_env[3]["agent"]
    scanned, attempting, release = Event(), Event(), Event()

    def erase():
        with app.state.session_factory() as db:

            def pause(session):
                scanned.set()
                assert release.wait(10)

            event.listen(db, "before_commit", pause, once=True)
            api.delete_knowledge(UUID(kb["id"]), SimpleNamespace(app=app), db, admin(db))

    def bind():
        with app.state.session_factory() as db:
            agent = lock_agent(db, aid)
            draft = {**agent.draft, "knowledge_base_ids": [kb["id"]]}
            attempting.set()
            try:
                if operation == "draft":
                    apply_agent(agent, AgentPatch(draft=draft), db)
                else:
                    # Publication validation protects every retained-version writer,
                    # including a transaction that has loaded its config before deletion.
                    validate_publication(db, draft, app.state)
                    db.add(
                        AgentVersion(
                            agent_id=aid, version=2, snapshot=draft, created_by=admin(db).id
                        )
                    )
                db.commit()
            except HTTPException as exc:
                db.rollback()
                return exc.status_code
            return 201

    with ThreadPoolExecutor(max_workers=2) as pool:
        deletion = pool.submit(erase)
        assert scanned.wait(10)
        binding = pool.submit(bind)
        # SQLite's global write lock can block the Agent lock before the KB lock.
        if app.state.engine.dialect.name == "postgresql":
            assert attempting.wait(10)
        try:
            assert not binding.done()
        finally:
            release.set()
        deletion.result(timeout=10)
        assert binding.result(timeout=10) == 422
    with app.state.session_factory() as db:
        assert db.get(KnowledgeBase, kb["id"]) is None
        assert kb["id"] not in db.get(Agent, aid).draft.get("knowledge_base_ids", [])
        assert all(
            kb["id"] not in version.snapshot.get("knowledge_base_ids", [])
            for version in db.scalars(select(AgentVersion))
        )
        # Deterministic sorted locking accepts duplicates and orders reverse input.
        with pytest.raises(HTTPException):
            lock_references(db, [kb["id"], kb["id"]])


def test_cleanup_cannot_remove_an_inflight_upload(race_env):
    from app.knowledge.lifecycle import cleanup_files
    from app.knowledge.models import Document, FileCleanup
    from app.knowledge.storage import path_for, store_upload

    _, app, _, kb, _ = knowledge_env(race_env)
    attempting = Event()
    with app.state.session_factory() as db:
        key, checksum = store_upload(
            app.state, UploadFile(filename="live.txt", file=BytesIO(b"live")), db
        )
        with ThreadPoolExecutor(max_workers=1) as pool:

            def clean():
                attempting.set()
                return cleanup_files(app.state)

            pending = pool.submit(clean)
            assert attempting.wait(10)
            # Commit the actual document while the cleaner waits/skips its upload fence.
            db.add(
                Document(
                    knowledge_base_id=kb["id"], name="live.txt", storage_key=key, checksum=checksum
                )
            )
            db.commit()
            pending.result(timeout=10)
    assert path_for(app.state, key).read_bytes() == b"live"
    assert cleanup_files(app.state)["removed"] == 0
    with app.state.session_factory() as db:
        assert db.scalar(select(FileCleanup.id)) is None


def test_retained_publication_wins_delete_race_and_rollback_survives(race_env):
    from app.knowledge import api
    from app.knowledge.models import KnowledgeBase
    from app.models import Agent, AgentVersion
    from app.routers.agents import lock_agent, rollback, validate_publication
    from app.schemas import Rollback

    _, app, _, kb, _ = knowledge_env(race_env)
    aid = race_env[3]["agent"]
    bound, release, deleting = Event(), Event(), Event()
    versions = []

    def publish():
        with app.state.session_factory() as db:
            actor = admin(db)
            agent = lock_agent(db, aid)
            draft = {**agent.draft, "knowledge_base_ids": [kb["id"]]}
            validate_publication(db, draft, app.state)
            # Leave the draft detached: only the retained publication prevents deletion.
            version = AgentVersion(agent_id=aid, version=2, snapshot=draft, created_by=actor.id)
            db.add(version)
            db.flush()
            versions.append(version.id)
            bound.set()
            assert release.wait(10)
            db.commit()

    def erase():
        with app.state.session_factory() as db:
            actor = admin(db)
            deleting.set()
            try:
                api.delete_knowledge(UUID(kb["id"]), SimpleNamespace(app=app), db, actor)
            except HTTPException as exc:
                return exc.status_code
            return 204

    with ThreadPoolExecutor(max_workers=2) as pool:
        publication = pool.submit(publish)
        assert bound.wait(10)
        deletion = pool.submit(erase)
        try:
            assert deleting.wait(10)
            assert not deletion.done()
        finally:
            release.set()
        publication.result(timeout=10)
        assert deletion.result(timeout=10) == 409
    with app.state.session_factory() as db:
        assert db.get(KnowledgeBase, kb["id"])
        rollback(
            UUID(aid), Rollback(version_id=versions[0]), SimpleNamespace(app=app), db, admin(db)
        )
        assert db.get(Agent, aid).draft["knowledge_base_ids"] == [kb["id"]]


def test_opposite_multi_kb_bindings_use_consistent_lock_order(race_env):
    from threading import Barrier

    from app.knowledge.locking import lock_references
    from app.knowledge.models import KnowledgeBase

    _, app, _, first, model = knowledge_env(race_env)
    with app.state.session_factory() as db:
        second = KnowledgeBase(name="second", embedding_model_id=model["id"])
        db.add(second)
        db.commit()
        ids = [first["id"], second.id]
    ready = Barrier(2)

    def bind(identifiers):
        with app.state.session_factory() as db:
            ready.wait(timeout=10)
            lock_references(db, identifiers)
            db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(bind, ids)
        two = pool.submit(bind, list(reversed(ids)))
        one.result(timeout=10)
        two.result(timeout=10)
