import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from conftest import login
from sqlalchemy import select
from test_knowledge import setup_kb, upload
from test_knowledge_runtime import transport
from test_runtime import conversation, start
from test_tools import install_tool, tool_transport


def attachment_answer(runtime_env):
    from app.knowledge.indexing import index_document
    from app.runtime.engine import execute

    client, app, headers, ids, _ = runtime_env
    app.state.dispatch_index = lambda identifier: None
    cid = conversation(client, headers, ids)
    doc = client.post(
        "/api/v1/files",
        headers=headers,
        data={"conversation_id": cid},
        files={"file": ("owner.txt", b"Private owner fact")},
    ).json()
    index_document(app.state, doc["id"])
    captured = []
    app.state.http_transport = transport(captured)
    first = start(client, headers, cid, attachment_ids=[doc["id"]]).json()["id"]
    execute(app.state, first)
    assert client.get(f"/api/v1/runs/{first}").json()["status"] == "completed"
    return client, app, headers, ids, cid, doc, captured


def test_transitive_history_sources_hide_all_derived_answers(runtime_env):
    from app.runtime.engine import execute

    client, app, headers, ids, cid, doc, captured = attachment_answer(runtime_env)
    derived = []
    for _ in range(2):
        rid = start(client, headers, cid).json()["id"]
        execute(app.state, rid)
        assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"
        derived.append(rid)
    assert client.delete(f"/api/v1/files/{doc['id']}", headers=headers).status_code == 204
    assert "Scoped answer" not in client.get(f"/api/v1/conversations/{cid}/messages").text
    for rid in derived:
        assert "Scoped answer" not in client.get(f"/api/v1/runs/{rid}/events").text
    rid = start(client, headers, cid).json()["id"]
    execute(app.state, rid)
    assert "Scoped answer" not in json.dumps(captured[-1]["messages"])
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"


@pytest.mark.parametrize("approved_before_delete", [False, True])
def test_paused_followup_cannot_resume_invalid_history_source(runtime_env, approved_before_delete):
    from app.models import Run
    from app.runtime.engine import execute

    client, app, headers, ids, cid, doc, _ = attachment_answer(runtime_env)
    _, headers = install_tool(client, app, ids)
    calls = []
    app.state.http_transport = tool_transport(calls)
    rid = start(client, headers, cid).json()["id"]
    execute(app.state, rid)
    result = client.get(f"/api/v1/runs/{rid}").json()
    assert result["status"] == "awaiting_confirmation"
    aid = result["approvals"][0]["id"]
    if approved_before_delete:
        assert (
            client.post(
                f"/api/v1/approvals/{aid}/decision", headers=headers, json={"approved": True}
            ).status_code
            == 200
        )
    assert client.delete(f"/api/v1/files/{doc['id']}", headers=headers).status_code == 204
    assert client.get(f"/api/v1/runs/{rid}").json()["approvals"] == []
    if not approved_before_delete:
        assert client.post(
            f"/api/v1/approvals/{aid}/decision", headers=headers, json={"approved": True}
        ).status_code in {403, 409}
    # Even recovered/previously approved checkpoints must fail before external effects.
    with app.state.session_factory() as db:
        db.get(Run, rid).status = "resume_queued"
        db.commit()
    execute(app.state, rid, resume=True)
    assert calls == []
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "failed"


def test_delete_refreshes_document_after_replacement_commit(env, monkeypatch):
    from app.knowledge import api
    from app.knowledge.models import Document
    from app.knowledge.storage import path_for
    from app.models import User

    client, app, headers, kb, _ = setup_kb(env)
    doc = upload(client, headers, kb)
    lock = api.lock_kb
    replaced = []

    def after_replacement(db, identifier):
        upload(client, headers, kb, b"replacement")
        with app.state.session_factory() as other:
            replaced.append(other.get(Document, doc["id"]).storage_key)
        return lock(db, identifier)

    # Route called with a genuinely stale identity-map object in an independent session.
    with app.state.session_factory() as db:
        db.get(Document, doc["id"])
        admin = db.scalar(select(User).where(User.role == "admin"))
        monkeypatch.setattr(api, "lock_kb", after_replacement)

        # Replacement endpoint must use the original lock (avoid recursion).
        def replacement_lock(db, identifier):
            monkeypatch.setattr(api, "lock_kb", lock)
            return after_replacement(db, identifier)

        monkeypatch.setattr(api, "lock_kb", replacement_lock)
        api.delete_document(UUID(doc["id"]), SimpleNamespace(app=app), db, admin)
    assert not path_for(app.state, replaced[0]).exists()


