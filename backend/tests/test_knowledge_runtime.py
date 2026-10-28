import json

import httpx
from conftest import login
from test_knowledge import setup_kb, upload
from test_runtime import conversation, start


def prepare(runtime_env):
    from app.knowledge.indexing import index_document
    from app.models import Agent

    client, app, _, ids, _ = runtime_env
    client, app, admin_headers, kb, model = setup_kb((client, app))
    doc = upload(client, admin_headers, kb)
    index_document(app.state, doc["id"])
    with app.state.session_factory() as db:
        agent = db.get(Agent, ids["agent"])
        agent.draft = {**agent.draft, "knowledge_base_ids": [kb["id"]]}
        db.commit()
    assert (
        client.post(
            f"/api/v1/admin/agents/{ids['agent']}/publish", headers=admin_headers
        ).status_code
        == 201
    )
    return client, app, login(client, "member"), ids, kb, doc, model


def transport(captured):
    def handler(request):
        body = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [1, 0, 0]}]})
        captured.append(body)
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"Scoped answer"}}]}\n\ndata: [DONE]\n\n',
        )

    return httpx.MockTransport(handler)


def test_graph_receives_scoped_context_and_live_citations(runtime_env):
    from app.models import User
    from app.runtime.engine import execute

    client, app, headers, ids, kb, doc, _ = prepare(runtime_env)
    captured = []
    app.state.http_transport = transport(captured)
    cid = conversation(client, headers, ids)
    rid = start(client, headers, cid).json()["id"]
    execute(app.state, rid)
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"
    assert "Important domain fact" in json.dumps(captured)
    messages = client.get(f"/api/v1/conversations/{cid}/messages").json()
    citation = messages[-1]["citations"][0]
    assert set(citation) == {"id", "title", "locator", "excerpt"}
    assert client.get(f"/api/v1/citations/{citation['id']}").json() == citation
    assert "Important domain fact" in client.get(f"/api/v1/runs/{rid}/events").text
    admin_headers = login(client)
    assert client.get(f"/api/v1/citations/{citation['id']}").status_code == 404
    assert (
        client.delete(
            f"/api/v1/admin/knowledge-bases/{kb['id']}", headers=admin_headers
        ).status_code
        == 409
    )
    headers = login(client, "member")
    with app.state.session_factory() as db:
        db.get(User, ids["user"]).groups = []
        db.commit()
    assert client.get(f"/api/v1/citations/{citation['id']}").status_code == 404
    assert client.get(f"/api/v1/conversations/{cid}/messages").json()[-1]["citations"] == []
    assert "Important domain fact" not in client.get(f"/api/v1/runs/{rid}/events").text
    assert "Scoped answer" not in client.get(f"/api/v1/runs/{rid}/events").text
    assert "Scoped answer" not in client.get(f"/api/v1/conversations/{cid}/messages").text


def test_deleted_source_hidden_from_replay_and_saved_graph(runtime_env):
    from app.models import Run
    from app.runtime.engine import execute

    client, app, headers, ids, _, doc, _ = prepare(runtime_env)
    captured = []
    app.state.http_transport = transport(captured)
    cid = conversation(client, headers, ids)
    rid = start(client, headers, cid).json()["id"]
    execute(app.state, rid)
    citation = client.get(f"/api/v1/conversations/{cid}/messages").json()[-1]["citations"][0]
    admin_headers = login(client)
    client.delete(f"/api/v1/admin/documents/{doc['id']}", headers=admin_headers)
    login(client, "member")
    assert client.get(f"/api/v1/citations/{citation['id']}").status_code == 404
    assert client.get(f"/api/v1/conversations/{cid}/messages").json()[-1]["citations"] == []
    assert "Important domain fact" not in client.get(f"/api/v1/runs/{rid}/events").text
    assert "Scoped answer" not in client.get(f"/api/v1/runs/{rid}/events").text
    assert "Scoped answer" not in client.get(f"/api/v1/conversations/{cid}/messages").text
    with app.state.session_factory() as db:
        run = db.get(Run, rid)
        run.status = "resume_queued"
        db.commit()
    execute(app.state, rid, resume=True)
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "failed"
    assert len(captured) == 1


def test_frozen_embedding_credentials_and_unlocked_network(runtime_env):
    from app.models import ModelConnection, Run
    from app.runtime.engine import execute
    from app.security import encrypt_secret
    from sqlalchemy import update

    client, app, headers, ids, _, _, model = prepare(runtime_env)
    with app.state.session_factory() as db:
        current = db.get(ModelConnection, model["id"])
        current.api_key_encrypted = encrypt_secret("old-key", app.state.settings)
        db.commit()
    cid = conversation(client, headers, ids)
    rid = start(client, headers, cid).json()["id"]
    with app.state.session_factory() as db:
        db.get(ModelConnection, model["id"]).api_key_encrypted = encrypt_secret(
            "new-key", app.state.settings
        )
        db.commit()
    captured = []
    delegate = transport(captured)

    def handler(request):
        if request.url.path.endswith("/embeddings"):
            assert request.headers["authorization"] == "Bearer old-key"
            with app.state.session_factory() as db:
                db.execute(update(Run).where(Run.id == rid).values(steps=Run.steps))
                db.commit()
        return delegate.handle_request(request)

    app.state.http_transport = httpx.MockTransport(handler)
    execute(app.state, rid)
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"
    assert "old-key" not in str(captured) and "new-key" not in str(captured)


