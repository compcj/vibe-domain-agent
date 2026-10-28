from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationInput(Input):
    agent_id: UUID
    title: str = Field(default="New conversation", min_length=1, max_length=200)


class RunInput(Input):
    message: str = Field(min_length=1, max_length=32000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)


class Decision(Input):
    approved: bool


class FeedbackInput(Input):
    rating: Literal[-1, 1]
    comment: str = Field(default="", max_length=2000)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    agent_id: str
    title: str
    created_at: datetime
    active_run_id: str | None


class CitationRead(BaseModel):
    id: str
    title: str
    locator: str
    excerpt: str


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    role: Literal["user", "assistant"]
    content: str
    citations: list[CitationRead]
    run_id: str
    created_at: datetime


def member_status(value):
    return "queued" if value == "resume_queued" else value


class RunAccepted(BaseModel):
    id: str
    status: str
    _status = field_validator("status", mode="before")(member_status)


class ApprovalRead(BaseModel):
    id: str
    title: str
    summary: str
    parameters: dict
    expires_at: datetime


class RunRead(BaseModel):
    id: str
    status: str
    _status = field_validator("status", mode="before")(member_status)
    error: str | None
    approvals: list[ApprovalRead]
    created_at: datetime


class StatusEvent(BaseModel):
    status: str
    message: str
    _status = field_validator("status", mode="before")(member_status)


class DeltaEvent(BaseModel):
    text: str


class MessageEvent(BaseModel):
    message: str


class DoneEvent(BaseModel):
    status: str


EVENT_SCHEMAS = {
    "status": StatusEvent,
    "delta": DeltaEvent,
    "citation": CitationRead,
    "approval": ApprovalRead,
    "result": MessageEvent,
    "done": DoneEvent,
    "error": MessageEvent,
}
