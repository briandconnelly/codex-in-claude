"""Shared parameter-contract registry (issue #333).

Some parameters — `idempotency_key`, `reasoning_effort` — carry lengthy lifecycle
or validation semantics. MCP inlines a parameter's description into *every* tool's
`inputSchema`, so a 1 KB description repeated across six tools costs ~6 KB on the
`tools/list` wire. This registry holds each such parameter's description in two
parts:

- ``summary`` — the compressed form that ships inline on the wire. It must stay
  independently sufficient for a correct first call: the first-call selection,
  safety, and spend-critical facts (including any egress/security guarantee the
  parameter carries), plus a pointer to the full home.
- ``full`` — the complete, authoritative semantics, served (uncompressed, once)
  from the ``codex://params`` resource.

Both live on the same :class:`ParamContract`, authored and reviewed together, so
the inline summary and the resource body cannot drift into two independent
sources of truth. The server builds each `Field(description=...)` from ``summary``
and the resource body from ``full``.

This is codex-specific surface (the semantics describe *these* tools), so it lives
in the package rather than in ``_core``.
"""

from __future__ import annotations

from dataclasses import dataclass

from codex_in_claude import config

PARAMS_RESOURCE_URI = "codex://params"


@dataclass(frozen=True)
class ParamContract:
    """A parameter's compressed inline ``summary`` and authoritative ``full`` text."""

    name: str
    summary: str
    full: str


# The full idempotency_key contract (the pre-#333 inline text, verbatim), now the
# authoritative resource body. The inline summary below carries the load-bearing
# subset; everything here stays discoverable at codex://params.
_IDEMPOTENCY_FULL = (
    "Optional client-supplied dedup key, scoped to THIS concrete tool on the same "
    "workspace. Reusing it on the same tool with the same arguments replays the existing "
    "run instead of starting — and paying for — a duplicate Codex call (a sync call "
    "reattaches to the in-flight run and returns its result; an _async call returns the "
    "same job_id). The sync and _async variants are DIFFERENT tools and never share a "
    "key's run. Reuse with different arguments — including a different timeout_seconds — "
    "is refused (idempotency_conflict); a key whose prior result was already "
    "consumed/evicted is idempotency_result_unavailable; a still-publishing reservation "
    "is idempotency_in_progress (retry). Omit it for the prior no-dedup behavior. A "
    "completed result stays replayable while its job record lives (its TTL), subject to "
    "consumption or count-eviction; the fail-closed conflict/in-progress window can last "
    "longer — up to the job's max runtime + termination grace + TTL. "
    "meta.idempotency_replayed=true marks a replayed (unpaid) response."
)

_REASONING_EFFORT_FULL = (
    "Override the Codex reasoning effort for this call (sent as a "
    "`model_reasoning_effort` config override); omit (or pass null) for the server "
    "default (CODEX_IN_CLAUDE_REASONING_EFFORT) or Codex's own resolution. An open "
    "per-model string the Codex backend validates at run time — commonly "
    "minimal|low|medium|high|xhigh; codex_models lists each model's advertised set "
    "(advisory). A backend-rejected value fails as invalid_reasoning_effort (repair steers "
    "to codex_models); an explicit empty string is sent as-is (and rejected by the backend), "
    "never treated as unset. Control characters, surrogates, and values over "
    f"{config.REASONING_EFFORT_MAX_LENGTH} chars in this per-call argument are rejected at "
    "the MCP boundary as invalid_arguments; the same hostile shape in the resolved "
    "CODEX_IN_CLAUDE_REASONING_EFFORT default — which never crosses that boundary — is "
    "instead refused pre-spend as invalid_reasoning_effort (repair: correct the config; "
    "zero spend, the value never reaches codex)."
)


PARAMETER_CONTRACTS: dict[str, ParamContract] = {
    "idempotency_key": ParamContract(
        name="idempotency_key",
        # Kept inline (selection-critical + the spend guarantee): tool+workspace scope,
        # same-args unpaid replay, different-args conflict, sync/_async never share a
        # key, omit=no dedup, retention is BOUNDED. Moved to the resource: the in-progress
        # / result-unavailable states, the exact TTL/grace horizons, the replay marker.
        summary=(
            "Optional client dedup key, scoped to THIS tool + workspace. Same key with "
            "identical arguments replays the prior run's result WITHOUT paying for a new "
            "Codex call; different arguments (including a different timeout_seconds) are "
            "refused with idempotency_conflict. The sync and _async forms are separate "
            "tools and never share a key. Omit for no dedup; retention is bounded, not "
            "indefinite. Full lifecycle (replay window, idempotency_in_progress / "
            "idempotency_result_unavailable, TTL & eviction, the meta.idempotency_replayed "
            "marker): codex://params."
        ),
        full=_IDEMPOTENCY_FULL,
    ),
    "reasoning_effort": ParamContract(
        name="reasoning_effort",
        # The max_length/pattern bounds are enforced by the Field itself, so the summary
        # need not restate them; the backend-rejection behavior moves to the resource.
        summary=(
            "Override the Codex reasoning effort for this call (a model_reasoning_effort "
            "override); omit or pass null for the server default "
            "(CODEX_IN_CLAUDE_REASONING_EFFORT) or Codex's own resolution. An open, "
            "per-model string the backend validates at run time — commonly "
            "minimal|low|medium|high|xhigh; codex_models lists each model's advertised "
            "set (advisory). Rejection and bounds detail: codex://params."
        ),
        full=_REASONING_EFFORT_FULL,
    ),
}


def resource_body() -> dict:
    """The ``codex://params`` payload: every parameter's ``summary`` and ``full``."""
    return {
        "description": (
            "Full semantics for parameters whose tools/list description is a compressed "
            "summary (issue #333). Each entry's `summary` is what ships inline on the "
            "wire; `full` is the authoritative contract."
        ),
        "params": {
            name: {"summary": c.summary, "full": c.full} for name, c in PARAMETER_CONTRACTS.items()
        },
    }