def test_multi_document_rollback_preserves_referenced_files(env):
    from app.knowledge.access import erase_document
    from app.knowledge.models import Document
    from app.knowledge.storage import path_for

    client, app, headers, kb, _ = setup_kb(env)
    docs = [upload(client, headers, kb, name=name) for name in ["a.txt", "b.txt"]]
    with app.state.session_factory() as db:
        rows = [db.get(Document, doc["id"]) for doc in docs]
        keys = [row.storage_key for row in rows]
        for row in rows:
            erase_document(db, app.state, row)
        db.flush()
        db.rollback()
    assert all(path_for(app.state, key).is_file() for key in keys)
    with app.state.session_factory() as db:
        assert len(db.scalars(select(Document)).all()) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("base_url", "https://new.test/v1"),
        ("model", "new-model"),
        ("api_key", "new-key"),
        ("kind", "chat"),
    ],
)
def test_slow_probe_does_not_publish_capabilities_for_edited_configuration(env, field, value):
    client, app, headers, kb, model = setup_kb(env)
    entered, release = Event(), Event()

    def handler(request):
        entered.set()
        assert release.wait(10)
        return httpx.Response(200, json={"data": [{"embedding": [1, 0, 0]}]})

    app.state.http_transport = httpx.MockTransport(handler)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            client.post, f"/api/v1/admin/models/{model['id']}/test", headers=headers
        )
        assert entered.wait(10)
        try:
            changed = client.patch(
                f"/api/v1/admin/models/{model['id']}", headers=headers, json={field: value}
            )
            assert changed.status_code == 200
        finally:
            release.set()
        result = pending.result(timeout=10)
    current = next(m for m in client.get("/api/v1/admin/models").json() if m["id"] == model["id"])
    assert current["capabilities"] == {}
    assert current["tested_at"] is None
    assert result.status_code == 409


