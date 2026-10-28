from datetime import timedelta

import pytest
from sqlalchemy import select
from test_runtime import answer_transport, conversation, start


def test_recover_submitted_write_and_expired_approval(runtime_env):
    from app.models import Approval, Run, ToolCall, utcnow
    from app.runtime.engine import execute
    from app.runtime.recovery import recover
    from test_tools import install_tool, tool_transport

    client, app, _, ids, _ = runtime_env
    _, headers = install_tool(client, app, ids)
    calls = []
    app.state.http_transport = tool_transport(calls)
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    execute(app.state, rid)
    with app.state.session_factory() as db:
        approval = db.scalar(select(Approval).where(Approval.run_id == rid))
        approval.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    recover(app.state)
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "expired"
    rid2 = start(client, headers, conversation(client, headers, ids)).json()["id"]
    execute(app.state, rid2)
    with app.state.session_factory() as db:
        run = db.get(Run, rid2)
        run.status, run.lease_token = "running", "dead-worker"
        run.lease_expires_at = utcnow() - timedelta(seconds=1)
        call = db.scalar(select(ToolCall).where(ToolCall.run_id == rid2))
        call.status = "submitted"
        db.commit()
    recover(app.state)
    execute(app.state, rid2)
    assert client.get(f"/api/v1/runs/{rid2}").json()["status"] == "outcome_unknown"
    assert calls == []


def test_slot_denial_keeps_run_queued(runtime_env):
    from app.runtime.engine import execute

    client, app, headers, ids, _ = runtime_env
    captured = []
    app.state.http_transport = answer_transport(captured)

    class DeniedSlots:
        def acquire(self, owner, token):
            return False

        def release(self, owner, token):
            raise AssertionError("Unacquired slot released")

    app.state.execution_slots = DeniedSlots()
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    assert execute(app.state, rid) is False
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "queued"
    assert captured == []


def test_budget_exhausted_stops_model(runtime_env):
    from app.models import Run
    from app.runtime.engine import execute

    client, app, headers, ids, _ = runtime_env
    captured = []
    app.state.http_transport = answer_transport(captured)
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    with app.state.session_factory() as db:
        db.get(Run, rid).elapsed_seconds = 300
        db.commit()
    execute(app.state, rid)
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "failed"
    assert captured == []


def test_worker_configuration_and_sandbox_disabled():
    from app.config import Settings
    from app.worker import celery_app, execute_run, resume_run

    assert celery_app.conf.task_routes["app.worker.execute_run"]["queue"] == "runs"
    assert celery_app.conf.task_routes["app.knowledge.tasks.*"]["queue"] == "indexing"
    assert execute_run.name != resume_run.name
    assert celery_app.conf.beat_schedule
    with pytest.raises(ValueError):
        Settings(sandbox_enabled=True)


def test_submitted_write_exception_keeps_uncertainty(runtime_env):
    from app.models import ToolCall
    from app.runtime.engine import execute
    from sqlalchemy import event
    from test_tools import install_tool, tool_transport

    client, app, _, ids, _ = runtime_env
    _, headers = install_tool(client, app, ids, "write", "auto")
    calls = []
    app.state.http_transport = tool_transport(calls)
    # Simulate a real transaction failure while storing a successful upstream result.
    tripped = []

    def fail_result(session, context, instances):
        if not tripped and any(
            isinstance(obj, ToolCall) and obj.status == "succeeded" for obj in session.dirty
        ):
            tripped.append(True)
            raise RuntimeError("database temporarily unavailable")

    event.listen(app.state.session_factory, "before_flush", fail_result)
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    try:
        execute(app.state, rid)
    finally:
        event.remove(app.state.session_factory, "before_flush", fail_result)
    assert len(calls) == 1
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "outcome_unknown"
    execute(app.state, rid)
    assert len(calls) == 1


def test_recovery_clears_lease_after_crash_at_confirmation_boundary(runtime_env):
    from app.models import Run, utcnow
    from app.runtime.engine import execute
    from app.runtime.recovery import recover
    from test_tools import install_tool, tool_transport

    client, app, _, ids, _ = runtime_env
    _, headers = install_tool(client, app, ids)
    calls = []
    app.state.http_transport = tool_transport(calls)
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    execute(app.state, rid)
    aid = client.get(f"/api/v1/runs/{rid}").json()["approvals"][0]["id"]
    with app.state.session_factory() as db:
        run = db.get(Run, rid)
        run.lease_token = "dead-worker-before-finally"
        run.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert (
        client.post(
            f"/api/v1/approvals/{aid}/decision", json={"approved": True}, headers=headers
        ).status_code
        == 200
    )
    recover(app.state)
    execute(app.state, rid, resume=True)
    assert len(calls) == 1
    assert client.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"
