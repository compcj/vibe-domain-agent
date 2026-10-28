import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path):
    from app.config import Settings
    from app.main import create_app
    from app.models import Base, User
    from app.security import hash_password

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        redis_url="",
        app_env="test",
        storage_dir=str(tmp_path / "files"),
        secret_key="x" * 64,
        encryption_key=Fernet.generate_key().decode(),
    )
    app = create_app(settings)
    Base.metadata.create_all(app.state.engine)
    with app.state.session_factory() as db:
        db.add_all(
            [
                User(
                    email="admin@example.test",
                    display_name="Admin",
                    role="admin",
                    password_hash=hash_password("admin-password"),
                ),
                User(
                    email="member@example.test",
                    display_name="Member",
                    role="member",
                    password_hash=hash_password("member-password"),
                ),
            ]
        )
        db.commit()
    with TestClient(app) as client:
        yield client, app
    app.state.engine.dispose()


def login(client, role="admin"):
    response = client.post(
        "/api/v1/auth/login", json={"email": f"{role}@example.test", "password": f"{role}-password"}
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


@pytest.fixture
def runtime_env(env):
    from app.models import Agent, AgentVersion, Group, ModelConnection, User
    from sqlalchemy import select

    client, app = env
    with app.state.session_factory() as db:
        group = Group(name="runtime")
        model = ModelConnection(
            name="fake", kind="chat", base_url="http://model.test/v1", model="fake"
        )
        db.add_all([group, model])
        db.flush()
        user = db.scalar(select(User).where(User.role == "member"))
        user.groups = [group]
        draft = {
            "model_id": model.id,
            "system_prompt": "immutable private prompt",
            "temperature": 0.7,
            "max_tokens": 100,
            "tool_ids": [],
            "knowledge_base_ids": [],
            "max_steps": 20,
            "timeout_seconds": 300,
        }
        agent = Agent(name="runtime", allowed_group_ids=[group.id], draft=draft)
        db.add(agent)
        db.flush()
        version = AgentVersion(agent_id=agent.id, version=1, snapshot=draft, created_by=user.id)
        db.add(version)
        db.flush()
        agent.published_version_id = version.id
        db.commit()
        ids = {"agent": agent.id, "group": group.id, "model": model.id, "user": user.id}
    dispatched = []
    app.state.dispatch_run = lambda run_id, resume=False: dispatched.append((run_id, resume))
    headers = login(client, "member")
    return client, app, headers, ids, dispatched
