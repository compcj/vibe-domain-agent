import copy
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from app.dependencies import DB, Admin, CurrentUser, can_use_agent
from app.models import Agent, AgentVersion, ModelConnection
from app.routers.users import get_object, groups_for
from app.schemas import (
    AgentInput,
    AgentPatch,
    AgentRead,
    Draft,
    MemberAgentRead,
    Rollback,
    VersionRead,
)
from app.security import audit

router = APIRouter(tags=["agents"])


def validate_publication(db, snapshot, app_state):
    draft = Draft.model_validate(snapshot)
    if not draft.system_prompt.strip() or not draft.model_id:
        raise HTTPException(422, "A model and nonempty system prompt are required")
    model = db.get(ModelConnection, str(draft.model_id))
    if not model or not model.enabled or model.kind != "chat":
        raise HTTPException(422, "An enabled chat model is required")
    for field in ("tool_ids", "knowledge_base_ids"):
        identifiers = snapshot.get(field, [])
        validator = app_state.resource_validators.get(field)
        if identifiers:
            if not validator:
                raise HTTPException(422, f"{field} are not available")
            validator(db, identifiers)


def apply_agent(agent, data, db):
    values = data.model_dump(mode="json", exclude_unset=True, exclude_none=True)
    if "allowed_group_ids" in values:
        groups_for(db, values["allowed_group_ids"])
    if data.draft is not None and "draft" in values:
        # Draft patches replace the complete draft; defaults are explicit snapshots.
        values["draft"] = data.draft.model_dump(mode="json")
    for key, value in values.items():
        setattr(agent, key, value)


@router.get("/admin/agents", response_model=list[AgentRead])
def agents(db: DB, admin: Admin):
    return db.scalars(select(Agent).order_by(Agent.created_at)).all()


@router.post("/admin/agents", response_model=AgentRead, status_code=201)
def add_agent(data: AgentInput, db: DB, admin: Admin):
    agent = Agent()
    apply_agent(agent, data, db)
    db.add(agent)
    db.flush()
    audit(db, admin, "create", "agent", agent.id)
    db.commit()
    return agent


@router.get("/admin/agents/{identifier}", response_model=AgentRead)
def agent(identifier: UUID, db: DB, admin: Admin):
    return get_object(db, Agent, identifier)


@router.patch("/admin/agents/{identifier}", response_model=AgentRead)
def update_agent(identifier: UUID, data: AgentPatch, db: DB, admin: Admin):
    agent = get_object(db, Agent, identifier)
    apply_agent(agent, data, db)
    audit(db, admin, "update", "agent", agent.id)
    db.commit()
    return agent


@router.post("/admin/agents/{identifier}/publish", response_model=VersionRead, status_code=201)
def publish(identifier: UUID, request: Request, db: DB, admin: Admin):
    agent = db.scalar(select(Agent).where(Agent.id == str(identifier)).with_for_update())
    if not agent:
        raise HTTPException(404, "Resource not found")
    validate_publication(db, agent.draft, request.app.state)
    count = (
        db.scalar(select(func.max(AgentVersion.version)).where(AgentVersion.agent_id == agent.id))
        or 0
    )
    version = AgentVersion(
        agent_id=agent.id,
        version=count + 1,
        snapshot=copy.deepcopy(agent.draft),
        created_by=admin.id,
    )
    db.add(version)
    db.flush()
    agent.published_version_id = version.id
    audit(db, admin, "publish", "agent", agent.id)
    db.commit()
    return version


@router.get("/admin/agents/{identifier}/versions", response_model=list[VersionRead])
def versions(identifier: UUID, db: DB, admin: Admin):
    get_object(db, Agent, identifier)
    return db.scalars(
        select(AgentVersion)
        .where(AgentVersion.agent_id == str(identifier))
        .order_by(AgentVersion.version.desc())
    ).all()


@router.post("/admin/agents/{identifier}/rollback", response_model=AgentRead)
def rollback(identifier: UUID, data: Rollback, request: Request, db: DB, admin: Admin):
    agent = get_object(db, Agent, identifier)
    version = get_object(db, AgentVersion, data.version_id)
    if version.agent_id != agent.id:
        raise HTTPException(404, "Resource not found")
    validate_publication(db, version.snapshot, request.app.state)
    agent.draft = copy.deepcopy(version.snapshot)
    agent.published_version_id = version.id
    audit(db, admin, "rollback", "agent", agent.id)
    db.commit()
    return agent


@router.get("/agents", response_model=list[MemberAgentRead])
def member_agents(db: DB, user: CurrentUser):
    return [
        MemberAgentRead.model_validate(agent)
        for agent in db.scalars(select(Agent))
        if can_use_agent(user, agent)
    ]
