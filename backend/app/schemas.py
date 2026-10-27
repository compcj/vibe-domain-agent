from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Output(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserRead(Output):
    id: str
    email: str
    display_name: str
    role: Literal["admin", "member"]
    group_ids: list[str]
    active: bool


class Login(Input):
    email: str = Field(max_length=254)
    password: str = Field(max_length=1024)


class AuthRead(Output):
    user: UserRead
    csrf_token: str


class UserCreate(Input):
    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=1024)
    role: Literal["admin", "member"] = "member"
    group_ids: list[UUID] = Field(default_factory=list)
    active: bool = True

    @field_validator("email")
    @classmethod
    def email_format(cls, value):
        value = value.strip().lower()
        if value.count("@") != 1 or any(c.isspace() for c in value):
            raise ValueError("Invalid email address")
        return value


class UserPatch(Input):
    email: str | None = Field(default=None, min_length=3, max_length=254)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: Literal["admin", "member"] | None = None
    group_ids: list[UUID] | None = None
    active: bool | None = None
    _email = field_validator("email")(
        classmethod(lambda cls, v: UserCreate.email_format(v) if v is not None else v)
    )


class UserImport(Input):
    users: list[UserCreate] = Field(min_length=1, max_length=1000)


class PasswordReset(Input):
    password: str = Field(min_length=12, max_length=1024)


class PasswordChange(Input):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class GroupInput(Input):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)


class GroupPatch(Input):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)


class GroupRead(Output):
    id: str
    name: str
    description: str


def validate_endpoint(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("URL must be HTTP(S) without embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("URL must not include a query or fragment")
    return value.rstrip("/")


class ModelInput(Input):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["chat", "embedding"]
    base_url: str = Field(max_length=2000)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(default="", max_length=10000)
    enabled: bool = True
    _url = field_validator("base_url")(validate_endpoint)


class ModelPatch(Input):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Literal["chat", "embedding"] | None = None
    base_url: str | None = Field(default=None, max_length=2000)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=10000)
    enabled: bool | None = None
    _url = field_validator("base_url")(lambda v: validate_endpoint(v) if v is not None else v)


class ModelRead(Output):
    id: str
    name: str
    kind: str
    base_url: str
    model: str
    enabled: bool
    has_api_key: bool


class Draft(Input):
    model_id: UUID | None = None
    system_prompt: str = Field(default="", max_length=100000)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=4096, ge=1, le=131072)
    tool_ids: list[UUID] = Field(default_factory=list)
    knowledge_base_ids: list[UUID] = Field(default_factory=list)
    max_steps: int = Field(default=20, ge=1, le=20)
    timeout_seconds: int = Field(default=300, ge=1, le=300)


class AgentInput(Input):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    examples: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool = True
    allowed_group_ids: list[UUID] = Field(default_factory=list)
    draft: Draft = Field(default_factory=Draft)


class AgentPatch(Input):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    examples: list[str] | None = Field(default=None, max_length=20)
    enabled: bool | None = None
    allowed_group_ids: list[UUID] | None = None
    draft: Draft | None = None


class AgentRead(Output):
    id: str
    name: str
    description: str
    examples: list[str]
    enabled: bool
    allowed_group_ids: list[str]
    draft: Draft
    published_version_id: str | None


class MemberAgentRead(Output):
    id: str
    name: str
    description: str
    examples: list[str]


class VersionRead(Output):
    id: str
    agent_id: str
    version: int
    snapshot: dict
    created_at: datetime
    created_by: str


class Rollback(Input):
    version_id: UUID


class AuditRead(Output):
    id: str
    actor_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    created_at: datetime
