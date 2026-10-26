"""Bounded, single-attempt DeepSeek transport; no game or credential storage."""
from __future__ import annotations

import http.client
import socket
import ssl
import threading
import time

from football_simulator.press import PressError
from football_simulator.press.repository import canonical
from football_simulator.press.json_output import strict_json, decode_article

MODEL = "deepseek-flash"
DOCUMENTED_VERSION = "DeepSeek-V4.1-Flash-1017"
HOST = "api.deepseek.com"
ENDPOINT = "/chat/completions"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 2 * 1024 * 1024


class ProviderError(PressError):
    """Only fixed, safe codes cross this boundary, never a provider error body."""

    def __init__(self, code, *, status=None, metadata=None):
        self.code, self.status, self.metadata = code, status, metadata
        super().__init__(f"DeepSeek 调用未完成：{code}" + (f"（HTTP {status}）" if status else ""))


def validate_key(key):
    # Header injection and accidental whitespace are rejected before any request.
    if not isinstance(key, str) or not key or len(key) > 512 or any(not 33 <= ord(c) <= 126 for c in key):
        raise ProviderError("missing_or_invalid_credential")


def send_once(payload, key):
    """Official HTTPS only, no redirects/proxy inheritance/retries. Deadline 120s."""
    validate_key(key)
    body = canonical(payload).encode("utf-8")
    if len(body) > MAX_REQUEST_BYTES:
        raise ProviderError("request_too_large")
    connection = http.client.HTTPSConnection(HOST, port=443, timeout=10, context=ssl.create_default_context())
    expired = threading.Event()
    active_socket = None

    def abort():
        expired.set()
        active = active_socket or connection.sock
        if active is not None:
            try:
                active.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            active.close()

    timer = threading.Timer(120, abort)
    timer.daemon = True
    started = time.monotonic()
    timer.start()
    try:
        connection.connect()
        active_socket = connection.sock
        if expired.is_set():
            raise ProviderError("timeout_outcome_unknown")
        # Non-streamed feature articles can legitimately take over 30 seconds
        # before the first response byte. The existing timer remains the total
        # 120-second deadline, including headers and body reads.
        active_socket.settimeout(120)
        connection.request("POST", ENDPOINT, body=body, headers={
            "Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "application/json"})
        response = connection.getresponse()
        status = response.status
        if status != 200:
            code = {400: "bad_request", 401: "authentication", 402: "insufficient_balance", 403: "forbidden",
                    429: "rate_limited"}.get(status, "redirect_refused" if 300 <= status < 400 else "http_error")
            raise ProviderError(code, status=status)
        chunks, size = [], 0
        while True:
            if expired.is_set() or time.monotonic() - started >= 120:
                raise ProviderError("timeout_outcome_unknown")
            block = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
            if not block:
                break
            chunks.append(block)
            size += len(block)
            if size > MAX_RESPONSE_BYTES:
                raise ProviderError("response_too_large")
        if expired.is_set():
            raise ProviderError("timeout_outcome_unknown")
        try:
            return strict_json(b"".join(chunks).decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            raise ProviderError("invalid_response_json") from None
    except ProviderError:
        raise
    except (TimeoutError, socket.timeout):
        raise ProviderError("timeout_outcome_unknown") from None
    except (OSError, http.client.HTTPException):
        raise ProviderError("transport_outcome_unknown") from None
    finally:
        timer.cancel()
        connection.close()


def build_request(request):
    # Closed parameter set: callers cannot change host/model, enable tools or stream.
    payload = {"model": MODEL, "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"},
               "stream": False, "max_tokens": 6144, "messages": request["messages"]}
    if request["model"] != MODEL:
        raise ProviderError("unexpected_requested_model")
    budget=request.get('max_tokens',6144)
    if budget not in (6144,12288):
        raise ProviderError('unexpected_output_budget')
    payload['max_tokens']=budget
    # Honour only the explicit frozen mode. Earlier saved requests remain
    # non-thinking; new editorial requests can use the same model's reasoning.
    if request.get('thinking') == {'type': 'enabled'}:
        effort = request.get('reasoning_effort', 'high')
        if effort not in ('low', 'high', 'max'):
            raise ProviderError('unexpected_reasoning_effort')
        payload['thinking'] = {'type': 'enabled'}
        payload['reasoning_effort'] = effort
    return payload


def complete(request, key, *, transport=send_once):
    """Validate one non-streamed response; article validation is a separate gate."""
    validate_key(key)
    payload = build_request(request)
    result = transport(payload, key)
    if not isinstance(result, dict):
        raise ProviderError("invalid_response_shape")
    if key in canonical(result):
        raise ProviderError("credential_in_response")
    metadata = response_metadata(result)
    try:
        article = parse_article(result, format_receipt=metadata)
    except ProviderError as exc:
        # A truncated/refused generation may still have billed usage.
        exc.metadata = metadata
        raise
    return article, metadata


def parse_article(result, *, format_receipt=None):
    choices = result.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ProviderError("invalid_choices")
    choice = choices[0]
    if choice.get("finish_reason") != "stop":
        raise ProviderError("incomplete_or_refused_output")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant" or message.get("refusal") or message.get("tool_calls"):
        raise ProviderError("invalid_message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ProviderError("empty_output")
    try:
        article, recovery = decode_article(content)
    except (ValueError, RecursionError):
        raise ProviderError("invalid_article_json") from None
    if not isinstance(article, dict) or not article:
        raise ProviderError("invalid_article_json")
    if recovery and format_receipt is not None:
        format_receipt['article_format_recovery'] = recovery
    return article


def response_metadata(result):
    model = result.get("model")
    if not isinstance(model, str) or model.lower() not in (MODEL.lower(), DOCUMENTED_VERSION.lower()):
        raise ProviderError("unexpected_returned_model")
    usage = result.get("usage")
    if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ("prompt_tokens", "completion_tokens", "total_tokens")):
        raise ProviderError("missing_or_invalid_usage")
    if usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
        raise ProviderError("inconsistent_usage")
    safe_usage = {k: usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    for key_name in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        if key_name in usage:
            if type(usage[key_name]) is not int or not 0 <= usage[key_name] <= usage["prompt_tokens"]:
                raise ProviderError("invalid_cache_usage")
            safe_usage[key_name] = usage[key_name]
    metadata = {"requested_model": MODEL, "returned_model": model, "documented_alias_version": DOCUMENTED_VERSION,
                "alias_version_pinned": False, "usage": safe_usage}
    choices = result.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        reason = choices[0].get("finish_reason")
        if reason in ("stop", "length", "content_filter", "tool_calls", "insufficient_system_resource", "aborted"):
            metadata["finish_reason"] = reason
    for field in ("id", "system_fingerprint"):
        value = result.get(field)
        if isinstance(value, str) and len(value) <= 256:
            metadata[field] = value
    return metadata
