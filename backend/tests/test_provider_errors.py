import gzip
import json

import httpx
import pytest
from sqlalchemy import select
from test_runtime import conversation, start


@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        (400, {"message": "This model's maximum context length is 8192 tokens"}, "context limit"),
        (400, {"code": "context_length_exceeded"}, "context limit"),
        (
            400,
            {"message": "Conversation roles must alternate user/assistant"},
            "request was rejected",
        ),
        (401, {}, "authentication"),
        (403, {}, "denied access"),
        (404, {}, "not found"),
        (413, {}, "too large"),
        (429, {}, "rate limit"),
        (503, {}, "temporarily unavailable"),
    ],
)
def test_provider_rejection_is_actionable_without_exposing_private_response(
    runtime_env, caplog, status, error, expected
):
    from app.knowledge.indexing import index_document
    from app.models import RunEvent
    from app.runtime.engine import execute

    client, app, headers, ids, dispatched = runtime_env
    app.state.dispatch_index = lambda identifier: None
    cid = conversation(client, headers, ids)
    doc = client.post(
        "/api/v1/files",
        headers=headers,
        data={"conversation_id": cid},
        files={"file": ("reference.md", b"# Reference\n" + b"Domain facts. " * 5500)},
    ).json()
    index_document(app.state, doc["id"])
    rid = start(client, headers, cid, attachment_ids=[doc["id"]]).json()["id"]
    calls = []
    private = "private-api-key private-endpoint private-model private-prompt"

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(status, json={"error": {**error, "private": private}})

    app.state.http_transport = httpx.MockTransport(handler)
    execute(app.state, rid)
    execute(app.state, rid)  # Delivery of a terminal run must not retry the model.
    run = client.get(f"/api/v1/runs/{rid}").json()
    assert run["status"] == "failed"
    assert expected in run["error"]
    assert f"HTTP {status}" in run["error"]
    assert len(calls) == 1
    assert len(dispatched) == 1
    assert "Domain facts" in json.dumps(calls[0]["messages"])
    with app.state.session_factory() as db:
        references = db.scalars(
            select(RunEvent).where(RunEvent.run_id == rid, RunEvent.kind == "citation")
        ).all()
        assert len(references) > 1  # File chunks, not repeated model requests.
        assert len({row.data["id"] for row in references}) == len(references)
    replay = client.get(f"/api/v1/runs/{rid}/events").text
    assert expected in replay
    assert replay.count("event: error") == 1
    assert replay.count("event: done") == 1
    for value in private.split():
        assert value not in str(run) + replay + caplog.text


def test_oversized_upstream_error_body_is_not_fully_read(runtime_env):
    from app.runtime.engine import execute

    client, app, headers, ids, _ = runtime_env
    read = []

    class LargeError(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(1000):
                read.append(1)
                yield b"x" * 4096

    app.state.http_transport = httpx.MockTransport(
        lambda request: httpx.Response(400, stream=LargeError())
    )
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    execute(app.state, rid)
    error = client.get(f"/api/v1/runs/{rid}").json()["error"]
    assert "HTTP 400" in error
    assert len(read) <= 5


def test_compressed_upstream_error_is_not_decoded(runtime_env):
    from app.runtime.engine import execute

    client, app, headers, ids, _ = runtime_env
    read = []
    compressed = gzip.compress(b"x" * 1024 * 1024)

    class CompressedError(httpx.SyncByteStream):
        def __iter__(self):
            read.append(True)
            yield compressed

    app.state.http_transport = httpx.MockTransport(
        lambda request: httpx.Response(
            400, headers={"Content-Encoding": "gzip"}, stream=CompressedError()
        )
    )
    rid = start(client, headers, conversation(client, headers, ids)).json()["id"]
    execute(app.state, rid)
    error = client.get(f"/api/v1/runs/{rid}").json()["error"]
    assert "HTTP 400" in error
    assert read == []