def test_draft_cannot_bind_deleted_knowledge_base(runtime_env):
    client, app, _, ids, _ = runtime_env
    client, app, headers, kb, _ = setup_kb((client, app))
    assert (
        client.delete(f"/api/v1/admin/knowledge-bases/{kb['id']}", headers=headers).status_code
        == 204
    )
    response = client.patch(
        f"/api/v1/admin/agents/{ids['agent']}",
        headers=headers,
        json={"draft": {"knowledge_base_ids": [kb["id"]]}},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("operation", ["delete", "replace", "conversation"])
def test_committed_file_cleanup_retries_unlink_failure_after_restart(
    runtime_env, monkeypatch, operation
):
    from app.knowledge import lifecycle
    from app.knowledge.models import Document, FileCleanup
    from app.knowledge.storage import path_for
    from app.main import create_app

    if operation == "conversation":
        client, app, headers, ids, cid, doc, _ = attachment_answer(runtime_env)
    else:
        client, app, headers, kb, _ = setup_kb(runtime_env[:2])
        doc = upload(client, headers, kb)
    with app.state.session_factory() as db:
        key = db.get(Document, doc["id"]).storage_key
    original = lifecycle.remove_file

    def fail(state, key):
        raise OSError("simulated volume outage")

    monkeypatch.setattr(lifecycle, "remove_file", fail)
    if operation == "replace":
        upload(client, headers, kb, b"new")
    elif operation == "delete":
        assert (
            client.delete(f"/api/v1/admin/documents/{doc['id']}", headers=headers).status_code
            == 204
        )
    else:
        assert client.delete(f"/api/v1/conversations/{cid}", headers=headers).status_code == 204
    assert path_for(app.state, key).exists()
    with app.state.session_factory() as db:
        assert db.scalar(select(FileCleanup).where(FileCleanup.storage_key == key))
    restarted = create_app(app.state.settings)
    monkeypatch.setattr(lifecycle, "remove_file", original)
    try:
        assert lifecycle.cleanup_files(restarted.state)["removed"] == 1
        assert not path_for(app.state, key).exists()
        assert lifecycle.cleanup_files(restarted.state)["removed"] == 0
        with app.state.session_factory() as db:
            assert db.scalar(select(FileCleanup.id)) is None
            for row in db.scalars(select(Document)):
                assert path_for(app.state, row.storage_key).exists()
    finally:
        restarted.state.engine.dispose()


def test_abandoned_upload_recovers_without_deleting_live_storage(env):
    from io import BytesIO

    from app.knowledge.lifecycle import cleanup_files
    from app.knowledge.models import Document, FileCleanup
    from app.knowledge.storage import path_for, store_upload
    from fastapi import UploadFile

    client, app, headers, kb, _ = setup_kb(env)
    live = upload(client, headers, kb)
    with app.state.session_factory() as db:
        live_key = db.get(Document, live["id"]).storage_key
        db.add(FileCleanup(storage_key=live_key))  # Obsolete work from a stale worker.
        db.commit()
    with app.state.session_factory() as db:
        key, _ = store_upload(
            app.state, UploadFile(filename="aborted.txt", file=BytesIO(b"partial")), db
        )
        assert path_for(app.state, key).exists()
        db.rollback()  # Crash/DB failure before attaching the document.
    assert cleanup_files(app.state)["removed"] == 1
    assert not path_for(app.state, key).exists()
    assert path_for(app.state, live_key).exists()


def test_cleanup_recovers_intent_when_file_write_fails(env, monkeypatch):
    from io import BytesIO
    from pathlib import Path

    from app.knowledge.lifecycle import cleanup_files
    from app.knowledge.models import FileCleanup
    from app.knowledge.storage import store_upload
    from fastapi import UploadFile

    _, app = env

    def fail(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(Path, "write_bytes", fail)
    with app.state.session_factory() as db:
        with pytest.raises(OSError):
            store_upload(app.state, UploadFile(filename="failed.txt", file=BytesIO(b"bytes")), db)
        db.rollback()
    with app.state.session_factory() as db:
        assert db.scalar(select(FileCleanup.id))
    assert cleanup_files(app.state)["removed"] == 1


def test_new_publication_does_not_revoke_paused_snapshot_sources(runtime_env):
    from app.runtime.engine import execute
    from test_knowledge_runtime import prepare

    client, app, _, ids, _ = runtime_env
    install_tool(client, app, ids)
    client, app, headers, ids, _, _, _ = prepare(runtime_env)
    calls = []
    delegate = tool_transport(calls)

    def handler(request):
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [1, 0, 0]}]})
        return delegate.handle_request(request)

    app.state.http_transport = httpx.MockTransport(handler)
    cid = conversation(client, headers, ids)
    rid = start(client, headers, cid).json()["id"]
    execute(app.state, rid)
    result = client.get(f"/api/v1/runs/{rid}").json()
    assert result["status"] == "awaiting_confirmation"
    aid = result["approvals"][0]["id"]
    admin = login(client)
    draft = client.get(f"/api/v1/admin/agents/{ids['agent']}").json()["draft"]
    draft["knowledge_base_ids"] = []
    assert (
        client.patch(
            f"/api/v1/admin/agents/{ids['agent']}", headers=admin, json={"draft": draft}
        ).status_code
        == 200
    )
    assert (
        client.post(f"/api/v1/admin/agents/{ids['agent']}/publish", headers=admin).status_code
        == 201
    )
    headers = login(client, "member")
    assert (
        client.post(
            f"/api/v1/approvals/{aid}/decision", headers=headers, json={"approved": True}
        ).status_code
        == 200
    )
    execute(app.state, rid, resume=True)
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"
    assert len(calls) == 1


