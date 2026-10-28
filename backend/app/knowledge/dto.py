from uuid import UUID

from pydantic import Field

from app.schemas import Input, Output


class KnowledgeInput(Input):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    embedding_model_id: UUID


class KnowledgePatch(Input):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    embedding_model_id: UUID | None = None


class KnowledgeRead(Output):
    id: str
    name: str
    description: str
    embedding_model_id: str


class FileRead(Output):
    id: str
    name: str
    status: str
    error: str | None = None


class SearchInput(Input):
    query: str = Field(min_length=1, max_length=10000)
