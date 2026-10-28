from datetime import timedelta
from uuid import UUID

import pytest
from conftest import login
from fastapi import Request
from sqlalchemy import select
from test_runtime import conversation, start


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalidation", ["logout", "password", "reset", "expiry", "owner", "other_session_logout"]
)
async def test_established_stream_rechecks_its_login_session(runtime_env, invalidation):
    from app.dependencies import get_current_user
    from app.models import LoginSession, Run, User, utcnow
    from app.routers.conversations import events
    from app.runtime.store import event

    client, app, headers, ids, dispatched = runtime_env
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    token = client.cookies.get("vda_session")

    async def receive():
        return {"type": "http.request", "body": b""}

    request = Request(
        {
            "type": "http",
            "app": app,
            "method": "GET",
            "headers": [(b"cookie", f"vda_session={token}".encode())],
        },
        receive=receive,
    )
    # Use the real authentication dependency; the iterator outlives its request DB.
    with app.state.session_factory() as db:
        user = get_current_user(request, db)
        session_id = request.state.login_session.id
        response = events(UUID(rid), request, db, user)
    iterator = response.body_iterator
    try:
        assert "event: status" in await anext(iterator)
        assert await anext(iterator) == ": keepalive\n\n"
        if invalidation == "logout":
            assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
        elif invalidation == "password":
            assert (
                client.post(
                    "/api/v1/auth/password",
                    headers=headers,
                    json={
                        "current_password": "member-password",
                        "new_password": "replacement-password",
                    },
                ).status_code
                == 204
            )
        elif invalidation == "reset":
            admin_headers = login(client)
            assert (
                client.post(
                    f"/api/v1/admin/users/{ids['user']}/reset-password",
                    headers=admin_headers,
                    json={"password": "replacement-password"},
                ).status_code
                == 204
            )
        elif invalidation == "other_session_logout":
            other_headers = login(client, "member")
            assert client.post("/api/v1/auth/logout", headers=other_headers).status_code == 204
        else:
            with app.state.session_factory() as db:
                login_session = db.get(LoginSession, session_id)
                if invalidation == "expiry":
                    login_session.expires_at = utcnow() - timedelta(seconds=1)
                else:
                    login_session.user_id = db.scalar(select(User.id).where(User.role == "admin"))
                db.commit()
        with app.state.session_factory() as db:
            event(db, rid, "delta", {"text": "private after invalidation"})
            db.commit()
        if invalidation == "other_session_logout":
            assert "private after invalidation" in await anext(iterator)
        else:
            with pytest.raises(StopAsyncIteration):
                await anext(iterator)
        # Revoking event access must not cancel/re-dispatch the legitimate task.
        with app.state.session_factory() as db:
            run = db.get(Run, rid)
            assert run.status == "queued" and not run.cancel_requested
        assert dispatched == [(rid, False)]
    finally:
        await iterator.aclose()
