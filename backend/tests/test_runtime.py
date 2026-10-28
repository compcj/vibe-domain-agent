import json
from concurrent.futures import ThreadPoolExecutor

import httpx
from conftest import login
from fastapi.testclient import TestClient
from sqlalchemy import select


def conversation(client, headers, ids):
    response = client.post(
        "/api/v1/conversations", json={"agent_id": ids["agent"]}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def start(client, headers, cid, **extra):
    return client.post(
        f"/api/v1/conversations/{cid}/runs", json={"message": "Hello", **extra}, headers=headers
    )


def test_run_snapshot_and_member_allowlist(runtime_env):
    from app.models import Agent, Run

    client, app, headers, ids, dispatched = runtime_env
    cid = conversation(client, headers, ids)
    response = start(client, headers, cid)
    assert response.status_code == 202, response.text
    rid = response.json()["id"]
    assert dispatched == [(rid, False)]
    assert client.get(f"/api/v1/conversations/{cid}").json()["active_run_id"] == rid
    with app.state.session_factory() as db:
        agent = db.get(Agent, ids["agent"])
        agent.draft = {**agent.draft, "system_prompt": "changed"}
        db.commit()
        assert db.get(Run, rid).snapshot["system_prompt"] == "immutable private prompt"
    payload = client.get(f"/api/v1/runs/{rid}").json()
    assert set(payload) == {"id", "status", "error", "approvals", "created_at"}
    assert "private prompt" not in json.dumps(payload)
    assert start(client, headers, cid).status_code == 409


def test_run_input_rejects_configuration_and_unvalidated_attachments(runtime_env):
    client, _, headers, ids, _ = runtime_env
    cid = conversation(client, headers, ids)
    assert start(client, headers, cid, system_prompt="hostile").status_code == 422
    assert start(client, headers, cid, attachment_ids=["anything"]).status_code == 422


def test_conversation_and_run_owner_checks(runtime_env):
    client, _, headers, ids, _ = runtime_env
    cid = conversation(client, headers, ids)
    rid = start(client, headers, cid).json()["id"]
    admin_headers = login(client)
    assert client.get("/api/v1/conversations").json() == []
    for path in [
        f"conversations/{cid}",
        f"conversations/{cid}/messages",
        f"runs/{rid}",
        f"runs/{rid}/events",
    ]:
        assert client.get(f"/api/v1/{path}").status_code == 404
    assert client.post(f"/api/v1/runs/{rid}/cancel", headers=admin_headers).status_code == 404


def test_concurrent_start_has_only_one_active_run(runtime_env):
    client, app, headers, ids, _ = runtime_env
    cid = conversation(client, headers, ids)
    cookies = dict(client.cookies)

    def submit(_):
        with TestClient(app) as other:
            other.cookies.update(cookies)
            return start(other, headers, cid).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, range(2))) == [202, 409]


