"""Parse a `codex exec` outcome into the normalized result envelope.

The final answer comes from --output-last-message (stable). The JSONL --json
stream is parsed TOLERANTLY for optional metadata (token usage, session id) only,
so an event-schema change degrades metadata rather than breaking a run."""

from __future__ import annotations

import json

from codex_in_claude import cli_contract
from codex_in_claude.schemas import (
    Finding,
    RateLimitSnapshot,
    RateLimitWindowSnapshot,
    Usage,
)


def parse_rate_limit(events: str) -> RateLimitSnapshot | None:
    """Tolerantly scan JSONL events for the latest rate_limits block. Never raises;
    malformed lines are skipped. Last event carrying the block wins."""
    snapshot: RateLimitSnapshot | None = None
    for raw_line in events.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        found = _find_rate_limit(event)
        if found is not None:
            snapshot = found
    return snapshot


def _find_rate_limit(event: dict) -> RateLimitSnapshot | None:
    blob = event.get(cli_contract.RATE_LIMIT_EVENT_KEY)
    if isinstance(blob, dict):
        snap = _snapshot_from(blob)
        if snap is not None:
            return snap
    for nest in ("msg", "payload", "data"):
        inner = event.get(nest)
        if isinstance(inner, dict):
            found = _find_rate_limit(inner)
            if found is not None:
                return found
    return None


def _snapshot_from(blob: dict) -> RateLimitSnapshot | None:
    primary = _window_from(blob.get("primary"))
    secondary = _window_from(blob.get("secondary"))
    if primary is None and secondary is None:
        return None
    plan = blob.get("plan_type")
    reached = blob.get("rate_limit_reached_type")
    return RateLimitSnapshot(
        plan_type=plan if isinstance(plan, str) else None,
        rate_limit_reached_type=reached if isinstance(reached, str) else None,
        primary=primary,
        secondary=secondary,
    )


def _window_from(blob: object) -> RateLimitWindowSnapshot | None:
    if not isinstance(blob, dict):
        return None
    used = blob.get("used_percent")
    window = blob.get("window_minutes")
    resets = blob.get("resets_at")
    used_f = float(used) if isinstance(used, (int, float)) and not isinstance(used, bool) else None
    window_i = window if isinstance(window, int) and not isinstance(window, bool) else None
    resets_i = (
        int(resets) if isinstance(resets, (int, float)) and not isinstance(resets, bool) else None
    )
    if used_f is None and resets_i is None:
        return None
    return RateLimitWindowSnapshot(used_percent=used_f, window_minutes=window_i, resets_at=resets_i)


def parse_event_metadata(events: str) -> tuple[Usage | None, str | None]:
    """Tolerantly scan JSONL events for token usage and a session id.

    Never raises: malformed lines are skipped. Returns (usage, session_id), either
    of which may be None when the stream did not carry it."""
    usage: Usage | None = None
    session_id: str | None = None
    for raw_line in events.splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        session_id = session_id or _find_session_id(event)
        found = _find_usage(event)
        if found is not None:
            usage = found
    return usage, session_id


def extract_error_message(events: str) -> str | None:
    """Pull a human-readable error from a failed run's JSONL stream.

    Codex reports request/turn failures as `error` / `turn.failed` events on
    stdout (not stderr). The event's `message` is sometimes itself a JSON blob
    ({"error": {"message": ...}}); we unwrap one level so the surfaced text is the
    underlying message rather than escaped JSON. Returns None when no error event
    is present."""
    found: str | None = None
    for raw_line in events.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        marker = str(event.get("type") or "").lower()
        if "error" not in marker and "failed" not in marker:
            continue
        message = event.get("message")
        if isinstance(event.get("error"), dict):
            message = event["error"].get("message", message)
        if isinstance(message, str) and message:
            found = _unwrap_json_message(message)
    return found


def _unwrap_json_message(message: str) -> str:
    """If `message` is itself JSON carrying error.message, return that inner text."""
    text = message.strip()
    if not text.startswith("{"):
        return text
    try:
        blob = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text
    if isinstance(blob, dict) and isinstance(blob.get("error"), dict):
        inner = blob["error"].get("message")
        if isinstance(inner, str) and inner:
            return inner
    return text


def _find_session_id(event: dict) -> str | None:
    for key in ("session_id", "sessionId", "thread_id", "threadId", "conversation_id"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    # Some events nest payload under "msg"/"payload".
    for nest in ("msg", "payload", "data"):
        inner = event.get(nest)
        if isinstance(inner, dict):
            found = _find_session_id(inner)
            if found:
                return found
    return None


def _find_usage(event: dict) -> Usage | None:
    """Pull a token-usage block out of an event, wherever it sits."""
    marker = str(event.get("type") or event.get("msg") or "").lower()
    candidates: list[dict] = []
    if any(m in marker for m in cli_contract.USAGE_EVENT_MARKERS):
        candidates.append(event)
    for key in ("usage", "token_usage", "tokens", "info"):
        inner = event.get(key)
        if isinstance(inner, dict):
            candidates.append(inner)
    for nest in ("msg", "payload", "data"):
        inner = event.get(nest)
        if isinstance(inner, dict):
            for key in ("usage", "token_usage", "tokens"):
                deep = inner.get(key)
                if isinstance(deep, dict):
                    candidates.append(deep)
    for blob in candidates:
        usage = _usage_from(blob)
        if usage is not None:
            return usage
    return None


def _usage_from(blob: dict) -> Usage | None:
    def _int(*names: str) -> int | None:
        for name in names:
            value = blob.get(name)
            if isinstance(value, int):
                return value
        return None

    input_tokens = _int("input_tokens", "prompt_tokens", "input")
    output_tokens = _int("output_tokens", "completion_tokens", "output")
    cached = _int("cached_input_tokens", "cache_read_input_tokens", "cached_tokens")
    total = _int("total_tokens", "total")
    if input_tokens is None and output_tokens is None and total is None:
        return None
    # The current codex CLI emits token_count without a total, so derive it from
    # input + output when both are present (cached is a subset of input, not an
    # addend). An explicit CLI total is still honored verbatim for forward-compat. (#28)
    if total is None and input_tokens is not None and output_tokens is not None:
        total = input_tokens + output_tokens
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached,
        total_tokens=total,
    )


def _strip_code_fence(text: str) -> str:
    """Remove a surrounding ```json ... ``` fence if the model added one."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def parse_structured(last_message: str | None) -> dict | None:
    """Parse the final message as the structured-findings JSON object.

    Returns the dict on success, or None when the message is absent or not a JSON
    object (caller falls back to treating the text as a plain summary)."""
    if not last_message:
        return None
    candidate = _strip_code_fence(last_message)
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def coerce_findings(raw: object) -> list[Finding]:
    """Build validated Findings from the structured payload, dropping malformed
    entries rather than failing the whole result."""
    if not isinstance(raw, list):
        return []
    findings: list[Finding] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            findings.append(Finding.model_validate(item))
        except Exception:
            continue
    return findings
