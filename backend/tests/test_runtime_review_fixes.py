import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from conftest import login
from sqlalchemy import select
from test_runtime import answer_transport, conversation, start
from test_tools import install_tool, tool_transport


@pytest.fixture(params=["sqlite", "postgres"])
def race_env(request, runtime_env):
    if request.param == "sqlite":
        yield runtime_env
        return
    if not os.getenv("TEST_POSTGRES_URL"):
        pytest.skip("TEST_POSTGRES_URL is not configured")
    from app.config import Settings
    from app.db import make_engine
    from app.main import create_app
    from app.models import Base, User
    from app.runtime.checkpoints import initialize_checkpoints
    from app.security import hash_password
    from conftest import runtime_env as seed_runtime
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.schema import CreateSchema, DropSchema

    schema = "runtime_races_" + uuid4().hex
    base = make_engine(os.environ["TEST_POSTGRES_URL"])
    with base.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
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
    settings = Settings(
        **{
            **runtime_env[1].state.settings.model_dump(),
            "database_url": url.render_as_string(hide_password=False),
        }
    )
    app = create_app(settings)
    try:
        Base.metadata.create_all(app.state.engine)
        initialize_checkpoints(settings)
        with app.state.session_factory() as db:
            for role in ("admin", "member"):
                db.add(
                    User(
                        email=f"{role}@example.test",
                        display_name=role,
                        role=role,
                        password_hash=hash_password(f"{role}-password"),
                    )
                )
            db.commit()
        with TestClient(app) as client:
            yield seed_runtime.__wrapped__((client, app))
    finally:
        app.state.engine.dispose()
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()


def separate(fn):
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn).result(timeout=10)


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_snapshot_binds_model_and_tool_credentials(runtime_env, monkeypatch, transport):
    from app.models import ModelConnection, Run, ToolDefinition
    from app.runtime.engine import execute
    from app.security import encrypt_secret

    client, app, _, ids, _ = runtime_env
    tid, headers = install_tool(client, app, ids, "write", "auto")
    with app.state.session_factory() as db:
        db.get(ModelConnection, ids["model"]).api_key_encrypted = encrypt_secret(
            "old-model-key", app.state.settings
        )
        db.get(ToolDefinition, tid).transport = transport
        db.commit()
    cid = conversation(client, headers, ids)
    rid = start(client, headers, cid).json()["id"]
    with app.state.session_factory() as db:
        model = db.get(ModelConnection, ids["model"])
        model.base_url = "http://replacement.test/v1"
        model.api_key_encrypted = encrypt_secret("replacement-model-key", app.state.settings)
        tool = db.get(ToolDefinition, tid)
        tool.endpoint = "http://replacement.test/tool"
        tool.headers_encrypted = encrypt_secret(
            json.dumps({"Authorization": "Bearer replacement-tool-key"}), app.state.settings
        )
        db.commit()
        snapshot = json.dumps(db.get(Run, rid).snapshot)
        assert "old-model-key" not in snapshot and "tool-secret" not in snapshot
    captured = []
    base = tool_transport(captured)

    def handler(request):
        if request.url.host == "model.test":
            assert request.headers["authorization"] == "Bearer old-model-key"
        return base.handle_request(request)

    app.state.http_transport = httpx.MockTransport(handler)
    if transport == "mcp":

        async def mcp(endpoint, headers, *args):
            assert endpoint == "http://tools.test/action"
            assert headers == {"Authorization": "Bearer tool-secret"}
            captured.append(endpoint)
            return "found"

        monkeypatch.setattr("app.runtime.transports.mcp_session", mcp)
    execute(app.state, rid)
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"
    assert len(captured) == 1
    if transport == "http":
        assert captured[0].headers["authorization"] == "Bearer tool-secret"
    from app.runtime.checkpoints import checkpointer

    with checkpointer(app.state) as saver:
        graph = str(saver.get_tuple({"configurable": {"thread_id": rid}}))
        assert "old-model-key" not in graph and "tool-secret" not in graph
    for path in [f"runs/{rid}", f"runs/{rid}/events", f"conversations/{cid}/messages"]:
        assert "replacement-model-key" not in client.get("/api/v1/" + path).text


