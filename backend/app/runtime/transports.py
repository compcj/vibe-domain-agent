"""Only this transport boundary sees decrypted connector credentials."""

import asyncio
import json
import logging
from datetime import timedelta

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MAX_RESULT = 64000


class TransportFailure(Exception):
    pass


def safe_text(value, headers):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) > MAX_RESULT:
        raise TransportFailure()
    for secret in headers.values():
        if secret:
            text = text.replace(secret, "[redacted]")
            if secret.startswith("Bearer "):
                text = text.replace(secret[7:], "[redacted]")
    return text


def http_call(state, config, arguments, headers, key):
    headers = dict(headers)
    if config["idempotency_header"]:
        headers[config["idempotency_header"]] = key
    with httpx.Client(
        timeout=config["timeout_seconds"],
        follow_redirects=False,
        trust_env=False,
        transport=getattr(state, "http_transport", None),
    ) as client:
        kwargs = {"params": arguments} if config["method"] == "GET" else {"json": arguments}
        with client.stream(
            config["method"], config["endpoint"], headers=headers, **kwargs
        ) as response:
            if not 200 <= response.status_code < 300:
                raise TransportFailure()
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_RESULT:
                    raise TransportFailure()
                chunks.append(chunk)
            return safe_text(b"".join(chunks).decode("utf-8", errors="replace"), headers)


class BoundedAsyncStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        total = 0
        async for chunk in self.stream:
            total += len(chunk)
            if total > 200000:
                raise TransportFailure()
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class BoundedAsyncTransport(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request):
        response = await super().handle_async_request(request)
        response.stream = BoundedAsyncStream(response.stream)
        return response


# SDK diagnostics can contain raw remote payloads. Keep only application-owned audit events.
logging.getLogger("mcp.client.streamable_http").setLevel(logging.CRITICAL)
logging.getLogger("mcp.shared.session").setLevel(logging.CRITICAL)


async def mcp_session(endpoint, headers, timeout, name=None, arguments=None):
    async with asyncio.timeout(timeout):
        async with httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=BoundedAsyncTransport(),
        ) as client:
            async with streamable_http_client(endpoint, http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    if name is None:
                        result = await session.list_tools()
                        rows = [
                            {
                                "name": t.name,
                                "title": t.title,
                                "description": t.description,
                                "inputSchema": t.inputSchema,
                            }
                            for t in result.tools
                        ]
                        return json.loads(safe_text(rows, headers))
                    result = await session.call_tool(
                        name, arguments, read_timeout_seconds=timedelta(seconds=timeout)
                    )
                    if result.isError:
                        raise TransportFailure()
                    return safe_text(result.model_dump(mode="json"), headers)


def invoke(state, config, arguments, headers, key):
    try:
        if config["transport"] == "http":
            return http_call(state, config, arguments, headers, key)
        return asyncio.run(
            mcp_session(
                config["endpoint"],
                headers,
                config["timeout_seconds"],
                config["mcp_tool_name"],
                arguments,
            )
        )
    except Exception:
        raise TransportFailure() from None
