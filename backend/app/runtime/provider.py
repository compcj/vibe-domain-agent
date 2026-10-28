"""OpenAI-compatible SSE adapter; only validated deltas cross the member boundary."""

import codecs
import json

import httpx

from app.runtime import budget
from app.runtime.store import event, live_access, lock_run
from app.security import decrypt_secret

MAX_CONTEXT = 200_000
MAX_OUTPUT = 100_000


class SafeFailure(Exception):
    pass


def stream_completion(state, run_id, messages, tools):
    budget.check()
    with state.session_factory() as db:
        run = lock_run(db, run_id)
        live_access(db, run)
        config = run.snapshot
        model = config["model"]
        if "api_key_encrypted" not in model:
            raise SafeFailure("Run credential binding is unavailable; start a new request")
        key = (
            decrypt_secret(model["api_key_encrypted"], state.settings)
            if model["api_key_encrypted"]
            else ""
        )
    if len(json.dumps(messages)) > MAX_CONTEXT:
        raise SafeFailure("Conversation context exceeds the configured size limit")
    payload = {
        "model": config["model"]["model"],
        "messages": messages,
        "temperature": config.get("temperature", 0.7),
        "max_tokens": config.get("max_tokens", 4096),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        payload["tools"] = tools
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    content, calls, usage = "", {}, None
    sanitizer = StreamingRedactor(key)
    try:
        with httpx.Client(
            timeout=httpx.Timeout(min(60, max(1, budget.remaining())), connect=10),
            follow_redirects=False,
            trust_env=False,
            transport=getattr(state, "http_transport", None),
        ) as client:
            with client.stream(
                "POST",
                config["model"]["base_url"].rstrip("/") + "/chat/completions",
                json=payload,
                headers=headers,
            ) as response:
                if response.status_code != 200:
                    raise SafeFailure("Model service is unavailable")
                total = 0
                for line in bounded_lines(response, state, run_id):
                    budget.heartbeat(state, run_id)
                    budget.check()
                    total += len(line)
                    if total > MAX_OUTPUT * 8:
                        raise SafeFailure("Model response exceeds the size limit")
                    with state.session_factory() as db:
                        run = lock_run(db, run_id)
                        if run.cancel_requested:
                            raise SafeFailure("Run cancelled")
                        live_access(db, run)
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    item = json.loads(raw)
                    if item.get("usage"):
                        usage = item["usage"]
                    for choice in item.get("choices", []):
                        if choice.get("index", 0) != 0:
                            continue
                        delta = choice.get("delta", {})
                        text = sanitizer.feed(delta.get("content") or "")
                        if text:
                            content += text
                            if len(content) > MAX_OUTPUT:
                                raise SafeFailure("Model response exceeds the size limit")
                            with state.session_factory() as db:
                                event(db, run_id, "delta", {"text": text})
                                db.commit()
                        for part in delta.get("tool_calls", []):
                            index = part["index"]
                            if index not in calls:
                                if len(calls) >= 20:
                                    raise SafeFailure("Too many tool calls")
                                calls[index] = {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            target = calls[index]
                            if part.get("id"):
                                target["id"] += part["id"]
                            for field in ("name", "arguments"):
                                target["function"][field] += part.get("function", {}).get(field, "")
    except SafeFailure:
        raise
    except Exception:
        raise SafeFailure("Model service returned an invalid or unavailable response") from None
    tail = sanitizer.finish()
    if tail:
        content += tail
        with state.session_factory() as db:
            event(db, run_id, "delta", {"text": tail})
            db.commit()
    if key and key in json.dumps(calls):
        raise SafeFailure("Model returned sensitive connector data")
    if usage:
        with state.session_factory() as db:
            run = lock_run(db, run_id)
            for source, field in (
                ("prompt_tokens", "input_tokens"),
                ("completion_tokens", "output_tokens"),
            ):
                value = usage.get(source)
                if isinstance(value, int) and value >= 0:
                    setattr(run, field, (getattr(run, field) or 0) + value)
            db.commit()
    result = {"role": "assistant", "content": content}
    if calls:
        result["tool_calls"] = list(calls.values())
    return result


def bounded_lines(response, state, run_id):
    decoder = codecs.getincrementaldecoder("utf-8")()
    pending, total = "", 0
    for chunk in response.iter_bytes():
        budget.heartbeat(state, run_id)
        budget.check()
        total += len(chunk)
        if total > MAX_OUTPUT * 8:
            raise SafeFailure("Model response exceeds the size limit")
        pending += decoder.decode(chunk)
        lines = pending.split("\n")
        pending = lines.pop()
        for line in lines:
            yield line.rstrip("\r")
    pending += decoder.decode(b"", final=True)
    if pending:
        yield pending.rstrip("\r")


class StreamingRedactor:
    """Retain only a possible secret prefix, so ordinary tokens stream immediately."""

    def __init__(self, secret):
        self.secret, self.pending = secret, ""

    def feed(self, text):
        if not self.secret:
            return text
        value = (self.pending + text).replace(self.secret, "[redacted]")
        keep = 0
        for length in range(1, min(len(self.secret), len(value) + 1)):
            if value.endswith(self.secret[:length]):
                keep = length
        self.pending = value[-keep:] if keep else ""
        return value[:-keep] if keep else value

    def finish(self):
        value, self.pending = self.pending, ""
        return value