@pytest.mark.parametrize("resolve_first", [False, True])
@pytest.mark.parametrize("late_error", [False, True])
def test_late_worker_cannot_change_recovered_or_resolved_call(race_env, resolve_first, late_error):
    from app.models import Run, ToolCall, utcnow
    from app.runtime.engine import execute
    from app.runtime.recovery import recover

    client, app, _, ids, _ = race_env
    _, headers = install_tool(client, app, ids, "write", "auto")
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    captured = []
    base = tool_transport(captured)
    frozen = []

    def interrupted(request):
        if request.url.host == "tools.test":

            def recovery():
                with app.state.session_factory() as db:
                    db.get(Run, rid).lease_expires_at = utcnow() - timedelta(seconds=1)
                    db.commit()
                recover(app.state)
                if resolve_first:
                    admin = login(client)
                    record = client.get("/api/v1/admin/tool-calls").json()[0]
                    assert (
                        client.post(
                            f"/api/v1/admin/tool-calls/{record['id']}/resolve",
                            json={"outcome": "failed", "note": "verified"},
                            headers=admin,
                        ).status_code
                        == 200
                    )
                with app.state.session_factory() as db:
                    run = db.get(Run, rid)
                    frozen.append((run.status, run.next_event_seq, run.elapsed_seconds))

            separate(recovery)
            if late_error:
                raise httpx.ReadTimeout("late private error")
        return base.handle_request(request)

    app.state.http_transport = httpx.MockTransport(interrupted)
    execute(app.state, rid)
    with app.state.session_factory() as db:
        run = db.get(Run, rid)
        call = db.scalar(select(ToolCall).where(ToolCall.run_id == rid))
        assert call.status == ("failed" if resolve_first else "outcome_unknown")
        assert (run.status, run.next_event_seq, run.elapsed_seconds) == frozen[0]


def test_cancel_refreshes_stale_queued_run_after_worker_submission(race_env):
    from app.models import Run, ToolCall, User
    from app.routers.conversations import cancel
    from app.runtime.engine import execute
    from app.runtime.recovery import recover

    client, app, _, ids, _ = race_env
    _, headers = install_tool(client, app, ids, "write", "auto")
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    with app.state.session_factory() as cancellation_db:
        stale = cancellation_db.get(Run, rid)
        user = cancellation_db.get(User, ids["user"])
        assert stale.status == "queued"
        base = tool_transport([])
        cancellation_status = []

        def handler(request):
            if request.url.host == "tools.test":
                response = separate(lambda: cancel(rid, cancellation_db, user))
                cancellation_status.append(response["status"])
                raise httpx.ReadTimeout("submitted write crashed")
            return base.handle_request(request)

        app.state.http_transport = httpx.MockTransport(handler)
        execute(app.state, rid)
        assert cancellation_status == ["running"]
    with app.state.session_factory() as db:
        run = db.get(Run, rid)
        assert run.status == "outcome_unknown"
        # Legacy inconsistent terminal state must not hide a submitted write.
        run.status, run.lease_token, run.lease_expires_at = "cancelled", None, None
        db.scalar(select(ToolCall).where(ToolCall.run_id == rid)).status = "submitted"
        db.commit()
    recover(app.state)
    with app.state.session_factory() as db:
        assert db.get(Run, rid).status == "outcome_unknown"


def test_sqlite_checkpointer_closes_connection(runtime_env, monkeypatch):
    from app.runtime.checkpoints import checkpointer

    _, app, _, _, _ = runtime_env
    original = sqlite3.connect
    closed = []

    class Connection(sqlite3.Connection):
        def close(self):
            closed.append(True)
            super().close()

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: original(*a, **kw, factory=Connection))
    with checkpointer(app.state):
        pass
    assert closed == [True]


@pytest.mark.parametrize("mode", ["headers", "drip"])
@pytest.mark.parametrize("limit", ["run", "tool"])
def test_http_tool_has_overall_deadline_and_renews_lease(runtime_env, mode, limit):
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from app.models import Run, utcnow
    from app.runtime import budget
    from app.runtime.transports import TransportFailure, invoke

    _, app, headers, ids, _ = runtime_env
    client = runtime_env[0]
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    with app.state.session_factory() as db:
        run = db.get(Run, rid)
        run.status, run.lease_token = "running", "deadline-worker"
        run.lease_expires_at = utcnow() + timedelta(seconds=90)
        before = run.lease_expires_at
        db.commit()
    effects = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            effects.append(True)
            try:
                if mode == "headers":
                    time.sleep(0.7)
                self.send_response(200)
                self.send_header("Content-Length", "12")
                self.end_headers()
                for _ in range(12):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.08)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    token = budget.lease_token.set("deadline-worker")
    accounted = budget.accounted_at.set(time.monotonic() - 6)
    deadline = budget.deadline.set(time.monotonic() + (0.3 if limit == "run" else 2))
    # Runtime transport receives the run identity without exposing it to remote payloads.
    run_context = getattr(budget, "run_id", None)
    context = run_context.set(rid) if run_context else None
    started = time.monotonic()
    try:
        with pytest.raises(TransportFailure):
            invoke(
                app.state,
                {
                    "transport": "http",
                    "endpoint": f"http://127.0.0.1:{server.server_port}/",
                    "method": "POST",
                    "timeout_seconds": 1 if limit == "run" else 0.3,
                    "idempotency_header": None,
                },
                {},
                {},
                "key",
            )
        elapsed = time.monotonic() - started
        assert elapsed < 0.6
        assert effects == [True]
        with app.state.session_factory() as db:
            assert db.get(Run, rid).lease_expires_at.replace(tzinfo=before.tzinfo) > before
    finally:
        if run_context:
            run_context.reset(context)
        budget.deadline.reset(deadline)
        budget.accounted_at.reset(accounted)
        budget.lease_token.reset(token)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("missing", ["model", "tool"])
