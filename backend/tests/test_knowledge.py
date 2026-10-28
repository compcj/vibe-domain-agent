from pathlib import Path

import httpx
from conftest import login
from sqlalchemy import select


def setup_kb(env):
    client, app = env
    app.state.settings.storage_dir = str(Path(app.state.engine.url.database).parent / "files")
    app.state.dispatch_index = lambda identifier: None
    app.state.http_transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [1, 0, 0], "index": i}
                    for i, _ in enumerate(__import__("json").loads(request.content)["input"])
                ]
            },
        )
    )
    headers = login(client)
    model = client.post(
        "/api/v1/admin/models",
        headers=headers,
        json={
            "name": "embedding",
            "kind": "embedding",
            "base_url": "https://emb.test/v1",
            "model": "emb",
        },
    ).json()
    response = client.post(
        "/api/v1/admin/knowledge-bases",
        headers=headers,
        json={"name": "Knowledge", "description": "", "embedding_model_id": model["id"]},
    )
    assert response.status_code == 201, response.text
    return client, app, headers, response.json(), model


def upload(client, headers, kb, text=b"Important domain fact", name="fact.txt"):
    response = client.post(
        f"/api/v1/admin/knowledge-bases/{kb['id']}/documents",
        headers=headers,
        files={"file": (name, text)},
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_admin_index_search_replace_delete(env):
    from app.knowledge.indexing import index_document
    from app.knowledge.models import Chunk, Document

    client, app, headers, kb, model = setup_kb(env)
    doc = upload(client, headers, kb)
    assert set(doc) == {"id", "name", "status", "error"} and doc["status"] == "queued"
    index_document(app.state, doc["id"])
    index_document(app.state, doc["id"])
    result = client.post(
        f"/api/v1/admin/knowledge-bases/{kb['id']}/search", headers=headers, json={"query": "fact"}
    )
    assert result.status_code == 200 and result.json()[0]["excerpt"] == "Important domain fact"
    with app.state.session_factory() as db:
        original_path = db.get(Document, doc["id"]).storage_key
        assert len(db.scalars(select(Chunk)).all()) == 1
    assert client.delete(f"/api/v1/admin/models/{model['id']}", headers=headers).status_code == 409
    changed = upload(client, headers, kb, b"Replaced fact")
    assert changed["id"] == doc["id"]
    assert not (Path(app.state.settings.storage_dir) / original_path).exists()
    assert (
        client.post(
            f"/api/v1/admin/knowledge-bases/{kb['id']}/search",
            headers=headers,
            json={"query": "fact"},
        ).json()
        == []
    )
    index_document(app.state, doc["id"])
    assert (
        client.post(f"/api/v1/admin/documents/{doc['id']}/retry", headers=headers).status_code
        == 409
    )
    assert client.delete(f"/api/v1/admin/documents/{doc['id']}", headers=headers).status_code == 204
    index_document(app.state, doc["id"])
    assert (
        client.post(
            f"/api/v1/admin/knowledge-bases/{kb['id']}/search",
            headers=headers,
            json={"query": "fact"},
        ).json()
        == []
    )


def test_owner_attachment_lifecycle_and_active_run(runtime_env):
    from app.knowledge.indexing import index_document

    client, app, headers, ids, _ = runtime_env
    app.state.settings.storage_dir = str(Path(app.state.engine.url.database).parent / "files")
    app.state.dispatch_index = lambda identifier: None
    conversation = client.post(
        "/api/v1/conversations", headers=headers, json={"agent_id": ids["agent"]}
    ).json()
    second = client.post(
        "/api/v1/conversations", headers=headers, json={"agent_id": ids["agent"]}
    ).json()

    def send():
        return client.post(
            "/api/v1/files",
            headers=headers,
            data={"conversation_id": conversation["id"]},
            files={"file": ("hello.txt", b"owner content")},
        )

    uploaded = send()
    assert uploaded.status_code == 202, uploaded.text
    doc = uploaded.json()
    endpoint = f"/api/v1/conversations/{conversation['id']}/runs"
    assert (
        client.post(
            endpoint, headers=headers, json={"message": "question", "attachment_ids": [doc["id"]]}
        ).status_code
        == 422
    )
    index_document(app.state, doc["id"])
    assert client.get(f"/api/v1/files/{doc['id']}/download").content == b"owner content"
    assert (
        client.post(
            f"/api/v1/conversations/{second['id']}/runs",
            headers=headers,
            json={"message": "q", "attachment_ids": [doc["id"]]},
        ).status_code
        == 422
    )
    run = client.post(
        endpoint, headers=headers, json={"message": "question", "attachment_ids": [doc["id"]]}
    ).json()
    assert client.delete(f"/api/v1/files/{doc['id']}", headers=headers).status_code == 409
    client.post(f"/api/v1/runs/{run['id']}/cancel", headers=headers)
    for _ in range(4):
        assert send().status_code == 202
    assert send().status_code == 409
    admin_headers = login(client)
    assert client.get(f"/api/v1/files/{doc['id']}").status_code == 404
    assert client.get(f"/api/v1/files/{doc['id']}/download").status_code == 404
    assert client.delete(f"/api/v1/files/{doc['id']}", headers=admin_headers).status_code == 404
    headers = login(client, "member")
    assert (
        client.delete(f"/api/v1/conversations/{conversation['id']}", headers=headers).status_code
        == 204
    )
    assert not list(Path(app.state.settings.storage_dir).glob("*"))
