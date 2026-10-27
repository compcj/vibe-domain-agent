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
