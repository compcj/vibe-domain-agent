from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.dependencies import DB, Admin
from app.models import Agent, ModelConnection
from app.routers.users import get_object
from app.schemas import ModelInput, ModelPatch, ModelRead
from app.security import audit, decrypt_secret, encrypt_secret

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
    )


def apply_model(model, data, settings):
    values = data.model_dump(exclude_unset=True, exclude_none=True)
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
    if any(agent.draft.get("model_id") == model.id for agent in db.scalars(select(Agent))):
        raise HTTPException(409, "Model is referenced by an Agent; disable it instead")
    db.delete(model)
    audit(db, admin, "delete", "model", str(identifier))
    db.commit()


@router.post("/{identifier}/test")
def test_model(identifier: UUID, request: Request, db: DB, admin: Admin):
    model = get_object(db, ModelConnection, identifier)
    if not model.enabled:
        raise HTTPException(409, "Model is disabled")
    credential = decrypt_secret(model.api_key_encrypted, request.app.state.settings)
    headers = {"Authorization": f"Bearer {credential}"} if credential else {}
    payload = {"model": model.model}
    if model.kind == "chat":
        endpoint = "chat/completions"
        payload.update(messages=[{"role": "user", "content": "Reply OK"}], max_tokens=8)
    else:
        endpoint = "embeddings"
        payload.update(input="Connection test")
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            response = client.post(f"{model.base_url}/{endpoint}", json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
        ok = isinstance(body, dict) and isinstance(
            body.get("choices" if model.kind == "chat" else "data"), list
        )
    except (httpx.HTTPError, ValueError):
        ok = False
    audit(db, admin, "connection_test", "model", model.id)
    db.commit()
    return {"ok": ok, "message": "Connection succeeded" if ok else "Connection test failed"}
