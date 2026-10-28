import json
from datetime import timedelta

import httpx
import pytest
from conftest import login
from sqlalchemy import select
from test_runtime import answer_transport, conversation, start


def install_tool(client, app, ids, effect="write", policy=None):
    from app.models import Agent, AgentVersion, new_id

    headers = login(client)
    payload = dict(
        name="lookup",
        title="Lookup",
        transport="http",
        endpoint="http://tools.test/action",
        input_schema={
            "type": "object",
            "properties": {"item": {"type": "string"}, "tenant": {"type": "string"}},
            "required": ["item", "tenant"],
            "additionalProperties": False,
        },
        fixed_arguments={"tenant": "fixed"},
        headers={"Authorization": "Bearer tool-secret"},
        effect=effect,
        allowed_group_ids=[ids["group"]],
    )
    if policy:
        payload["policy"] = policy
    response = client.post("/api/v1/admin/tools", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    assert "tool-secret" not in response.text
    tid = response.json()["id"]
    with app.state.session_factory() as db:
        agent = db.get(Agent, ids["agent"])
        draft = {**agent.draft, "tool_ids": [tid]}
        agent.draft = draft
        version = AgentVersion(
            id=new_id(), agent_id=agent.id, version=2, snapshot=draft, created_by=ids["user"]
        )
        db.add(version)
        db.flush()
        agent.published_version_id = version.id
        db.commit()
    return tid, login(client, "member")


def tool_transport(captured, fail=False):
    def handler(request):
        if request.url.host == "tools.test":
            captured.append(request)
            if fail:
                raise httpx.ReadTimeout("secret upstream error")
            return httpx.Response(200, json={"found": True})
        data = json.loads(request.content)
        assert "tool-secret" not in request.content.decode()
        if any(m["role"] == "tool" for m in data["messages"]):
            return answer_transport([]).handle_request(request)
        delta = {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_1",
                    "function": {"name": "lookup", "arguments": '{"item":"x","tenant":"hostile"}'},
                }
            ]
        }
        return httpx.Response(
            200,
            text="data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\ndata: [DONE]\n\n",
        )

    return httpx.MockTransport(handler)


def test_confirm_checkpoint_resume_exactly_once(runtime_env):
    from app.models import ToolCall
    from app.runtime.engine import execute

    client, app, _, ids, dispatched = runtime_env
    _, headers = install_tool(client, app, ids)
    calls = []
    app.state.http_transport = tool_transport(calls)
    cid = conversation(client, headers, ids)
    rid = start(client, headers, cid).json()["id"]
    execute(app.state, rid)
    payload = client.get(f"/api/v1/runs/{rid}").json()
    assert payload["status"] == "awaiting_confirmation"
    assert calls == []
    approval = payload["approvals"][0]
    assert approval["parameters"] == {"item": "x", "tenant": "fixed"}
    aid = approval["id"]
    admin = login(client)
    assert (
        client.post(
            f"/api/v1/approvals/{aid}/decision", json={"approved": True}, headers=admin
        ).status_code
        == 404
    )
    headers = login(client, "member")
    assert (
        client.post(
            f"/api/v1/approvals/{aid}/decision", json={"approved": True}, headers=headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/approvals/{aid}/decision", json={"approved": True}, headers=headers
        ).status_code
        == 200
    )
    execute(app.state, rid, resume=True)
    execute(app.state, rid, resume=True)
    assert len(calls) == 1
    assert json.loads(calls[0].content) == approval["parameters"]
    assert calls[0].headers["Authorization"] == "Bearer tool-secret"
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"
    with app.state.session_factory() as db:
        assert db.scalar(select(ToolCall).where(ToolCall.run_id == rid)).status == "succeeded"


@pytest.mark.parametrize("effect,policy", [("read", None), ("write", "auto")])
def test_auto_tools(runtime_env, effect, policy):
    from app.runtime.engine import execute

    client, app, _, ids, _ = runtime_env
    _, headers = install_tool(client, app, ids, effect, policy)
    calls = []
    app.state.http_transport = tool_transport(calls)
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    execute(app.state, rid)
    assert len(calls) == 1
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"


