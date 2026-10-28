import asyncio
import json

import httpx
import pytest
from conftest import login


def test_embedding_validation_and_credentials(env):
    from app.knowledge.embedding import EmbeddingFailure, binding, embed
    from app.models import ModelConnection
    from app.security import encrypt_secret

    client, app = env
    model = ModelConnection(
        id="test",
        kind="embedding",
        model="emb",
        base_url="https://emb.test/v1",
        api_key_encrypted=encrypt_secret("private-key", app.state.settings),
    )
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 2, 3]}]})

    app.state.http_transport = httpx.MockTransport(handler)
    assert embed(app.state, binding(model), ["text"]) == [[1.0, 2.0, 3.0]]
    assert seen[0].headers["authorization"] == "Bearer private-key"
    assert "private-key" not in seen[0].content.decode()
    for body in [
        {"data": []},
        {"data": [{"embedding": []}]},
        {"data": [{"embedding": [float("inf")]}]},
        {"data": [{"embedding": [1, 2]}, {"embedding": [1, 2]}]},
    ]:
        app.state.http_transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=json.dumps(body))
        )
        with pytest.raises(EmbeddingFailure, match="Embedding service"):
            embed(app.state, binding(model), ["text"])


def test_model_capabilities_real_probes_and_reset(env):
    client, app = env
    headers = login(client)
    model = client.post(
        "/api/v1/admin/models",
        headers=headers,
        json={
            "name": "chat",
            "kind": "chat",
            "base_url": "https://model.test/v1",
            "model": "chat",
            "api_key": "private-key",
        },
    ).json()

    def handler(request):
        data = json.loads(request.content)
        assert data["stream"] is True
        delta = (
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "x",
                        "type": "function",
                        "function": {"name": "capability_probe", "arguments": "{}"},
                    }
                ]
            }
            if "tools" in data
            else {"content": "OK"}
        )
        return httpx.Response(
            200,
            content="data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\ndata: [DONE]\n\n",
        )

    app.state.http_transport = httpx.MockTransport(handler)
    tested = client.post(f"/api/v1/admin/models/{model['id']}/test", headers=headers)
    assert tested.status_code == 200
    assert tested.json()["capabilities"] == {"streaming": True, "tool_calling": True}
    read = client.get("/api/v1/admin/models").json()[0]
    assert read["tested_at"] and read["capabilities"]["streaming"]
    patched = client.patch(
        f"/api/v1/admin/models/{model['id']}", headers=headers, json={"api_key": "rotated"}
    ).json()
    assert patched["capabilities"] == {} and patched["tested_at"] is None
    app.state.http_transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": []})
    )
    failed = client.post(f"/api/v1/admin/models/{model['id']}/test", headers=headers).json()
    assert failed["ok"] is False and "private-key" not in str(failed)


def test_embedding_timeout_is_overall(env):
    from app.knowledge.embedding import EmbeddingFailure, embed

    _, app = env

    async def handler(request):
        await asyncio.sleep(0.3)
        return httpx.Response(200, json={"data": [{"embedding": [1, 0]}]})

    app.state.http_transport = httpx.MockTransport(handler)
    with pytest.raises(EmbeddingFailure):
        embed(
            app.state,
            {"base_url": "https://a.test", "model": "a", "api_key_encrypted": None},
            ["x"],
            timeout=0.03,
        )


def test_embedding_dimensions_and_optional_tool_failure(env):
    from app.knowledge.embedding import EmbeddingFailure, embed

    client, app = env
    headers = login(client)
    model = client.post(
        "/api/v1/admin/models",
        headers=headers,
        json={
            "name": "embedding",
            "kind": "embedding",
            "base_url": "https://emb.test/v1",
            "model": "embedding",
        },
    ).json()
    app.state.http_transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [{"embedding": [1, 2, 3]}]})
    )
    result = client.post(f"/api/v1/admin/models/{model['id']}/test", headers=headers).json()
    assert result["capabilities"] == {"embedding": True, "embedding_dimensions": 3}
    with pytest.raises(EmbeddingFailure):
        embed(
            app.state,
            {"model": "e", "base_url": "https://a.test", "api_key_encrypted": None},
            ["x"],
            dimension=2,
        )
    model = client.post(
        "/api/v1/admin/models",
        headers=headers,
        json={"name": "chat", "kind": "chat", "base_url": "https://chat.test/v1", "model": "chat"},
    ).json()

    def handler(request):
        if "tools" in json.loads(request.content):
            return httpx.Response(400, json={"error": "unsupported"})
        return httpx.Response(
            200, text='data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n'
        )

    app.state.http_transport = httpx.MockTransport(handler)
    result = client.post(f"/api/v1/admin/models/{model['id']}/test", headers=headers).json()
    assert result["ok"] is True
    assert result["capabilities"] == {"streaming": True, "tool_calling": False}
