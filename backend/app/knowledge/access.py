"""Owner and live source authorization, including transcript and event projections."""

from fastapi import HTTPException
from sqlalchemy import select, update

from app.dependencies import can_use_agent
from app.knowledge.models import Citation, Document, KnowledgeBase
from app.knowledge.retrieval import configuration
from app.knowledge.storage import remove_file
from app.models import Agent, AgentVersion, Conversation
from app.runtime.dto import CitationRead


def validate_knowledge(db, ids):
    for identifier in set(map(str, ids)):
        kb = db.get(KnowledgeBase, identifier)
        if not kb:
            raise HTTPException(422, "Knowledge base is unavailable")
        configuration(db, kb)


def frozen_knowledge(db, config):
    return [
        {"knowledge_base_id": kid, "embedding": configuration(db, db.get(KnowledgeBase, kid))}
        for kid in config.get("knowledge_base_ids", [])
    ]


def validate_attachments(db, user, conversation, ids):
    if len(ids) > 5 or len(set(map(str, ids))) != len(ids):
        raise HTTPException(422, "At most five unique ready attachments are allowed")
    for identifier in ids:
        doc = db.get(Document, str(identifier))
        if (
            not doc
            or doc.owner_id != user.id
            or doc.conversation_id != conversation.id
            or doc.status != "ready"
        ):
            raise HTTPException(422, "Attachment is unavailable or not ready")


def citation_allowed(db, citation, user, *, admin_debug=False):
    if not citation or citation.owner_id != user.id or not user.active:
        return False
    doc = db.get(Document, citation.document_id) if citation.document_id else None
    if not doc or doc.status != "ready" or doc.revision != citation.revision:
        return False
    conversation = db.get(Conversation, citation.conversation_id)
    if not conversation or conversation.owner_id != user.id:
        return False
    if doc.conversation_id:
        return doc.owner_id == user.id and doc.conversation_id == conversation.id
    kb = db.get(KnowledgeBase, doc.knowledge_base_id)
    agent = db.get(Agent, conversation.agent_id)
    if not kb or not agent or not agent.enabled:
        return False
    if not admin_debug and not can_use_agent(user, agent):
        return False
    version = db.get(AgentVersion, agent.published_version_id)
    if not admin_debug and (
        not version or kb.id not in version.snapshot.get("knowledge_base_ids", [])
    ):
        return False
    try:
        configuration(db, kb)
    except HTTPException:
        return False
    return True


def citation_dto(citation):
    return CitationRead.model_validate(
        {key: getattr(citation, key) for key in ("id", "title", "locator", "excerpt")}
    ).model_dump(mode="json")


def visible_citations(db, user, values):
    result = []
    for value in values:
        citation = db.get(Citation, value.get("id", ""))
        if citation_allowed(db, citation, user):
            result.append(citation_dto(citation))
    return result


def erase_document(db, state, doc):
    # Tombstone references so saved graph citations fail closed on resume.
    db.execute(
        update(Citation).where(Citation.document_id == doc.id).values(document_id=None, excerpt="")
    )
    remove_file(state, doc.storage_key)
    db.delete(doc)


def cleanup_conversation(state, db, identifier):
    for doc in db.scalars(select(Document).where(Document.conversation_id == identifier)).all():
        erase_document(db, state, doc)


def validate_run_citations(db, run, user, values):
    for value in values:
        citation = db.get(Citation, value.get("id", ""))
        if (
            not citation
            or citation.run_id != run.id
            or not citation_allowed(
                db, citation, user, admin_debug=run.debug and user.role == "admin"
            )
        ):
            raise HTTPException(403, "Reference material is no longer available")


UNAVAILABLE_ANSWER = "Referenced answer is no longer available"


def references_hidden(db, user, run_id):
    refs = db.scalars(select(Citation).where(Citation.run_id == run_id)).all()
    return any(not citation_allowed(db, ref, user) for ref in refs)


def message_content(db, user, message):
    if message.role == "assistant" and references_hidden(db, user, message.run_id):
        return UNAVAILABLE_ANSWER
    return message.content


def validate_run_knowledge(db, run, user):
    ids = run.snapshot.get("knowledge_base_ids", [])
    if not ids:
        return
    conversation = db.get(Conversation, run.conversation_id)
    agent = db.get(Agent, conversation.agent_id)
    if not (run.debug and user.role == "admin"):
        version = db.get(AgentVersion, agent.published_version_id)
        allowed = set(version.snapshot.get("knowledge_base_ids", [])) if version else set()
        if not set(ids).issubset(allowed):
            raise HTTPException(403, "Knowledge access is no longer available")
    validate_knowledge(db, ids)
