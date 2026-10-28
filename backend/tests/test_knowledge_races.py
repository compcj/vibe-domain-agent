from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_knowledge import setup_kb, upload


def test_stale_index_cannot_replace_new_revision_or_deleted_document(env):
    from app.knowledge.indexing import index_document
    from app.knowledge.models import Chunk, Document

    client, app, headers, kb, _ = setup_kb(env)
    doc = upload(client, headers, kb)

    def replace(request):
        upload(client, headers, kb, b"new revision")
        return httpx.Response(200, json={"data": [{"embedding": [1, 0, 0]}]})

    app.state.http_transport = httpx.MockTransport(replace)
    index_document(app.state, doc["id"])
    with app.state.session_factory() as db:
        assert db.get(Document, doc["id"]).status == "queued"
        assert db.get(Document, doc["id"]).revision == 2
        assert not db.scalars(select(Chunk)).all()

    def erase(request):
        assert (
            client.delete(f"/api/v1/admin/documents/{doc['id']}", headers=headers).status_code
            == 204
        )
        return httpx.Response(200, json={"data": [{"embedding": [1, 0, 0]}]})

    app.state.http_transport = httpx.MockTransport(erase)
    index_document(app.state, doc["id"])
    with app.state.session_factory() as db:
        assert db.get(Document, doc["id"]) is None
        assert not db.scalars(select(Chunk)).all()


def test_failed_retry_and_stale_recovery(env):
    from app.knowledge.indexing import index_document, recover_indexing
    from app.knowledge.models import Document
    from app.models import utcnow

    client, app, headers, kb, _ = setup_kb(env)
    doc = upload(client, headers, kb, b"not a pdf", "bad.pdf")
    index_document(app.state, doc["id"])
    failed = client.get(f"/api/v1/admin/knowledge-bases/{kb['id']}/documents").json()[0]
    assert failed["status"] == "failed" and failed["error"]
    assert "files" not in failed["error"] and "not a pdf" not in failed["error"]
    assert (
        client.post(f"/api/v1/admin/documents/{doc['id']}/retry", headers=headers).status_code
        == 202
    )
    with app.state.session_factory() as db:
        current = db.get(Document, doc["id"])
        current.status, current.claim_token = "indexing", "old-token"
        current.claim_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    dispatched = []
    app.state.dispatch_index = dispatched.append
    assert recover_indexing(app.state) == 1 and dispatched == [doc["id"]]
    with app.state.session_factory() as db:
        assert db.get(Document, doc["id"]).claim_token is None
        assert db.get(Document, doc["id"]).status == "queued"


def test_model_space_change_and_disabled_model_fail_explicitly(env):
    from app.knowledge.indexing import index_document

    client, app, headers, kb, model = setup_kb(env)
    doc = upload(client, headers, kb)
    index_document(app.state, doc["id"])
    client.patch(f"/api/v1/admin/models/{model['id']}", headers=headers, json={"enabled": False})
    assert (
        client.post(
            f"/api/v1/admin/knowledge-bases/{kb['id']}/search", headers=headers, json={"query": "x"}
        ).status_code
        == 409
    )
    client.patch(
        f"/api/v1/admin/models/{model['id']}",
        headers=headers,
        json={"enabled": True, "model": "new-space"},
    )
    assert (
        client.post(
            f"/api/v1/admin/knowledge-bases/{kb['id']}/search", headers=headers, json={"query": "x"}
        ).status_code
        == 409
    )
    model2 = client.post(
        "/api/v1/admin/models",
        headers=headers,
        json={
            "name": "two",
            "kind": "embedding",
            "model": "second",
            "base_url": "https://two.test/v1",
        },
    ).json()
    assert (
        client.patch(
            f"/api/v1/admin/knowledge-bases/{kb['id']}",
            headers=headers,
            json={"embedding_model_id": model2["id"]},
        ).status_code
        == 409
    )
    client.delete(f"/api/v1/admin/documents/{doc['id']}", headers=headers)
    assert (
        client.patch(
            f"/api/v1/admin/knowledge-bases/{kb['id']}",
            headers=headers,
            json={"embedding_model_id": model2["id"]},
        ).status_code
        == 200
    )


