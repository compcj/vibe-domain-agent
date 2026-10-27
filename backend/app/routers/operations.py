from fastapi import APIRouter
from sqlalchemy import func, select

from app.dependencies import DB, Admin
from app.models import Agent, AuditEvent, Group, ModelConnection, User
from app.schemas import AuditRead

router = APIRouter(prefix="/admin", tags=["operations"])


@router.get("/audit", response_model=list[AuditRead])
def audit_events(db: DB, admin: Admin):
    return db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(500)).all()


@router.get("/metrics")
def metrics(db: DB, admin: Admin):
    return {
        name: db.scalar(select(func.count()).select_from(model))
        for name, model in {
            "users": User,
            "groups": Group,
            "agents": Agent,
            "models": ModelConnection,
            "audit_events": AuditEvent,
        }.items()
    }