def answer_transport(captured):
    def handler(request):
        captured.append(json.loads(request.content))
        body = (
            "\n\n".join(
                "data: " + json.dumps(item)
                for item in [
                    {"choices": [{"delta": {"content": "Hel"}}]},
                    {"choices": [{"delta": {"content": "lo"}}]},
                    {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
                ]
            )
            + "\n\ndata: [DONE]\n\n"
        )
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    return httpx.MockTransport(handler)


def test_actual_graph_streaming_persists_events_messages_and_replay(runtime_env):
    from app.models import Run
    from app.runtime.engine import execute

    client, app, headers, ids, _ = runtime_env
    captured = []
    app.state.http_transport = answer_transport(captured)
    cid = conversation(client, headers, ids)
    rid = start(client, headers, cid).json()["id"]
    execute(app.state, rid)
    execute(app.state, rid)  # duplicate delivery cannot create another model call
    assert len(captured) == 1
    assert captured[0]["messages"][0]["content"] == "immutable private prompt"
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"
    messages = client.get(f"/api/v1/conversations/{cid}/messages").json()
    assert [m["content"] for m in messages] == ["Hello", "Hello"]
    events = client.get(f"/api/v1/runs/{rid}/events").text
    assert '"text":"Hel"' in events and '"text":"lo"' in events
    assert "event: done" in events
    assert "private prompt" not in events
    with app.state.session_factory() as db:
        run = db.get(Run, rid)
        assert (run.input_tokens, run.output_tokens) == (5, 2)
    assert (
        client.post(
            f"/api/v1/messages/{messages[1]['id']}/feedback", json={"rating": 1}, headers=headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/messages/{messages[0]['id']}/feedback", json={"rating": 1}, headers=headers
        ).status_code
        == 422
    )
    assert client.delete(f"/api/v1/conversations/{cid}", headers=headers).status_code == 204


def test_cancel_prevents_model_execution(runtime_env):
    from app.runtime.engine import execute

    client, app, headers, ids, _ = runtime_env
    captured = []
    app.state.http_transport = answer_transport(captured)
    cid = conversation(client, headers, ids)
    rid = start(client, headers, cid).json()["id"]
    assert client.post(f"/api/v1/runs/{rid}/cancel", headers=headers).status_code == 200
    execute(app.state, rid)
    assert captured == []
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "cancelled"


def test_metrics_report_runtime_aggregates_only(runtime_env):
    from app.runtime.engine import execute

    client, app, headers, ids, _ = runtime_env
    app.state.http_transport = answer_transport([])
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    execute(app.state, rid)
    login(client)
    payload = client.get("/api/v1/admin/metrics").json()
    assert payload["runs"] == 1
    assert payload["run_status_counts"] == {"completed": 1}
    assert payload["input_tokens"] == 5
    assert payload["output_tokens"] == 2
    assert payload["tool_calls"] == 0
    assert "immutable private prompt" not in json.dumps(payload)


def test_checkpoint_resume_with_fresh_application(runtime_env):
    from app.main import create_app
    from app.runtime.engine import execute
    from test_tools import install_tool, tool_transport

    client, app, _, ids, _ = runtime_env
    _, headers = install_tool(client, app, ids)
    calls = []
    app.state.http_transport = tool_transport(calls)
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    execute(app.state, rid)
    aid = client.get(f"/api/v1/runs/{rid}").json()["approvals"][0]["id"]
    client.post(f"/api/v1/approvals/{aid}/decision", json={"approved": True}, headers=headers)
    fresh = create_app(app.state.settings)
    fresh.state.http_transport = tool_transport(calls)
    execute(fresh.state, rid, resume=True)
    assert len(calls) == 1
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"
    fresh.state.engine.dispose()


def test_sse_cursor_and_live_revocation(runtime_env):
    from app.models import ModelConnection
    from app.runtime.engine import execute

    client, app, headers, ids, _ = runtime_env
    app.state.http_transport = answer_transport([])
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    with app.state.session_factory() as db:
        db.get(ModelConnection, ids["model"]).enabled = False
        db.commit()
    execute(app.state, rid)
    payload = client.get(f"/api/v1/runs/{rid}").json()
    assert payload["status"] == "failed"
    response = client.get(f"/api/v1/runs/{rid}/events", headers={"Last-Event-ID": "1"})
    assert "id: 1\n" not in response.text
    assert "event: done" in response.text


def test_delta_is_persisted_before_provider_finishes(runtime_env):
    from app.models import RunEvent
    from app.runtime.engine import execute

    client, app, headers, ids, _ = runtime_env
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    observed = []

    class Incremental(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
            with app.state.session_factory() as db:
                observed.extend(
                    db.scalars(
                        select(RunEvent).where(RunEvent.run_id == rid, RunEvent.kind == "delta")
                    ).all()
                )
            yield b"data: [DONE]\n\n"

    app.state.http_transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=Incremental())
    )
    execute(app.state, rid)
    assert len(observed) == 1
    assert observed[0].data == {"text": "first"}


def test_provider_credential_echo_is_redacted_even_across_deltas(runtime_env):
    from app.models import ModelConnection
    from app.runtime.engine import execute
    from app.security import encrypt_secret

    client, app, headers, ids, _ = runtime_env
    with app.state.session_factory() as db:
        db.get(ModelConnection, ids["model"]).api_key_encrypted = encrypt_secret(
            "private-api-key", app.state.settings
        )
        db.commit()
    cid = conversation(client, headers, ids)
    rid = start(client, headers, cid).json()["id"]
    body = (
        "".join(
            "data: " + json.dumps({"choices": [{"delta": {"content": part}}]}) + "\n\n"
            for part in ["hello private-", "api-key world"]
        )
        + "data: [DONE]\n\n"
    )
    app.state.http_transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    execute(app.state, rid)
    assert "private-api-key" not in client.get(f"/api/v1/conversations/{cid}/messages").text
    events = client.get(f"/api/v1/runs/{rid}/events").text
    assert "private-api-key" not in events
    assert "[redacted]" in events


def test_context_citation_allowlist_and_resume_status(runtime_env):
    from app.runtime.dto import RunAccepted
    from app.runtime.engine import execute

    client, app, headers, ids, _ = runtime_env
    assert RunAccepted(id="test", status="resume_queued").status == "queued"
    app.state.http_transport = answer_transport([])
    app.state.runtime_context = lambda db, run: {
        "context": "reference",
        "citations": [
            {
                "id": "ref-1",
                "title": "Reference",
                "locator": "page 1",
                "excerpt": "excerpt",
                "private_config": "must-not-leak",
            }
        ],
    }
    cid = conversation(client, headers, ids)
    rid = start(client, headers, cid).json()["id"]
    execute(app.state, rid)
    assert "must-not-leak" not in client.get(f"/api/v1/conversations/{cid}/messages").text
    assert "must-not-leak" not in client.get(f"/api/v1/runs/{rid}/events").text
