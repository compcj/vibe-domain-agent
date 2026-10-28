"""Real official MCP server/client protocol roundtrip on loopback; no external secrets."""

import asyncio
import socket
import threading
import time

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP


@pytest.fixture
def mcp_endpoint():
    server = FastMCP("test", stateless_http=True, json_response=True)
    calls = []

    @server.tool()
    def lookup(item: str) -> str:
        calls.append(item)
        return "found:" + item

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(server.streamable_http_app(), log_level="critical")
    runner = uvicorn.Server(config)
    thread = threading.Thread(target=lambda: runner.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not runner.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner.started
    try:
        yield f"http://127.0.0.1:{port}/mcp", calls
    finally:
        runner.should_exit = True
        thread.join(timeout=5)
        sock.close()


def test_mcp_discovery_does_not_register_and_execution_uses_sdk(env, mcp_endpoint):
    from app.runtime.transports import mcp_session
    from conftest import login

    client, _ = env
    endpoint, calls = mcp_endpoint
    headers = login(client)
    response = client.post(
        "/api/v1/admin/tools/discover", json={"endpoint": endpoint}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()[0]["name"] == "lookup"
    assert response.json()[0]["inputSchema"]["type"] == "object"
    assert client.get("/api/v1/admin/tools").json() == []
    assert calls == []
    result = asyncio.run(mcp_session(endpoint, {}, 5, "lookup", {"item": "x"}))
    assert "found:x" in result
    assert calls == ["x"]