def test_owner_admin_debug_result_and_citations_are_readable(runtime_env):
    from app.models import User
    from app.runtime.engine import execute
    from test_knowledge_runtime import prepare

    client, app, _, ids, _, _, _ = prepare(runtime_env)
    headers = login(client)
    app.state.http_transport = transport([])
    rid = client.post(
        f"/api/v1/admin/agents/{ids['agent']}/debug", headers=headers, json={"message": "question"}
    ).json()["id"]
    execute(app.state, rid)
    result = client.get(f"/api/v1/runs/{rid}").json()
    assert result["status"] == "completed"
    events = client.get(f"/api/v1/runs/{rid}/events").text
    assert "Scoped answer" in events and "Important domain fact" in events
    from app.models import Run

    with app.state.session_factory() as db:
        cid = db.get(Run, rid).conversation_id
    reference = client.get(f"/api/v1/conversations/{cid}/messages").json()[-1]["citations"][0]
    assert client.get(f"/api/v1/citations/{reference['id']}").status_code == 200
    login(client, "member")
    assert client.get(f"/api/v1/runs/{rid}/events").status_code == 404
    assert client.get(f"/api/v1/citations/{reference['id']}").status_code == 404
    login(client)
    with app.state.session_factory() as db:
        db.scalar(select(User).where(User.role == "admin")).role = "member"
        db.commit()
    assert "Scoped answer" not in client.get(f"/api/v1/runs/{rid}/events").text
    assert client.get(f"/api/v1/citations/{reference['id']}").status_code == 404


def test_model_edit_always_clears_probe_result_even_when_loaded_before_probe(env, monkeypatch):
    from app.routers import models

    client, app, headers, _, model = setup_kb(env)
    loaded, release = Event(), Event()
    apply = models.apply_model

    def pause(model, data, settings):
        loaded.set()
        assert release.wait(10)
        return apply(model, data, settings)

    monkeypatch.setattr(models, "apply_model", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        patch = pool.submit(
            client.patch,
            f"/api/v1/admin/models/{model['id']}",
            headers=headers,
            json={"model": "changed"},
        )
        assert loaded.wait(10)
        try:
            assert client.post(f"/api/v1/admin/models/{model['id']}/test", headers=headers).json()[
                "ok"
            ]
        finally:
            release.set()
        assert patch.result(timeout=10).status_code == 200
    current = next(m for m in client.get("/api/v1/admin/models").json() if m["id"] == model["id"])
    assert current["capabilities"] == {}
    assert current["tested_at"] is None


def test_migration_backfills_existing_derived_run_provenance(runtime_env):
    from alembic import command
    from alembic.config import Config
    from app.knowledge.models import FileCleanup, RunSource
    from app.runtime.engine import execute

    client, app, headers, _, cid, doc, _ = attachment_answer(runtime_env)
    rid = start(client, headers, cid).json()["id"]
    execute(app.state, rid)
    with app.state.engine.begin() as connection:
        RunSource.__table__.drop(connection)
        FileCleanup.__table__.drop(connection)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", app.state.settings.database_url)
    command.stamp(config, "0670a71142ff")
    command.upgrade(config, "head")
    assert client.delete(f"/api/v1/files/{doc['id']}", headers=headers).status_code == 204
    assert "Scoped answer" not in client.get(f"/api/v1/runs/{rid}/events").text


def test_unlink_failure_does_not_starve_later_cleanup_intents(env, monkeypatch):
    from app.knowledge import lifecycle
    from app.knowledge.models import FileCleanup

    _, app = env
    with app.state.session_factory() as db:
        db.add(FileCleanup(storage_key="unremovable"))
        db.commit()
        db.add(FileCleanup(storage_key="later"))
        db.commit()
    remove = lifecycle.remove_file

    def fail_first(state, key):
        if key == "unremovable":
            raise OSError("file busy")
        return remove(state, key)

    monkeypatch.setattr(lifecycle, "remove_file", fail_first)
    assert lifecycle.cleanup_files(app.state, limit=1)["removed"] == 0
    assert lifecycle.cleanup_files(app.state, limit=1)["removed"] == 1
