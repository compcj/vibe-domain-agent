import secrets
from datetime import UTC
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Agent, LoginSession, User, utcnow
from app.security import csrf_token, token_hash

DB = Annotated[Session, Depends(get_db)]


def get_current_user(request: Request, db: DB) -> User:
    token = request.cookies.get("vda_session", "")
    session = db.scalar(select(LoginSession).where(LoginSession.token_hash == token_hash(token)))
    if not session or session.expires_at.replace(tzinfo=UTC) <= utcnow():
        raise HTTPException(401, "Authentication required")
    user = db.get(User, session.user_id)
    if not user or not user.active:
        raise HTTPException(401, "Authentication required")
    request.state.login_session = session
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_csrf(request: Request, user: CurrentUser) -> User:
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        expected = csrf_token(
            request.cookies.get("vda_session", ""), request.app.state.settings.secret_key
        )
        if not secrets.compare_digest(expected, request.headers.get("X-CSRF-Token", "")):
            raise HTTPException(403, "Invalid CSRF token")
    return user


def require_admin(user: Annotated[User, Depends(require_csrf)]) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Administrator access required")
    return user


Admin = Annotated[User, Depends(require_admin)]


def can_use_agent(user: User, agent: Agent) -> bool:
    return bool(
        user.active
        and agent.enabled
        and agent.published_version_id
        and set(user.group_ids).intersection(agent.allowed_group_ids)
    )
