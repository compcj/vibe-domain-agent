from conftest import login


def test_authentication_rbac_and_csrf(env):
    client, _ = env
    assert client.get("/api/v1/admin/users").status_code == 401
    login(client, "member")
    assert client.get("/api/v1/admin/users").status_code == 403
    assert client.post("/api/v1/auth/logout").status_code == 403
    headers = login(client)
    assert client.post("/api/v1/admin/groups", json={"name": "A"}).status_code == 403
    assert (
        client.post("/api/v1/admin/groups", json={"name": "A"}, headers=headers).status_code == 201
    )


def test_import_is_atomic_and_last_admin_is_protected(env):
    client, _ = env
    headers = login(client)
    payload = {"email": "new@example.test", "display_name": "New", "password": "a-long-password"}
    response = client.post(
        "/api/v1/admin/users/import", json={"users": [payload, payload]}, headers=headers
    )
    assert response.status_code == 409
    users = client.get("/api/v1/admin/users").json()
    assert len(users) == 2
    admin = next(x for x in users if x["role"] == "admin")
    assert (
        client.patch(
            f"/api/v1/admin/users/{admin['id']}", json={"role": "member"}, headers=headers
        ).status_code
        == 409
    )


def test_encrypted_models_and_publish_member_allowlist(env):
    from app.models import ModelConnection

    client, app = env
    headers = login(client)
    group = client.post("/api/v1/admin/groups", json={"name": "Readers"}, headers=headers).json()
    model = client.post(
        "/api/v1/admin/models",
        json={
            "name": "Local",
            "kind": "chat",
            "base_url": "http://localhost:8001/v1",
            "model": "local",
            "api_key": "secret-api-value",
        },
        headers=headers,
    )
    assert model.status_code == 201, model.text
    model = model.json()
    assert model["has_api_key"] and "api_key" not in model
    with app.state.session_factory() as db:
        stored = db.get(ModelConnection, model["id"])
        assert "secret-api-value" not in stored.api_key_encrypted
    draft = {"model_id": model["id"], "system_prompt": "Private instructions"}
    agent = client.post(
        "/api/v1/admin/agents",
        json={"name": "Domain", "allowed_group_ids": [group["id"]], "draft": draft},
        headers=headers,
    ).json()
    published = client.post(f"/api/v1/admin/agents/{agent['id']}/publish", headers=headers)
    assert published.status_code == 201, published.text
    version = published.json()
    draft["system_prompt"] = "Changed instructions"
    assert (
        client.patch(
            f"/api/v1/admin/agents/{agent['id']}", json={"draft": draft}, headers=headers
        ).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/admin/agents/{agent['id']}/versions").json()[0]["snapshot"][
            "system_prompt"
        ]
        == "Private instructions"
    )
    member = next(x for x in client.get("/api/v1/admin/users").json() if x["role"] == "member")
    login(client, "member")
    assert client.get("/api/v1/agents").json() == []
    headers = login(client)
    client.patch(
        f"/api/v1/admin/users/{member['id']}", json={"group_ids": [group["id"]]}, headers=headers
    )
    login(client, "member")
    agents = client.get("/api/v1/agents").json()
    assert agents == [{"id": agent["id"], "name": "Domain", "description": "", "examples": []}]
    assert version["id"] != ""


def test_publication_validates_model_and_limits(env):
    client, _ = env
    headers = login(client)
    agent = client.post(
        "/api/v1/admin/agents",
        json={"name": "Invalid", "draft": {"system_prompt": "Prompt"}},
        headers=headers,
    ).json()
    assert (
        client.post(f"/api/v1/admin/agents/{agent['id']}/publish", headers=headers).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/v1/admin/agents/{agent['id']}",
            json={"draft": {"max_steps": 21}},
            headers=headers,
        ).status_code
        == 422
    )


def test_password_change_revokes_sessions(env):
    client, app = env
    headers = login(client)
    from fastapi.testclient import TestClient

    other = TestClient(app)
    login(other)
    response = client.post(
        "/api/v1/auth/password",
        json={"current_password": "admin-password", "new_password": "replacement-password"},
        headers=headers,
    )
    assert response.status_code == 204
    assert other.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401
