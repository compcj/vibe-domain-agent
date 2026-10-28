"""Bounded embedding transport with immutable connection bindings."""

import asyncio
import hashlib
import json
import math

import httpx

from app.runtime import budget
from app.runtime.transports import supervised
from app.security import decrypt_secret


class EmbeddingFailure(Exception):
    pass


def fingerprint(config):
    return hashlib.sha256(
        json.dumps(
            [config["base_url"].rstrip("/"), config["model"]], separators=(",", ":")
        ).encode()
    ).hexdigest()


def binding(model):
    config = {key: getattr(model, key) for key in ("id", "base_url", "model", "api_key_encrypted")}
    config["fingerprint"] = fingerprint(config)
    return config


def vectors(body, count, dimension=None):
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("data"), list)
        or len(body["data"]) != count
    ):
        raise ValueError()
    result = [None] * count
    for index, row in enumerate(body["data"]):
        position = row.get("index", index)
        vector = row.get("embedding")
        if (
            type(position) is not int
            or position < 0
            or position >= count
            or result[position] is not None
        ):
            raise ValueError()
        if not isinstance(vector, list) or not 1 <= len(vector) <= 8192:
            raise ValueError()
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in vector) or not any(
            vector
        ):
            raise ValueError()
        dimension = dimension or len(vector)
        if len(vector) != dimension:
            raise ValueError()
        result[position] = [float(v) for v in vector]
    return result


async def request_bytes(state, config, endpoint, payload, timeout=30):
    key = (
        decrypt_secret(config["api_key_encrypted"], state.settings)
        if config["api_key_encrypted"]
        else ""
    )
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        transport=getattr(state, "http_transport", None),
    ) as client:
        async with client.stream(
            "POST", config["base_url"].rstrip("/") + "/" + endpoint, json=payload, headers=headers
        ) as response:
            if response.status_code != 200:
                raise ValueError()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 4 * 1024 * 1024:
                    raise ValueError()
            return bytes(body)


def network(state, operation, timeout):
    accounting = {}
    try:
        return asyncio.run(supervised(state, operation, timeout, accounting))
    finally:
        if accounting.get("at") is not None:
            budget.accounted_at.set(accounting["at"])


def embed(state, config, texts, dimension=None, timeout=30):
    if not texts or len(texts) > 32 or any(len(t) > 10000 for t in texts):
        raise EmbeddingFailure("Embedding service input limit exceeded")
    try:
        body = network(
            state,
            lambda: request_bytes(
                state, config, "embeddings", {"model": config["model"], "input": texts}, timeout
            ),
            timeout,
        )
        return vectors(json.loads(body), len(texts), dimension)
    except Exception:
        raise EmbeddingFailure(
            "Embedding service unavailable or returned invalid vectors"
        ) from None
