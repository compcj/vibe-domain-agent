from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select, update

from app.dependencies import DB, Admin, CurrentUser, require_csrf
from app.knowledge.access import citation_allowed, citation_dto, erase_document
from app.knowledge.dto import FileRead, KnowledgeInput, KnowledgePatch, KnowledgeRead, SearchInput
from app.knowledge.indexing import dispatch
from app.knowledge.models import Chunk, Citation, Document, KnowledgeBase
from app.knowledge.retrieval import configuration, search
from app.knowledge.storage import path_for, remove_file, store_upload
from app.models import Agent, AgentVersion, Conversation, ModelConnection, Run
from app.routers.users import get_object
from app.runtime.dto import CitationRead
from app.runtime.store import TERMINAL, owned
from app.security import audit

router = APIRouter(tags=["knowledge and files"])
Member = Annotated[object, Depends(require_csrf)]


def lock_kb(db, identifier):
    if not db.execute(
        update(KnowledgeBase)
        .where(KnowledgeBase.id == str(identifier))
        .values(name=KnowledgeBase.name)
    ).rowcount:
        raise HTTPException(404, "Knowledge base not found")
    return db.scalar(
        select(KnowledgeBase)
        .where(KnowledgeBase.id == str(identifier))
        .execution_options(populate_existing=True)
    )


def apply_kb(db, kb, data):
    values = data.model_dump(mode="json", exclude_unset=True, exclude_none=True)
    if "embedding_model_id" in values:
        model = db.get(ModelConnection, values["embedding_model_id"])
        if not model or not model.enabled or model.kind != "embedding":
            raise HTTPException(422, "An enabled embedding model is required")
        if kb.id and kb.embedding_model_id != model.id:
            if db.scalar(select(Document.id).where(Document.knowledge_base_id == kb.id)):
                raise HTTPException(409, "Remove all documents before changing the embedding model")
            kb.embedding_dimensions, kb.embedding_fingerprint = None, None
    for key, value in values.items():
        setattr(kb, key, value)


@router.get("/admin/knowledge-bases", response_model=list[KnowledgeRead])
def knowledge_bases(db: DB, admin: Admin):
    return db.scalars(select(KnowledgeBase)).all()


@router.post("/admin/knowledge-bases", response_model=KnowledgeRead, status_code=201)
def add_knowledge(data: KnowledgeInput, db: DB, admin: Admin):
    kb = KnowledgeBase()
    apply_kb(db, kb, data)
    db.add(kb)
    db.flush()
    audit(db, admin, "create", "knowledge_base", kb.id)
    db.commit()
    return kb


@router.patch("/admin/knowledge-bases/{identifier}", response_model=KnowledgeRead)
def update_knowledge(identifier: UUID, data: KnowledgePatch, db: DB, admin: Admin):
    kb = lock_kb(db, identifier)
    apply_kb(db, kb, data)
    audit(db, admin, "update", "knowledge_base", kb.id)
    db.commit()
    return kb


@router.delete("/admin/knowledge-bases/{identifier}", status_code=204)
def delete_knowledge(identifier: UUID, request: Request, db: DB, admin: Admin):
    kb = lock_kb(db, identifier)
    referenced = any(
        kb.id in config.get("knowledge_base_ids", [])
        for config in [*db.scalars(select(Agent.draft)), *db.scalars(select(AgentVersion.snapshot))]
    )
    if referenced:
        raise HTTPException(
            409, "Knowledge base is referenced by an Agent draft or retained version"
        )
    for doc in db.scalars(select(Document).where(Document.knowledge_base_id == kb.id)).all():
        erase_document(db, request.app.state, doc)
    db.flush()
    db.delete(kb)
    audit(db, admin, "delete", "knowledge_base", kb.id)
    db.commit()


@router.get("/admin/knowledge-bases/{identifier}/documents", response_model=list[FileRead])
def documents(identifier: UUID, db: DB, admin: Admin):
    get_object(db, KnowledgeBase, identifier)
    return db.scalars(select(Document).where(Document.knowledge_base_id == str(identifier))).all()


@router.post(
    "/admin/knowledge-bases/{identifier}/documents", response_model=FileRead, status_code=202
)
def upload_document(
    identifier: UUID, request: Request, db: DB, admin: Admin, file: UploadFile = File()
):
    kb = lock_kb(db, identifier)
    configuration(db, kb)
    key, checksum = store_upload(request.app.state, file)
    old_key = None
    try:
        doc = db.scalar(
            select(Document).where(
                Document.knowledge_base_id == kb.id, Document.name == file.filename
            )
        )
        if doc:
            old_key = doc.storage_key
            db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
            doc.revision += 1
            doc.status, doc.error, doc.claim_token, doc.claim_expires_at = (
                "queued",
                None,
                None,
                None,
            )
            doc.storage_key, doc.checksum, doc.embedding_fingerprint = key, checksum, None
        else:
            doc = Document(
                knowledge_base_id=kb.id, name=file.filename, storage_key=key, checksum=checksum
            )
            db.add(doc)
        db.flush()
        audit(db, admin, "upload", "document", doc.id)
        db.commit()
    except Exception:
        remove_file(request.app.state, key)
        raise
    if old_key:
        remove_file(request.app.state, old_key)
    dispatch(request.app.state, doc.id)
    return doc


