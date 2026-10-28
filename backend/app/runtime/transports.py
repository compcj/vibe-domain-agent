"""Only this transport boundary sees decrypted connector credentials."""

import asyncio
import json
import logging
from datetime import timedelta

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.runtime import budget

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


async def http_call(state, config, arguments, headers, key):
    headers = dict(headers)
    if config["idempotency_header"]:
        headers[config["idempotency_header"]] = key
    async with httpx.AsyncClient(
        timeout=config["timeout_seconds"],
        follow_redirects=False,
        trust_env=False,
        transport=getattr(state, "http_transport", None),
    ) as client:
        kwargs = {"params": arguments} if config["method"] == "GET" else {"json": arguments}
        async with client.stream(
            config["method"], config["endpoint"], headers=headers, **kwargs
        ) as response:
            if not 200 <= response.status_code < 300:
                raise TransportFailure()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                budget.check()
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


HEARTBEAT_INTERVAL = 1.0


async def supervised(state, operation, timeout, accounting):
    """Cancel and await network I/O on timeout/fence loss; never orphan a write thread."""
    task = None
    try:
        async with asyncio.timeout(min(timeout, budget.remaining())):
            while True:
                budget.check()
                if budget.run_id.get():
                    budget.heartbeat(state, budget.run_id.get(), force=True)
                    accounting["at"] = budget.accounted_at.get()
                if task is None:
                    task = asyncio.create_task(operation())
                done, _ = await asyncio.wait({task}, timeout=HEARTBEAT_INTERVAL)
                if done:
                    return task.result()
    finally:
        accounting["at"] = budget.accounted_at.get()
        if task is not None:
            if not task.done():
                task.cancel()
            # AsyncClient and MCP contexts close before the caller can finish/recover.
            await asyncio.gather(task, return_exceptions=True)


def invoke(state, config, arguments, headers, key):
    accounting = {}
    try:

        def operation():
            if config["transport"] == "http":
                return http_call(state, config, arguments, headers, key)
            return mcp_session(
                config["endpoint"],
                headers,
                config["timeout_seconds"],
                config["mcp_tool_name"],
                arguments,
            )

        return asyncio.run(supervised(state, operation, config["timeout_seconds"], accounting))
    except Exception:
        raise TransportFailure() from None
    finally:
        # asyncio task contexts do not propagate ContextVar changes to the caller.
        if accounting.get("at") is not None:
            budget.accounted_at.set(accounting["at"])