@pytest.mark.parametrize("blocked", ["refused", "expired", "revoked", "hash"])
def test_confirmation_blockers(runtime_env, blocked):
    from app.models import Approval, ToolCall, ToolDefinition, utcnow
    from app.runtime.engine import execute

    client, app, _, ids, _ = runtime_env
    tid, headers = install_tool(client, app, ids)
    calls = []
    app.state.http_transport = tool_transport(calls)
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    execute(app.state, rid)
    aid = client.get(f"/api/v1/runs/{rid}").json()["approvals"][0]["id"]
    with app.state.session_factory() as db:
        approval = db.get(Approval, aid)
        if blocked == "expired":
            approval.expires_at = utcnow() - timedelta(seconds=1)
        if blocked == "hash":
            db.get(ToolCall, approval.tool_call_id).arguments = {"item": "changed"}
        if blocked == "revoked":
            db.get(ToolDefinition, tid).enabled = False
        db.commit()
    response = client.post(
        f"/api/v1/approvals/{aid}/decision",
        json={"approved": blocked != "refused"},
        headers=headers,
    )
    if blocked == "refused":
        assert response.status_code == 200
        execute(app.state, rid, resume=True)
    else:
        assert response.status_code in {403, 409}
    assert calls == []


def test_ambiguous_write_reconciles_without_reexecution(runtime_env):
    from app.runtime.engine import execute

    client, app, _, ids, _ = runtime_env
    _, headers = install_tool(client, app, ids, "write", "auto")
    calls = []
    app.state.http_transport = tool_transport(calls, fail=True)
    cid = conversation(client, headers, ids)
    rid = start(client, headers, cid).json()["id"]
    execute(app.state, rid)
    execute(app.state, rid)
    assert len(calls) == 1
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "outcome_unknown"
    admin = login(client)
    records = client.get("/api/v1/admin/tool-calls?status=outcome_unknown").json()
    assert len(records) == 1
    assert "arguments" not in records[0]
    response = client.post(
        f"/api/v1/admin/tool-calls/{records[0]['id']}/resolve",
        json={"outcome": "succeeded", "note": "Verified externally"},
        headers=admin,
    )
    assert response.status_code == 200
    execute(app.state, rid, resume=True)
    assert len(calls) == 1
    login(client, "member")
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"
    assert client.get(f"/api/v1/conversations/{cid}/messages").json()[-1]["role"] == "assistant"


def test_unknown_tool_cannot_execute(runtime_env):
    from app.runtime.engine import execute

    client, app, headers, ids, _ = runtime_env
    calls = []
    app.state.http_transport = tool_transport(calls)
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    execute(app.state, rid)
    assert calls == []
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "failed"


def test_tool_http_get_and_read_failure_is_recoverable(runtime_env):
    from app.runtime.engine import execute

    client, app, _, ids, _ = runtime_env
    tid, headers = install_tool(client, app, ids, "read")
    admin = login(client)
    assert (
        client.patch(
            f"/api/v1/admin/tools/{tid}", json={"method": "GET"}, headers=admin
        ).status_code
        == 200
    )
    # Publish the updated tool configuration in the next run snapshot.
    headers = login(client, "member")
    calls = []
    app.state.http_transport = tool_transport(calls, fail=True)
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    execute(app.state, rid)
    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert calls[0].url.params["tenant"] == "fixed"
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"


def test_tool_policy_validation_and_credential_storage(runtime_env):
    from app.models import ToolDefinition

    client, app, _, ids, _ = runtime_env
    tid, _ = install_tool(client, app, ids)
    admin = login(client)
    tool = client.get("/api/v1/admin/tools").json()[0]
    assert tool["policy"] == "confirm" and tool["has_headers"]
    assert "headers" not in tool
    with app.state.session_factory() as db:
        assert "tool-secret" not in db.get(ToolDefinition, tid).headers_encrypted
    for data in [
        {"transport": "stdio"},
        {"endpoint": "https://test/?key=secret"},
        {"input_schema": {"type": "object", "$ref": "http://evil.test/schema"}},
        {"sandbox_enabled": True},
    ]:
        assert (
            client.patch(f"/api/v1/admin/tools/{tid}", json=data, headers=admin).status_code == 422
        )
    assert client.delete(f"/api/v1/admin/tools/{tid}", headers=admin).status_code == 409


def test_delete_cascades_ledger_and_checkpoint(runtime_env):
    from app.models import Approval, Message, Run, RunEvent, ToolCall
    from app.runtime.checkpoints import checkpointer
    from app.runtime.engine import execute

    client, app, _, ids, _ = runtime_env
    _, headers = install_tool(client, app, ids, "write", "auto")
    app.state.http_transport = tool_transport([])
    cid = conversation(client, headers, ids)
    rid = start(client, headers, cid).json()["id"]
    execute(app.state, rid)
    assert client.delete(f"/api/v1/conversations/{cid}", headers=headers).status_code == 204
    with app.state.session_factory() as db:
        assert db.get(Run, rid) is None
        for cls in [ToolCall, Approval, Message, RunEvent]:
            assert db.scalar(select(cls).where(cls.run_id == rid)) is None
    with checkpointer(app.state) as saver:
        assert saver.get_tuple({"configurable": {"thread_id": rid}}) is None
