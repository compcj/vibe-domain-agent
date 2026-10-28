"""Classify bounded upstream failures without exposing provider content or configuration."""

import json

import httpx

from app.runtime import budget

MAX_ERROR_BYTES = 16_384


def context_limit_error(response, state, run_id):
    # HTTPX decodes before yielding iter_bytes(); do not expand an untrusted
    # compressed error body just to provide a more specific diagnostic.
    if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
        return False
    body = bytearray()
    try:
        for chunk in response.iter_bytes():
            budget.heartbeat(state, run_id)
            budget.check()
            if len(body) + len(chunk) > MAX_ERROR_BYTES:
                return False
            body.extend(chunk)
        value = json.loads(body)
    except (httpx.HTTPError, ValueError):
        return False
    if not isinstance(value, dict):
        return False
    error = value.get("error", value)
    if not isinstance(error, dict):
        return False
    if error.get("code") == "context_length_exceeded":
        return True
    message = error.get("message")
    return isinstance(message, str) and "maximum context length" in message.lower()


def model_http_error(response, state, run_id):
    status = response.status_code
    if status in {400, 422}:
        if context_limit_error(response, state, run_id):
            message = (
                "Model context limit exceeded. Shorten the conversation or attachments, "
                "or ask an administrator to adjust the context/output limits."
            )
        else:
            message = (
                "Model request was rejected. Ask an administrator to check the model "
                "service's request parameters and chat template."
            )
    elif status == 401:
        message = "Model service authentication failed. Contact an administrator."
    elif status == 403:
        message = "Model service denied access. Contact an administrator."
    elif status == 404:
        message = "Model service resource was not found. Contact an administrator."
    elif status == 413:
        message = "Model request is too large. Reduce the conversation or attachments."
    elif status == 429:
        message = (
            "Model service rate limit or quota exceeded. Try later or contact an administrator."
        )
    elif status >= 500:
        message = "Model service is temporarily unavailable. Try later or contact an administrator."
    else:
        message = "Model service returned an unexpected HTTP response. Contact an administrator."
    return f"{message} (HTTP {status})"
