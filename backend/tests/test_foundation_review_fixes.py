import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from conftest import login
from fastapi import HTTPException, Request, Response


@pytest.mark.parametrize(("initial", "workers"), [(0, 12), (8, 2), (9, 4)])
def test_concurrent_login_failures_reserve_bounded_attempts(env, monkeypatch, initial, workers):
    from app.models import LoginThrottle
    from app.routers.auth import login as authenticate
    from app.schemas import Login
    from app.security import token_hash

    _, app = env
    key = token_hash("127.0.0.1:missing@example.test")
    if initial:
        with app.state.session_factory() as db:
            db.add(LoginThrottle(key=key, failures=initial))
            db.commit()
    start = threading.Barrier(workers)
    checks = []

    def verify(*args):
        checks.append(True)
        # Force overlapping reads/password work against independent DB sessions.
        time.sleep(0.15)
        return False

    monkeypatch.setattr("app.routers.auth.verify_password", verify)

    def attempt(_):
        request = Request({"type": "http", "app": app, "client": ("127.0.0.1", 12345)})
        with app.state.session_factory() as db:
            start.wait(timeout=10)
            try:
                authenticate(
                    Login(email="missing@example.test", password="wrong"), request, Response(), db
                )
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=workers) as pool:
        statuses = list(pool.map(attempt, range(workers)))
    admitted = min(workers, 10 - initial)
    assert statuses.count(401) == admitted
    assert statuses.count(429) == workers - admitted
    assert len(checks) == admitted
    with app.state.session_factory() as db:
        assert db.get(LoginThrottle, key).failures == min(initial + workers, 10)


def test_expired_login_window_resets_before_reserving_attempt(env):
    from app.models import LoginThrottle, utcnow
    from app.security import token_hash

    client, app = env
    key = token_hash("testclient:missing@example.test")
    with app.state.session_factory() as db:
        db.add(LoginThrottle(key=key, failures=10, window_start=utcnow() - timedelta(minutes=16)))
        db.commit()
    response = client.post(
        "/api/v1/auth/login", json={"email": "missing@example.test", "password": "wrong"}
    )
    assert response.status_code == 401
    with app.state.session_factory() as db:
        assert db.get(LoginThrottle, key).failures == 1


def test_successful_login_releases_its_reservation_without_erasing_failures(env):
    from app.models import LoginThrottle
    from app.security import token_hash

    client, app = env
    key = token_hash("testclient:admin@example.test")
    with app.state.session_factory() as db:
        db.add(LoginThrottle(key=key, failures=3))
        db.commit()
    login(client)
    with app.state.session_factory() as db:
        assert db.get(LoginThrottle, key).failures == 3


def test_model_delete_preserves_published_and_retained_version_references(env):
    from app.models import AgentVersion, ModelConnection
    from test_foundation_security import model_and_agent

    client, app = env
    headers = login(client)
    model_a, agent = model_and_agent(client, headers)
    first = client.post(f"/api/v1/admin/agents/{agent['id']}/publish", headers=headers).json()
    model_b = client.post(
        "/api/v1/admin/models",
        json={"name": "Other", "kind": "chat", "base_url": "http://localhost/v1", "model": "b"},
        headers=headers,
    ).json()
    draft_b = dict(agent["draft"], model_id=model_b["id"])
    client.patch(f"/api/v1/admin/agents/{agent['id']}", json={"draft": draft_b}, headers=headers)
    assert (
        client.delete(f"/api/v1/admin/models/{model_a['id']}", headers=headers).status_code == 409
    )
    client.post(f"/api/v1/admin/agents/{agent['id']}/publish", headers=headers)
    assert (
        client.delete(f"/api/v1/admin/models/{model_a['id']}", headers=headers).status_code == 409
    )
    rolled = client.post(
        f"/api/v1/admin/agents/{agent['id']}/rollback",
        json={"version_id": first["id"]},
        headers=headers,
    )
    assert rolled.status_code == 200
    assert rolled.json()["published_version_id"] == first["id"]
    with app.state.session_factory() as db:
        assert db.get(ModelConnection, model_a["id"]) is not None
        assert db.get(AgentVersion, first["id"]).snapshot["model_id"] == model_a["id"]
    assert (
        client.patch(
            f"/api/v1/admin/models/{model_a['id']}", json={"enabled": False}, headers=headers
        ).status_code
        == 200
    )


def test_init_admin_validation_never_outputs_password(monkeypatch, capsys):
    from app.cli import main

    password = "hidden-123"
    answers = iter(["admin@example.test", "Administrator"])
    monkeypatch.setattr(sys, "argv", ["app.cli", "init-admin"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr("app.cli.getpass.getpass", lambda _: password)
    try:
        main()
    except BaseException:
        # Include the traceback an uncaught CLI exception would send to the terminal.
        traceback.print_exc()
    output = capsys.readouterr()
    text = output.out + output.err
    assert password not in text
    assert "password" in text.lower()
    assert "12" in text
