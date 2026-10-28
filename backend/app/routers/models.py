from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.orm.attributes import flag_modified

from app.dependencies import DB, Admin
from app.models import Agent, AgentVersion, ModelConnection, utcnow
from app.routers.users import get_object
from app.schemas import ModelInput, ModelPatch, ModelRead
from app.security import audit, encrypt_secret

router = APIRouter(prefix="/admin/models", tags=["model connections"])


def model_read(model):
    return ModelRead(
        id=model.id,
        name=model.name,
        kind=model.kind,
        base_url=model.base_url,
        model=model.model,
        enabled=model.enabled,
        has_api_key=bool(model.api_key_encrypted),
        capabilities=model.capabilities or {},
        tested_at=model.tested_at,
    )


def apply_model(model, data, settings):
    values = data.model_dump(exclude_unset=True, exclude_none=True)
    if set(values).intersection({"api_key", "base_url", "model", "kind"}):
        model.capabilities = {}
        model.tested_at = None
        # Force both resets even if a concurrent probe changed the database after
        # this request loaded an already-untested identity-map object.
        flag_modified(model, "capabilities")
        flag_modified(model, "tested_at")
    if "api_key" in values:
        try:
            model.api_key_encrypted = encrypt_secret(values.pop("api_key"), settings)
        except ValueError:
            raise HTTPException(503, "Server encryption is not configured") from None
    for name, value in values.items():
        setattr(model, name, value)


@router.get("", response_model=list[ModelRead])
def models(db: DB, admin: Admin):
    return [model_read(model) for model in db.scalars(select(ModelConnection))]


@router.post("", response_model=ModelRead, status_code=201)
def add_model(data: ModelInput, request: Request, db: DB, admin: Admin):
    model = ModelConnection()
    apply_model(model, data, request.app.state.settings)
    db.add(model)
    db.flush()
    audit(db, admin, "create", "model", model.id)
    db.commit()
    return model_read(model)


@router.patch("/{identifier}", response_model=ModelRead)
def update_model(identifier: UUID, data: ModelPatch, request: Request, db: DB, admin: Admin):
    model = get_object(db, ModelConnection, identifier)
    apply_model(model, data, request.app.state.settings)
    audit(db, admin, "update", "model", model.id)
    db.commit()
    return model_read(model)


@router.delete("/{identifier}", status_code=204)
def delete_model(identifier: UUID, db: DB, admin: Admin):
    model = get_object(db, ModelConnection, identifier)
    draft_reference = db.scalar(
        select(Agent.id).where(Agent.draft["model_id"].as_string() == model.id)
    )
    version_reference = db.scalar(
        select(AgentVersion.id).where(AgentVersion.snapshot["model_id"].as_string() == model.id)
    )
    from app.knowledge.models import KnowledgeBase

    knowledge_reference = db.scalar(
        select(KnowledgeBase.id).where(KnowledgeBase.embedding_model_id == model.id)
    )
    if draft_reference or version_reference or knowledge_reference:
        raise HTTPException(409, "Model is referenced by an Agent; disable it instead")
    db.delete(model)
    audit(db, admin, "delete", "model", str(identifier))
    db.commit()


@router.post("/{identifier}/test")
def test_model(identifier: UUID, request: Request, db: DB, admin: Admin):
    model = get_object(db, ModelConnection, identifier)
    if not model.enabled:
        raise HTTPException(409, "Model is disabled")
    from app.knowledge.probes import probe

    # Detach the immutable probe input and release the transaction before network I/O.
    fields = ("base_url", "model", "api_key_encrypted", "kind", "enabled")
    expected = {field: getattr(model, field) for field in fields}
    model_id = model.id
    db.expunge(model)
    db.commit()
    result = probe(request.app.state, model)
    changed = db.execute(
        update(ModelConnection)
        .where(
            ModelConnection.id == model_id,
            *(getattr(ModelConnection, field) == value for field, value in expected.items()),
        )
        .values(capabilities=result["capabilities"], tested_at=utcnow())
    ).rowcount
    if not changed:
        db.rollback()
        raise HTTPException(
            409, "Model configuration changed; test the current configuration again"
        )
    audit(db, admin, "connection_test", "model", model_id)
    db.commit()
    return result
