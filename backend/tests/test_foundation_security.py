import copy
from datetime import timedelta

import httpx
import pytest
from conftest import login
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy import select


def model_and_agent(client, headers):
    model = client.post(
        "/api/v1/admin/models",
        json={
            "name": "Local",
            "kind": "chat",
            "base_url": "http://localhost:12345/v1",
            "model": "local",
        },
        headers=headers,
    ).json()
    agent = client.post(
        "/api/v1/admin/agents",
        json={
            "name": "Agent",
            "draft": {"model_id": model["id"], "system_prompt": "Private instructions"},
        },
        headers=headers,
    ).json()
    return model, agent


def test_production_settings_reject_missing_and_weak_secrets():
    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(app_env="production", secret_key="", encryption_key="")
    with pytest.raises(ValidationError):
        Settings(
            app_env="production", secret_key="x" * 64, encryption_key=Fernet.generate_key().decode()
        )


def test_login_is_generic_and_throttled_and_cookie_secure(env):
    client, _ = env
    for _ in range(10):
        response = client.post(
            "/api/v1/auth/login", json={"email": "missing@example.test", "password": "wrong"}
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid email or password"}
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "missing@example.test", "password": "wrong"}
        ).status_code
        == 429
    )
    response = client.post(
        "/api/v1/auth/login", json={"email": "admin@example.test", "password": "admin-password"}
    )
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_session_expiration_and_group_change_revocation(env):
    from app.models import LoginSession, utcnow

    client, app = env
    login(client)
    with app.state.session_factory() as db:
        session = db.scalar(select(LoginSession))
        session.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/v1/auth/me").status_code == 401
    headers = login(client)
    member = next(x for x in client.get("/api/v1/admin/users").json() if x["role"] == "member")
    from fastapi.testclient import TestClient

    with TestClient(app) as other:
        login(other, "member")
        client.patch(f"/api/v1/admin/users/{member['id']}", json={"active": False}, headers=headers)
        assert other.get("/api/v1/auth/me").status_code == 401


def test_import_rolls_back_partial_work_when_reference_invalid(env):
    client, _ = env
    headers = login(client)
    response = client.post(
        "/api/v1/admin/users/import",
        json={
            "users": [
                {
                    "email": "first@example.test",
                    "display_name": "First",
                    "password": "a-long-password",
                },
                {
                    "email": "second@example.test",
                    "display_name": "Second",
                    "password": "a-long-password",
                    "group_ids": ["00000000-0000-0000-0000-000000000001"],
                },
            ]
        },
        headers=headers,
    )
    assert response.status_code == 404
    assert len(client.get("/api/v1/admin/users").json()) == 2


