"""Admin capability probes return booleans only, never provider content."""

import json

from app.knowledge.embedding import binding, embed, network, request_bytes


def streaming_probe(state, config, tools=False):
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "user", "content": "Call capability_probe." if tools else "Reply OK."}
        ],
        "max_tokens": 64,
        "stream": True,
    }
    if tools:
        payload.update(
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "capability_probe",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": "capability_probe"}},
        )
    body = network(state, lambda: request_bytes(state, config, "chat/completions", payload, 15), 15)
    found, ended, calls = False, False, {}
    for line in body.decode("utf-8").splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            ended = True
            break
        value = json.loads(data)
        for choice in value.get("choices", []):
            delta = choice.get("delta", {})
            if isinstance(delta.get("content"), str) and delta["content"].strip():
                found = True
            for call in delta.get("tool_calls", []):
                item = calls.setdefault(call.get("index", 0), {"name": "", "arguments": ""})
                item["name"] += call.get("function", {}).get("name", "")
                item["arguments"] += call.get("function", {}).get("arguments", "")
    if tools:
        return ended and any(
            c["name"] == "capability_probe" and json.loads(c["arguments"]) == {}
            for c in calls.values()
        )
    return found and ended


def probe(state, model):
    config = binding(model)
    capabilities = {}
    try:
        if model.kind == "embedding":
            vector = embed(state, config, ["Connection test"], timeout=15)[0]
            capabilities = {"embedding": True, "embedding_dimensions": len(vector)}
            ok = True
        else:
            ok = streaming_probe(state, config)
            capabilities["streaming"] = ok
            if ok:
                try:
                    capabilities["tool_calling"] = streaming_probe(state, config, True)
                except Exception:
                    capabilities["tool_calling"] = False
    except Exception:
        ok = False
    return {
        "ok": ok,
        "message": "Connection succeeded" if ok else "Connection test failed",
        "capabilities": capabilities,
    }