def test_parallel_uploads_enforce_five_file_limit(runtime_env):
    client, app, headers, ids, _ = runtime_env
    app.state.dispatch_index = lambda identifier: None
    cid = client.post(
        "/api/v1/conversations", headers=headers, json={"agent_id": ids["agent"]}
    ).json()["id"]
    cookies = dict(client.cookies)

    def send(number):
        with TestClient(app) as other:
            other.cookies.update(cookies)
            return other.post(
                "/api/v1/files",
                headers=headers,
                data={"conversation_id": cid},
                files={"file": (f"{number}.txt", b"owner text")},
            ).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(send, range(8)))
    assert sorted(codes) == [202] * 5 + [409] * 3
    assert len(client.get("/api/v1/files", params={"conversation_id": cid}).json()) == 5


def test_invalid_uploads_and_member_admin_denial(runtime_env):
    client, app, headers, ids, _ = runtime_env
    app.state.dispatch_index = lambda identifier: None
    cid = client.post(
        "/api/v1/conversations", headers=headers, json={"agent_id": ids["agent"]}
    ).json()["id"]
    for name in ["../evil.txt", "bad.html", "bad.xlsm"]:
        assert (
            client.post(
                "/api/v1/files",
                headers=headers,
                data={"conversation_id": cid},
                files={"file": (name, b"bad")},
            ).status_code
            == 422
        )
    assert (
        client.post(
            "/api/v1/files",
            headers=headers,
            data={"conversation_id": cid},
            files={"file": ("big.txt", b"x" * (20 * 1024 * 1024 + 1))},
        ).status_code
        == 413
    )
    assert client.get("/api/v1/admin/knowledge-bases").status_code == 403
    assert not Path(app.state.settings.storage_dir).exists()


def test_index_rechecks_model_before_each_batch(env):
    import json

    from app.knowledge.indexing import index_document

    client, app, headers, kb, model = setup_kb(env)
    doc = upload(client, headers, kb, b"text " * 14000)
    calls = []

    def handler(request):
        count = len(json.loads(request.content)["input"])
        calls.append(count)
        client.patch(
            f"/api/v1/admin/models/{model['id']}", headers=headers, json={"enabled": False}
        )
        return httpx.Response(
            200, json={"data": [{"index": i, "embedding": [1, 0, 0]} for i in range(count)]}
        )

    app.state.http_transport = httpx.MockTransport(handler)
    index_document(app.state, doc["id"])
    assert calls == [32]
    assert (
        client.get(f"/api/v1/admin/knowledge-bases/{kb['id']}/documents").json()[0]["status"]
        == "failed"
    )


def test_postgres_cosine_uses_dimension_guard_before_scoring():
    from unittest.mock import Mock

    from app.knowledge.retrieval import matching_chunks
    from sqlalchemy.dialects import postgresql

    db = Mock()
    db.bind.dialect.name = "postgresql"
    db.execute.return_value.all.return_value = []
    matching_chunks(db, "kb", "space", [1, 0, 0])
    statement = db.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "CASE WHEN" in sql and "vector_dims" in sql and "<=>" in sql


def test_deletion_between_index_claim_and_read_is_inert(env, monkeypatch):
    from app.knowledge.indexing import index_document
    from sqlalchemy.orm import Session

    client, app, headers, kb, _ = setup_kb(env)
    doc = upload(client, headers, kb)
    original = Session.commit
    triggered = False

    def commit(session):
        nonlocal triggered
        original(session)
        if not triggered:
            triggered = True
            assert (
                client.delete(f"/api/v1/admin/documents/{doc['id']}", headers=headers).status_code
                == 204
            )

    monkeypatch.setattr(Session, "commit", commit)
    index_document(app.state, doc["id"])
    assert client.get(f"/api/v1/admin/knowledge-bases/{kb['id']}/documents").json() == []