def test_model_url_validation_redaction_and_no_redirects(env, monkeypatch):
    from app.models import ModelConnection
    from app.security import decrypt_secret

    client, app = env
    headers = login(client)
    payload = {"name": "Local", "kind": "chat", "base_url": "file:///tmp", "model": "local"}
    assert client.post("/api/v1/admin/models", json=payload, headers=headers).status_code == 422
    payload["base_url"] = "http://user:password@localhost/v1"
    assert client.post("/api/v1/admin/models", json=payload, headers=headers).status_code == 422
    payload.update(base_url="http://localhost/v1", api_key="private-secret")
    model = client.post("/api/v1/admin/models", json=payload, headers=headers).json()
    with app.state.session_factory() as db:
        assert (
            decrypt_secret(
                db.get(ModelConnection, model["id"]).api_key_encrypted, app.state.settings
            )
            == "private-secret"
        )
    real_client = httpx.AsyncClient

    def upstream(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        kwargs.pop("transport", None)

        def handler(request):
            assert request.headers["authorization"] == "Bearer private-secret"
            return httpx.Response(500, json={"error": "private-secret"})

        return real_client(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("app.knowledge.embedding.httpx.AsyncClient", upstream)
    response = client.post(f"/api/v1/admin/models/{model['id']}/test", headers=headers)
    assert response.json()["ok"] is False
    assert response.json()["message"] == "Connection test failed"
    assert "private-secret" not in client.get("/api/v1/admin/audit").text


def test_publish_rejects_disabled_embedding_and_missing_resources(env):
    client, _ = env
    headers = login(client)
    model, agent = model_and_agent(client, headers)
    for changes in ({"enabled": False}, {"enabled": True, "kind": "embedding"}):
        client.patch(f"/api/v1/admin/models/{model['id']}", json=changes, headers=headers)
        assert (
            client.post(f"/api/v1/admin/agents/{agent['id']}/publish", headers=headers).status_code
            == 422
        )
    client.patch(f"/api/v1/admin/models/{model['id']}", json={"kind": "chat"}, headers=headers)
    draft = dict(agent["draft"], tool_ids=["00000000-0000-0000-0000-000000000001"])
    client.patch(f"/api/v1/admin/agents/{agent['id']}", json={"draft": draft}, headers=headers)
    assert (
        client.post(f"/api/v1/admin/agents/{agent['id']}/publish", headers=headers).status_code
        == 422
    )


def test_versions_are_immutable_and_rollback_revalidates(env):
    from app.models import AgentVersion

    client, app = env
    headers = login(client)
    model, agent = model_and_agent(client, headers)
    version = client.post(f"/api/v1/admin/agents/{agent['id']}/publish", headers=headers).json()
    with app.state.session_factory() as db:
        item = db.get(AgentVersion, version["id"])
        item.snapshot = dict(copy.deepcopy(item.snapshot), system_prompt="Changed")
        with pytest.raises(ValueError, match="immutable"):
            db.commit()
    draft = dict(agent["draft"], system_prompt="New")
    client.patch(f"/api/v1/admin/agents/{agent['id']}", json={"draft": draft}, headers=headers)
    next_version = client.post(
        f"/api/v1/admin/agents/{agent['id']}/publish", headers=headers
    ).json()
    assert next_version["version"] == 2
    rolled = client.post(
        f"/api/v1/admin/agents/{agent['id']}/rollback",
        json={"version_id": version["id"]},
        headers=headers,
    )
    assert rolled.json()["draft"]["system_prompt"] == "Private instructions"
    client.patch(f"/api/v1/admin/models/{model['id']}", json={"enabled": False}, headers=headers)
    assert (
        client.post(
            f"/api/v1/admin/agents/{agent['id']}/rollback",
            json={"version_id": next_version["id"]},
            headers=headers,
        ).status_code
        == 422
    )


def test_group_deletion_cleans_grants_and_unknown_api_never_serves_spa(env):
    client, _ = env
    headers = login(client)
    group = client.post("/api/v1/admin/groups", json={"name": "Readers"}, headers=headers).json()
    _, agent = model_and_agent(client, headers)
    client.patch(
        f"/api/v1/admin/agents/{agent['id']}",
        json={"allowed_group_ids": [group["id"]]},
        headers=headers,
    )
    assert client.delete(f"/api/v1/admin/groups/{group['id']}", headers=headers).status_code == 204
    assert client.get(f"/api/v1/admin/agents/{agent['id']}").json()["allowed_group_ids"] == []
    assert client.get("/api/v1/missing").status_code == 404
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200


def test_model_test_rejects_malformed_upstream_without_internal_error(env, monkeypatch):
    client, _ = env
    headers = login(client)
    model, _ = model_and_agent(client, headers)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.knowledge.embedding.httpx.AsyncClient",
        lambda **kwargs: real_client(
            **{key: value for key, value in kwargs.items() if key != "transport"},
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
        ),
    )
    response = client.post(f"/api/v1/admin/models/{model['id']}/test", headers=headers)
    assert response.json()["ok"] is False
    assert response.json()["message"] == "Connection test failed"


def test_validation_errors_never_echo_credentials(env):
    client, _ = env
    headers = login(client)
    response = client.post(
        "/api/v1/admin/models",
        json={
            "name": "Local",
            "kind": "chat",
            "base_url": "http://localhost/v1",
            "model": "local",
            "api_key": {"token": "private-secret"},
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert "private-secret" not in response.text
