import secrets
from datetime import UTC, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select

from app.dependencies import DB, CurrentUser, require_csrf
from app.models import LoginSession, LoginThrottle, User, utcnow
from app.schemas import AuthRead, Login, PasswordChange, UserRead
from app.security import DUMMY_HASH, audit, csrf_token, hash_password, token_hash, verify_password

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/login", response_model=AuthRead)
def login(data: Login, request: Request, response: Response, db: DB):
    email = data.email.strip().lower()
    key = token_hash(f"{request.client.host if request.client else 'unknown'}:{email}")
    throttle = db.get(LoginThrottle, key)
    now = utcnow()
    if throttle and throttle.window_start.replace(tzinfo=UTC) < now - timedelta(minutes=15):
        db.delete(throttle)
        db.flush()
        throttle = None
    if throttle and throttle.failures >= 10:
        raise HTTPException(429, "Too many login attempts; try again later")
    user = db.scalar(select(User).where(User.email == email))
    valid = verify_password(user.password_hash if user else DUMMY_HASH, data.password)
    if not user or not valid or not user.active:
        if not throttle:
            throttle = LoginThrottle(key=key, failures=0)
            db.add(throttle)
        throttle.failures += 1
        db.commit()
        raise HTTPException(401, "Invalid email or password")
    if throttle:
        db.delete(throttle)
    settings = request.app.state.settings
    token = secrets.token_urlsafe(48)
    db.add(
        LoginSession(
            user_id=user.id,
            token_hash=token_hash(token),
            expires_at=now + timedelta(hours=settings.session_hours),
        )
    )
    audit(db, user, "login", "session")
    db.commit()
    response.set_cookie(
        "vda_session",
        token,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="lax",
        max_age=settings.session_hours * 3600,
        path="/",
    )
    return {
        "user": UserRead.model_validate(user),
        "csrf_token": csrf_token(token, settings.secret_key),
    }


@router.get("/me", response_model=AuthRead)
def me(request: Request, user: CurrentUser):
    return {
        "user": UserRead.model_validate(user),
        "csrf_token": csrf_token(
            request.cookies["vda_session"], request.app.state.settings.secret_key
        ),
    }


@router.post("/logout", status_code=204)
def logout(
    request: Request, response: Response, db: DB, user: Annotated[User, Depends(require_csrf)]
):
    db.delete(request.state.login_session)
    db.commit()
    response.delete_cookie("vda_session", path="/")


@router.post("/password", status_code=204)
def password(
    data: PasswordChange, response: Response, db: DB, user: Annotated[User, Depends(require_csrf)]
):
    if not verify_password(user.password_hash, data.current_password):
        raise HTTPException(400, "Current password is incorrect")
    user.password_hash = hash_password(data.new_password)
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    audit(db, user, "password_change", "user", user.id)
    db.commit()
    response.delete_cookie("vda_session", path="/")