def test_source_deleted_during_chat_prevents_completed_reference(runtime_env):
    from app.runtime.engine import execute

    client, app, headers, ids, _, doc, _ = prepare(runtime_env)
    cid = conversation(client, headers, ids)
    rid = start(client, headers, cid).json()["id"]
    captured = []
    delegate = transport(captured)

    def handler(request):
        if request.url.path.endswith("/chat/completions"):
            admin_headers = login(client)
            assert (
                client.delete(
                    f"/api/v1/admin/documents/{doc['id']}", headers=admin_headers
                ).status_code
                == 204
            )
            login(client, "member")
        return delegate.handle_request(request)

    app.state.http_transport = httpx.MockTransport(handler)
    execute(app.state, rid)
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "failed"
    assert len(client.get(f"/api/v1/conversations/{cid}/messages").json()) == 1


def test_debug_retrieval_is_independent_of_member_grant(runtime_env):
    from app.runtime.engine import execute

    client, app, _, ids, _, _, _ = prepare(runtime_env)
    headers = login(client)
    captured = []
    app.state.http_transport = transport(captured)
    response = client.post(
        f"/api/v1/admin/agents/{ids['agent']}/debug", headers=headers, json={"message": "question"}
    )
    rid = response.json()["id"]
    execute(app.state, rid)
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"
    assert "Important domain fact" in str(captured)


def test_owner_attachment_context_without_embedding_and_cleanup(runtime_env):
    from app.knowledge.indexing import index_document
    from app.knowledge.models import Citation, Document
    from app.runtime.engine import execute
    from sqlalchemy import select

    client, app, headers, ids, _ = runtime_env
    app.state.dispatch_index = lambda identifier: None
    cid = conversation(client, headers, ids)
    doc = client.post(
        "/api/v1/files",
        headers=headers,
        data={"conversation_id": cid},
        files={"file": ("owner.txt", b"Private owner facts")},
    ).json()
    index_document(app.state, doc["id"])
    captured = []
    app.state.http_transport = transport(captured)
    rid = start(client, headers, cid, attachment_ids=[doc["id"]]).json()["id"]
    execute(app.state, rid)
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"
    assert "Private owner facts" in str(captured)
    citation = client.get(f"/api/v1/conversations/{cid}/messages").json()[-1]["citations"][0]
    assert client.get(f"/api/v1/citations/{citation['id']}").status_code == 200
    assert client.delete(f"/api/v1/conversations/{cid}", headers=headers).status_code == 204
    with app.state.session_factory() as db:
        assert not db.scalars(select(Document)).all()
        assert not db.scalars(select(Citation)).all()
    from app.runtime.checkpoints import checkpointer

    with checkpointer(app.state) as saver:
        assert saver.get_tuple({"configurable": {"thread_id": rid}}) is None


def test_legacy_pending_run_without_knowledge_bindings_still_executes(runtime_env):
    from app.models import Run
    from app.runtime.engine import execute

    client, app, headers, ids, _ = runtime_env
    captured = []
    app.state.http_transport = transport(captured)
    cid = conversation(client, headers, ids)
    rid = start(client, headers, cid).json()["id"]
    with app.state.session_factory() as db:
        run = db.get(Run, rid)
        run.snapshot = {key: value for key, value in run.snapshot.items() if key != "knowledge"}
        db.commit()
    execute(app.state, rid)
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"


def test_deleted_source_is_not_reintroduced_in_future_model_history(runtime_env):
    from app.runtime.engine import execute

    client, app, headers, ids, _, doc, _ = prepare(runtime_env)
    captured = []
    app.state.http_transport = transport(captured)
    cid = conversation(client, headers, ids)
    first = start(client, headers, cid).json()["id"]
    execute(app.state, first)
    assert "Scoped answer" in client.get(f"/api/v1/conversations/{cid}/messages").text
    admin_headers = login(client)
    client.delete(f"/api/v1/admin/documents/{doc['id']}", headers=admin_headers)
    headers = login(client, "member")
    second = start(client, headers, cid).json()["id"]
    execute(app.state, second)
    assert len(captured) == 2
    assert "Scoped answer" not in str(captured[-1]["messages"])
    assert "Referenced answer is no longer available" in str(captured[-1]["messages"])
    assert client.get(f"/api/v1/runs/{second}").json()["status"] == "completed"


def test_new_publication_keeps_existing_run_knowledge_snapshot(runtime_env):
    from app.models import Agent
    from app.runtime.engine import execute

    client, app, headers, ids, _, _, _ = prepare(runtime_env)
    cid = conversation(client, headers, ids)
    rid = start(client, headers, cid).json()["id"]
    admin_headers = login(client)
    with app.state.session_factory() as db:
        agent = db.get(Agent, ids["agent"])
        agent.draft = {**agent.draft, "knowledge_base_ids": []}
        db.commit()
    client.post(f"/api/v1/admin/agents/{ids['agent']}/publish", headers=admin_headers)
    calls = []

    def handler(request):
        calls.append(request)
        return transport([]).handle_request(request)

    app.state.http_transport = httpx.MockTransport(handler)
    execute(app.state, rid)
    assert any(request.url.path.endswith("/embeddings") for request in calls)
    login(client, "member")
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"