def test_legacy_snapshot_without_secret_binding_fails_closed(runtime_env, missing):
    from app.models import Run
    from app.runtime.engine import execute

    client, app, _, ids, _ = runtime_env
    _, headers = install_tool(client, app, ids, "write", "auto")
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    with app.state.session_factory() as db:
        run = db.get(Run, rid)
        snapshot = json.loads(json.dumps(run.snapshot))
        if missing == "model":
            snapshot["model"].pop("api_key_encrypted")
        else:
            snapshot["tools"][0].pop("headers_encrypted")
        run.snapshot = snapshot
        db.commit()
    calls = []
    app.state.http_transport = tool_transport(calls)
    execute(app.state, rid)
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "failed"
    assert calls == []


def test_expired_worker_cannot_renew_or_clear_its_lease(runtime_env):
    import time

    from app.models import Run, utcnow
    from app.runtime import budget
    from app.runtime.engine import execute
    from app.runtime.provider import SafeFailure
    from app.runtime.recovery import recover

    client, app, headers, ids, _ = runtime_env
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    observed = []
    base = answer_transport([])

    def handler(request):
        with app.state.session_factory() as db:
            run = db.get(Run, rid)
            run.lease_expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
            observed.append(run.lease_token)
        context = budget.accounted_at.set(time.monotonic() - 6)
        try:
            with pytest.raises(SafeFailure):
                budget.heartbeat(app.state, rid, force=True)
        finally:
            budget.accounted_at.reset(context)
        return base.handle_request(request)

    app.state.http_transport = httpx.MockTransport(handler)
    execute(app.state, rid)
    with app.state.session_factory() as db:
        run = db.get(Run, rid)
        assert run.lease_token == observed[0]
        assert run.status == "running"
    recover(app.state)
    with app.state.session_factory() as db:
        assert db.get(Run, rid).status == "resume_queued"


def test_heartbeat_runs_while_tool_response_headers_are_pending(runtime_env, monkeypatch):
    import asyncio

    from app.models import Run
    from app.runtime import transports
    from app.runtime.engine import execute

    client, app, _, ids, _ = runtime_env
    _, headers = install_tool(client, app, ids, "write", "auto")
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    observations = []
    base = tool_transport([])

    class PendingTransport(httpx.MockTransport):
        async def handle_async_request(self, request):
            with app.state.session_factory() as db:
                before = db.get(Run, rid).elapsed_seconds
            await asyncio.sleep(0.12)
            with app.state.session_factory() as db:
                after = db.get(Run, rid).elapsed_seconds
            observations.append(after - before)
            return base.handle_request(request)

    monkeypatch.setattr(transports, "HEARTBEAT_INTERVAL", 0.02)
    app.state.http_transport = PendingTransport(base.handle_request)
    execute(app.state, rid)
    assert observations and observations[0] > 0.07
    with app.state.session_factory() as db:
        assert db.get(Run, rid).status == "completed"


def test_cancel_orphan_submitted_write_retains_uncertainty(runtime_env):
    from app.models import Run, ToolCall
    from app.runtime.engine import execute

    client, app, _, ids, _ = runtime_env
    _, headers = install_tool(client, app, ids)
    app.state.http_transport = tool_transport([])
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    execute(app.state, rid)
    with app.state.session_factory() as db:
        db.get(Run, rid).status = "queued"
        db.scalar(select(ToolCall).where(ToolCall.run_id == rid)).status = "submitted"
        db.commit()
    response = client.post(f"/api/v1/runs/{rid}/cancel", headers=headers)
    assert response.json()["status"] == "outcome_unknown"
    with app.state.session_factory() as db:
        assert db.scalar(select(ToolCall).where(ToolCall.run_id == rid)).status == "outcome_unknown"


def test_model_partial_sse_frames_renew_lease_before_newline(runtime_env):
    from app.models import Run
    from app.runtime import budget
    from app.runtime.engine import execute

    client, app, headers, ids, _ = runtime_env
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    renewed = []

    class Partial(httpx.SyncByteStream):
        def __iter__(self):
            budget.accounted_at.set(budget.time.monotonic() - 6)
            yield b"data: "
            with app.state.session_factory() as db:
                renewed.append(db.get(Run, rid).elapsed_seconds > 5)
            yield b'{"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'

    app.state.http_transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=Partial())
    )
    execute(app.state, rid)
    assert renewed == [True]
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"
