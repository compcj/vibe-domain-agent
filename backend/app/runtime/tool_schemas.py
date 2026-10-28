"""Administrator tool configuration and member-safe input validation."""

import json
import re
from typing import Literal

from jsonschema import Draft202012Validator, SchemaError
from pydantic import Field, field_validator, model_validator

from app.runtime.dto import Input
from app.schemas import validate_endpoint


class ToolInput(Input):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    transport: Literal["http", "mcp"]
    endpoint: str = Field(max_length=2000)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    mcp_tool_name: str = Field(default="", max_length=200)
    input_schema: dict = Field(default_factory=lambda: {"type": "object"})
    fixed_arguments: dict = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    effect: Literal["read", "write"] = "read"
    policy: Literal["auto", "confirm"] | None = None
    allowed_group_ids: list[str] = Field(default_factory=list, max_length=1000)
    timeout_seconds: int = Field(default=30, ge=1, le=60)
    idempotency_header: str = Field(default="", max_length=100)
    enabled: bool = True
    _url = field_validator("endpoint")(validate_endpoint)

    @model_validator(mode="after")
    def validate_config(self):
        self.policy = self.policy or ("confirm" if self.effect == "write" else "auto")
        if self.transport == "mcp" and not self.mcp_tool_name:
            raise ValueError("MCP tool selection is required")
        if (
            len(json.dumps(self.input_schema)) > 32000
            or len(json.dumps(self.fixed_arguments)) > 32000
        ):
            raise ValueError("Tool configuration exceeds size limit")

        def refs(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"$ref", "$dynamicRef"} and (
                        not isinstance(item, str) or not item.startswith("#")
                    ):
                        raise ValueError("External schema references are forbidden")
                    refs(item)
            elif isinstance(value, list):
                for item in value:
                    refs(item)

        refs(self.input_schema)
        try:
            Draft202012Validator.check_schema(self.input_schema)
        except SchemaError:
            raise ValueError("Invalid JSON Schema") from None
        if self.input_schema.get("type") != "object":
            raise ValueError("Tool input schema must describe an object")
        for name, value in self.headers.items():
            if (
                not re.fullmatch(r"[!#$%&\x27*+.^_`|~0-9A-Za-z-]+", name)
                or "\n" in value
                or "\r" in value
            ):
                raise ValueError("Invalid header")
        if self.idempotency_header and not re.fullmatch(r"[A-Za-z0-9-]+", self.idempotency_header):
            raise ValueError("Invalid idempotency header")
        return self


class DiscoverInput(Input):
    endpoint: str = Field(max_length=2000)
    headers: dict[str, str] = Field(default_factory=dict)
    _url = field_validator("endpoint")(validate_endpoint)


class ResolveInput(Input):
    outcome: Literal["succeeded", "failed"]
    note: str = Field(min_length=1, max_length=2000)