@router.delete("/admin/documents/{identifier}", status_code=204)
def delete_document(identifier: UUID, request: Request, db: DB, admin: Admin):
    doc = get_object(db, Document, identifier)
    if not doc.knowledge_base_id:
        raise HTTPException(404, "Document not found")
    kb = lock_kb(db, doc.knowledge_base_id)
    erase_document(db, request.app.state, doc)
    db.flush()
    if not db.scalar(select(Document.id).where(Document.knowledge_base_id == kb.id)):
        kb.embedding_dimensions, kb.embedding_fingerprint = None, None
    audit(db, admin, "delete", "document", str(identifier))
    db.commit()


@router.post("/admin/documents/{identifier}/retry", response_model=FileRead, status_code=202)
def retry_document(identifier: UUID, request: Request, db: DB, admin: Admin):
    doc = get_object(db, Document, identifier)
    if not doc.knowledge_base_id:
        raise HTTPException(404, "Document not found")
    lock_kb(db, doc.knowledge_base_id)
    if not db.execute(
        update(Document)
        .where(Document.id == doc.id, Document.status == "failed")
        .values(status="queued", error=None, claim_token=None, claim_expires_at=None)
    ).rowcount:
        raise HTTPException(409, "Only failed documents can be retried")
    audit(db, admin, "retry", "document", doc.id)
    db.commit()
    db.refresh(doc)
    dispatch(request.app.state, doc.id)
    return doc


@router.post("/admin/knowledge-bases/{identifier}/search", response_model=list[CitationRead])
def search_knowledge(identifier: UUID, data: SearchInput, request: Request, db: DB, admin: Admin):
    rows = search(request.app.state, db, str(identifier), data.query)
    return [
        {"id": chunk.id, "title": doc.name, "locator": chunk.locator, "excerpt": chunk.text}
        for chunk, doc in rows
    ]


def lock_conversation(db, user, identifier):
    conversation = owned(db, Conversation, identifier, user)
    db.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(title=Conversation.title)
    )
    db.refresh(conversation)
    return conversation


@router.post("/files", response_model=FileRead, status_code=202)
def upload_file(
    request: Request,
    db: DB,
    user: Member,
    conversation_id: UUID = Form(),
    file: UploadFile = File(),
):
    conversation = lock_conversation(db, user, conversation_id)
    if (
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.conversation_id == conversation.id)
        )
        >= 5
    ):
        raise HTTPException(409, "At most five files are allowed per conversation")
    key, checksum = store_upload(request.app.state, file)
    doc = Document(
        conversation_id=conversation.id,
        owner_id=user.id,
        name=file.filename,
        storage_key=key,
        checksum=checksum,
    )
    try:
        db.add(doc)
        db.flush()
        audit(db, user, "upload", "attachment", doc.id)
        db.commit()
    except Exception:
        remove_file(request.app.state, key)
        raise
    dispatch(request.app.state, doc.id)
    return doc


@router.get("/files", response_model=list[FileRead])
def files(conversation_id: UUID, db: DB, user: CurrentUser):
    owned(db, Conversation, conversation_id, user)
    return db.scalars(
        select(Document).where(
            Document.conversation_id == str(conversation_id), Document.owner_id == user.id
        )
    ).all()


def owner_file(db, user, identifier):
    doc = db.get(Document, str(identifier))
    if not doc or not doc.conversation_id or doc.owner_id != user.id:
        raise HTTPException(404, "File not found")
    owned(db, Conversation, doc.conversation_id, user)
    return doc


@router.get("/files/{identifier}", response_model=FileRead)
def file_status(identifier: UUID, db: DB, user: CurrentUser):
    return owner_file(db, user, identifier)


@router.get("/files/{identifier}/download")
def download_file(identifier: UUID, request: Request, db: DB, user: CurrentUser):
    doc = owner_file(db, user, identifier)
    path = path_for(request.app.state, doc.storage_key)
    if not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=doc.name,
        content_disposition_type="attachment",
    )


@router.delete("/files/{identifier}", status_code=204)
def delete_file(identifier: UUID, request: Request, db: DB, user: Member):
    doc = owner_file(db, user, identifier)
    conversation = lock_conversation(db, user, doc.conversation_id)
    if conversation.active_run_id:
        run = db.get(Run, conversation.active_run_id)
        if run and run.status not in TERMINAL and doc.id in run.attachment_ids:
            raise HTTPException(409, "Attachment is in use by an active run")
    erase_document(db, request.app.state, doc)
    audit(db, user, "delete", "attachment", str(identifier))
    db.commit()


@router.get("/citations/{identifier}", response_model=CitationRead)
def citation(identifier: UUID, db: DB, user: CurrentUser):
    row = db.get(Citation, str(identifier))
    if not citation_allowed(db, row, user):
        raise HTTPException(404, "Citation not found")
    return citation_dto(row)
