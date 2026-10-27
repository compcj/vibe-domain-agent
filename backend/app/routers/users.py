from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import delete, select

from app.dependencies import DB, Admin
from app.models import Agent, Group, LoginSession, User
from app.schemas import (
    GroupInput,
    GroupPatch,
    GroupRead,
    PasswordReset,
    UserCreate,
    UserImport,
    UserPatch,
    UserRead,
)
from app.security import audit, hash_password

router = APIRouter(prefix="/admin", tags=["accounts"])


def get_object(db, model, identifier):
    obj = db.get(model, str(identifier))
    if obj is None:
        raise HTTPException(404, "Resource not found")
    return obj


def groups_for(db, identifiers):
    return [
        get_object(db, Group, identifier) for identifier in dict.fromkeys(map(str, identifiers))
    ]


def invalidate_sessions(db, user_id):
    db.execute(delete(LoginSession).where(LoginSession.user_id == user_id))


def protect_last_admin(db, user, changes):
    admins = db.scalars(
        select(User).where(User.active.is_(True), User.role == "admin").with_for_update()
    ).all()
    if (
        user.active
        and user.role == "admin"
        and (
            changes.get("active", user.active) is False or changes.get("role", user.role) != "admin"
        )
        and len(admins) <= 1
    ):
        raise HTTPException(409, "The last active administrator must be preserved")


def create_user(db, data):
    if db.scalar(select(User.id).where(User.email == data.email)):
        raise HTTPException(409, "Email is already in use")
    values = data.model_dump(exclude={"password", "group_ids"})
    user = User(
        **values, password_hash=hash_password(data.password), groups=groups_for(db, data.group_ids)
    )
    db.add(user)
    db.flush()
    return user


@router.get("/users", response_model=list[UserRead])
def users(db: DB, admin: Admin):
    return db.scalars(select(User).order_by(User.created_at)).all()


@router.post("/users", response_model=UserRead, status_code=201)
def add_user(data: UserCreate, db: DB, admin: Admin):
    user = create_user(db, data)
    audit(db, admin, "create", "user", user.id)
    db.commit()
    return user


@router.post("/users/import", response_model=list[UserRead], status_code=201)
def import_users(data: UserImport, db: DB, admin: Admin):
    emails = [user.email for user in data.users]
    if len(set(emails)) != len(emails):
        raise HTTPException(409, "Import contains duplicate emails")
    # Every insert remains in one transaction; exceptions roll back on session close.
    result = [create_user(db, user) for user in data.users]
    audit(db, admin, "import", "user")
    db.commit()
    return result


@router.patch("/users/{identifier}", response_model=UserRead)
def update_user(identifier: UUID, data: UserPatch, db: DB, admin: Admin):
    user = get_object(db, User, identifier)
    changes = data.model_dump(exclude_unset=True, exclude_none=True, exclude={"group_ids"})
    protect_last_admin(db, user, changes)
    for name, value in changes.items():
        setattr(user, name, value)
    if data.group_ids is not None:
        user.groups = groups_for(db, data.group_ids)
    invalidate_sessions(db, user.id)
    audit(db, admin, "update", "user", user.id)
    db.commit()
    return user


@router.delete("/users/{identifier}", status_code=204)
def disable_user(identifier: UUID, db: DB, admin: Admin):
    user = get_object(db, User, identifier)
    protect_last_admin(db, user, {"active": False})
    user.active = False
    invalidate_sessions(db, user.id)
    audit(db, admin, "disable", "user", user.id)
    db.commit()


@router.post("/users/{identifier}/reset-password", status_code=204)
def reset_password(identifier: UUID, data: PasswordReset, db: DB, admin: Admin):
    user = get_object(db, User, identifier)
    user.password_hash = hash_password(data.password)
    invalidate_sessions(db, user.id)
    audit(db, admin, "password_reset", "user", user.id)
    db.commit()


@router.get("/groups", response_model=list[GroupRead])
def groups(db: DB, admin: Admin):
    return db.scalars(select(Group).order_by(Group.name)).all()


@router.post("/groups", response_model=GroupRead, status_code=201)
def add_group(data: GroupInput, db: DB, admin: Admin):
    group = Group(**data.model_dump())
    db.add(group)
    db.flush()
    audit(db, admin, "create", "group", group.id)
    db.commit()
    return group


@router.patch("/groups/{identifier}", response_model=GroupRead)
def update_group(identifier: UUID, data: GroupPatch, db: DB, admin: Admin):
    group = get_object(db, Group, identifier)
    for name, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(group, name, value)
    audit(db, admin, "update", "group", group.id)
    db.commit()
    return group


@router.delete("/groups/{identifier}", status_code=204)
def delete_group(identifier: UUID, db: DB, admin: Admin):
    group = get_object(db, Group, identifier)
    for agent in db.scalars(select(Agent)):
        if group.id in agent.allowed_group_ids:
            agent.allowed_group_ids = [i for i in agent.allowed_group_ids if i != group.id]
    # Extension tables register grant cleanup when introduced.
    for cleanup in getattr(db.info.get("app"), "group_grant_cleanups", []):
        cleanup(db, group.id)
    db.delete(group)
    audit(db, admin, "delete", "group", str(identifier))
    db.commit()
