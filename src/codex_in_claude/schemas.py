"""Pydantic models for the normalized tool result contract."""

from __future__ import annotations

import math
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from codex_in_claude._core.jobs import DEFAULT_POLL_AFTER_MS

# Bump this whenever the agent-visible surface changes: tool names, input or
# output schemas, the ErrorCode set, the tier/sandbox/isolation/scope value sets,
# or the capability guarantees. Clients cache by it.
FINGERPRINT = "codex-in-claude/0.1/schema-13"

# Default poll/backoff interval (ms) shared by job handles and the job_running
# error's retry_after_ms, so the "when to retry" hint stays consistent in one place.
# Sourced from _core (the lower layer that owns the value) so a live job record's
# poll_after_ms and this constant can never drift.
JOB_POLL_AFTER_MS = DEFAULT_POLL_AFTER_MS

Severity = Literal["critical", "high", "medium", "low", "nit"]
Verdict = Literal["pass", "concerns", "fail", "unknown"]
Confidence = Literal["low", "medium", "high"]
# Intent tier: read-only consult, write-in-worktree propose (diff returned, not
# applied), or write-in-place apply (explicit opt-in).
Tier = Literal["consult", "propose", "apply"]
Sandbox = Literal["read-only", "workspace-write", "danger-full-access"]
# Isolation maps to Codex flags: inherit (none), ignore-config
# (--ignore-user-config), ignore-rules (--ignore-user-config --ignore-rules).
Isolation = Literal["inherit", "ignore-config", "ignore-rules"]
ReviewScope = Literal["working_tree", "branch", "commit"]
Detail = Literal["summary", "full"]
# Lifecycle states for a background job. Terminal: done|failed|cancelled|timeout.
# (TTL-expired records are deleted and reported as job_not_found, not a state.)
JobState = Literal["running", "done", "failed", "cancelled", "timeout"]
# Per-tool maturity, advertised as discovery metadata in codex_capabilities. NOT the
# consult/propose/apply intent `Tier`. Omitted (None) means the tool inherits the
# server-wide `stability` ("alpha"); a value flags a tool that differs from that norm.
ToolStability = Literal["stable", "preview", "experimental"]


def workspace_warning_for(source: str | None, cwd: str) -> str | None:
    """Warning when the workspace was resolved from the server's own cwd.

    The MCP server process launches from its install directory, so a cwd-resolved
    workspace silently targets the wrong repo. Surfacing this (rather than failing)
    lets agents notice and pass workspace_root without breaking existing callers."""
    if source == "cwd":
        return (
            f"workspace resolved from the server's own cwd ({cwd}); pass "
            "workspace_root (or configure an MCP root) to be sure the task "
            "targets the intended repository"
        )
    return None


def apply_detail(envelope: dict, detail: str) -> dict:
    """Trim a SUCCESS envelope to the requested detail level (#56).

    `detail="full"` returns the envelope unchanged — the full raw model output and
    complete metadata, for diagnostics. `detail="summary"` (the default) omits the
    often-large, duplicative raw model text (`raw_response.text`); the structured
    fields (`summary`/`findings`/`verdict`/`diff`/…) stay authoritative and the
    parser shape is unchanged (`raw_response` remains present with its `text` nulled,
    and `session_id`/`model` are still echoed there and in `meta`). Error envelopes
    (`ok` != True) are returned unchanged. Mutates and returns the same dict."""
    if detail == "full" or envelope.get("ok") is not True:
        return envelope
    raw = envelope.get("raw_response")
    if isinstance(raw, dict):
        raw["text"] = None
    return envelope


ErrorCode = Literal[
    # Setup / auth
    "codex_not_found",
    "codex_auth_required",
    "unexpanded_env_placeholder",
    # Configuration
    "unsupported_tier",
    "unsupported_sandbox",
    "unsupported_isolation",
    "unsupported_detail",
    "invalid_scope",
    "invalid_base",
    "invalid_commit",
    "invalid_paths",
    "invalid_workspace_root",
    "workspace_outside_roots",
    "input_too_large",
    # Git / worktree
    "not_a_git_repo",
    "git_unavailable",
    "worktree_error",
    "context_too_large",
    # Runtime
    "timeout",
    "nonzero_exit",
    "invalid_json",
    "schema_violation",
    "internal_error",
    # The installed `codex` rejected a flag/value this plugin sends — its CLI
    # contract drifted and the plugin likely needs an update.
    "cli_contract_changed",
    # codex hit a usage/rate limit (ChatGPT window or API-key 429). Transient and
    # retryable; the error carries retry_after_ms as the suggested backoff.
    "codex_rate_limited",
    # Background-job lifecycle errors:
    "job_not_found",
    "job_running",
    "job_cancelled",
    "job_timeout",
    "job_failed",
]


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    total_tokens: int | None = None


class RateLimitWindowSnapshot(BaseModel):
    """Raw per-window quota as emitted by codex's token_count event (one of the
    primary/secondary windows). Parsed tolerantly; unknown fields ignored.

    Field validators (mode="before") enforce numeric bounds on all three fields so
    both the live-parse path (normalize._window_from) and the cache-read path
    (RateLimitSnapshot.model_validate) are covered in one place:
    - used_percent: must be a finite float in [0, 100]; out-of-range or non-finite
      → None (treated as absent — never clamped to a valid-looking value).
    - resets_at / window_minutes: must be a finite numeric; non-finite → None.
    """

    model_config = ConfigDict(extra="ignore")
    used_percent: float | None = None
    window_minutes: int | None = None
    resets_at: int | None = None  # epoch seconds

    @field_validator("used_percent", mode="before")
    @classmethod
    def _validate_used_percent(cls, v: object) -> float | None:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        if not math.isfinite(float(v)):
            return None
        fv = float(v)
        if fv < 0.0 or fv > 100.0:
            return None
        return fv

    @field_validator("resets_at", mode="before")
    @classmethod
    def _validate_resets_at(cls, v: object) -> int | None:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        if not math.isfinite(float(v)):
            return None
        return int(v)

    @field_validator("window_minutes", mode="before")
    @classmethod
    def _validate_window_minutes(cls, v: object) -> int | None:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        if not math.isfinite(float(v)):
            return None
        return int(v)


class RateLimitSnapshot(BaseModel):
    """Raw rate_limits block from a token_count event; what we persist/replay."""

    model_config = ConfigDict(extra="ignore")
    plan_type: str | None = None
    rate_limit_reached_type: str | None = None
    primary: RateLimitWindowSnapshot | None = None  # 5-hour window
    secondary: RateLimitWindowSnapshot | None = None  # weekly window


RateLimitStatus = Literal["available", "limited", "exhausted", "unknown"]


class RateLimitWindow(BaseModel):
    """One quota window, interpreted for an agent. used_percent/remaining_percent are
    current-ish (as of `as_of`) for an open window and are NULLED when reset_passed is
    true (the window rolled over since capture, so its captured usage is obsolete and
    its post-reset usage is unobserved). One source of truth: a present percentage
    always means current-ish, never stale."""

    model_config = ConfigDict(extra="forbid")
    used_percent: float | None = None
    remaining_percent: float | None = None  # max(0, 100 - used_percent); None if reset_passed
    window_minutes: int | None = None
    resets_at: int | None = None  # epoch seconds
    seconds_until_reset: int | None = None  # clamped ≥ 0; 0 when reset_passed; None if no resets_at
    reset_passed: bool = False


class RateLimit(BaseModel):
    """Agent-facing rate-limit quota. A snapshot captured opportunistically from a
    paid call, interpreted against each window's reset clock. NOT a live query.

    Asymmetric by design: `available` is reported only when every binding window is
    observed and healthy; an unobserved window (reset-passed, missing, or lacking
    resets_at) never yields `available` — it degrades to `unknown`. `limited`/
    `exhausted` come only from still-open windows, so they stay conservative even when
    the snapshot is stale (captured usage is a lower bound on current usage).
    `unknown` means no fresh/usable reading yet — run any paid Codex call to populate
    it — not that anything is wrong."""

    model_config = ConfigDict(extra="forbid")
    status: RateLimitStatus
    source: Literal["current_run", "plugin_cache"] = "plugin_cache"
    as_of: str | None = None  # ISO-8601 capture time; None when no snapshot
    age_seconds: int | None = None
    is_stale: bool = False  # older than the configured warn threshold (advisory)
    plan_type: str | None = None  # captured metadata, NOT a verified current plan
    home_unverified: bool = False  # cached CODEX_HOME differs from the current environment
    limiting_window: Literal["primary", "secondary"] | None = None
    primary: RateLimitWindow | None = None  # 5-hour window
    secondary: RateLimitWindow | None = None  # weekly window
    note: str | None = None


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Severity
    title: str
    file: str | None = None
    line: int | None = None
    line_end: int | None = None  # end line when the finding spans a range (line = start)
    evidence: str
    risk: str
    recommendation: str


class RawResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = None
    session_id: str | None = None
    model: str | None = None


class ContextSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0


class Workspace(BaseModel):
    """Compact workspace-resolution context for job-lifecycle success responses.

    Mirrors the `cwd`/`workspace_source`/`workspace_warning` fields the full `Meta`
    envelope carries, so a successful `codex_job_status`/`codex_job_list` call shows
    which repository the lookup targeted (and warns when it fell back to the server's
    own cwd) — making wrong-workspace polling diagnosable rather than a silent empty
    list or `job_not_found` (#54)."""

    model_config = ConfigDict(extra="forbid")
    cwd: str
    workspace_source: str | None = None  # how cwd was resolved: param|roots|cwd
    workspace_warning: str | None = None  # set when cwd was resolved from server cwd


class Meta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cwd: str
    workspace_source: str | None = None  # how cwd was resolved: param|roots|cwd
    workspace_warning: str | None = None  # set when cwd was resolved from server cwd
    tier: Tier
    sandbox: Sandbox
    isolation: Isolation
    model: str | None = None
    scope: str | None = None  # review scope: working_tree|branch|commit
    base: str | None = None
    commit: str | None = None
    paths: list[str] | None = None
    timeout_seconds: int
    elapsed_ms: int
    command_exit_code: int | None = None
    session_id: str | None = None  # Codex session id, when one was emitted
    truncated: bool = False
    truncation_hint: str | None = None
    # Optional `codex` flags this server dropped because the installed CLI did not
    # advertise them in --help (e.g. ["--model"]). Empty in the common case;
    # informational — guarantee-bearing flags are never dropped, only depth ones.
    compat_warnings: list[str] = Field(default_factory=list)
    # Advisory security posture warnings detected before launching Codex.
    security_warnings: list[str] = Field(default_factory=list)
    redacted_paths: list[str] = Field(default_factory=list)
    usage: Usage | None = None
    # Live rate-limit quota snapshot captured from this call's event stream (the same
    # data codex_status reports from cache). None when codex emitted no rate_limits block.
    rate_limit: RateLimit | None = None
    context_summary: ContextSummary | None = None
    job_id: str | None = None  # set on background-job results; None for sync calls
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    fingerprint: str = FINGERPRINT


class _SuccessBase(BaseModel):
    """Fields shared by every success envelope. `verdict`/`confidence` live only on
    the review result — they are a review judgment, meaningless for Q&A (consult) or
    a worktree diff (delegate), so those tools must not carry them (#31)."""

    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    raw_response: RawResponse = Field(default_factory=RawResponse)
    meta: Meta


class ConsultResult(_SuccessBase):
    """codex_consult: a read-only answer/second opinion. No verdict/confidence/diff."""

    tool: Literal["codex_consult"] = "codex_consult"


class ReviewResult(_SuccessBase):
    """codex_review_changes: a structured review. The only verdict-bearing result."""

    tool: Literal["codex_review_changes"] = "codex_review_changes"
    verdict: Verdict = "unknown"
    confidence: Confidence = "medium"


class DelegateResult(_SuccessBase):
    """codex_delegate(_async): a proposed change. Carries the unified `diff` (confined
    to a temp worktree and NOT applied to the live tree); no verdict/confidence."""

    tool: Literal["codex_delegate"] = "codex_delegate"
    diff: str | None = None


class ErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: ErrorCode
    message: str
    repair: str  # prose guidance; the structured fields below are the machine-actionable form
    offending_param: str | None = None
    retryable: bool = False
    # Machine-actionable repair metadata — set when known so an agent can recover
    # without parsing `repair` prose. All optional/backward-compatible.
    allowed_values: list[str] | None = None  # concrete valid values for an enum-like param
    repair_tool: str | None = None  # a tool to call to recover (e.g. codex_job_status)
    # args for repair_tool, e.g. {"job_id": ...}; values are arbitrary JSON since tool
    # arguments aren't all strings.
    repair_tool_params: dict[str, Any] | None = None
    retry_after_ms: int | None = None  # suggested backoff before retrying a retryable error
    # Size context for input_too_large, so an agent can trim by an exact amount without
    # parsing the message prose (#95).
    limit_bytes: int | None = None  # the byte limit that was exceeded
    actual_bytes: int | None = None  # the offending input's actual byte size
    # For workspace_outside_roots: the client-supplied MCP roots a valid workspace_root
    # must sit inside. Populated ONLY from roots the client already provided — never
    # arbitrary local filesystem paths — so it leaks no extra path context (#95).
    candidate_roots: list[str] | None = None


class ErrorResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: Literal[False] = False
    error: ErrorInfo
    meta: Meta


class ResolvedDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: Tier
    sandbox: Sandbox
    isolation: Isolation
    model: str | None = None
    timeout_seconds: int
    timeout_bounds: list[int]  # [min, max] clamp range for timeout_seconds


class RawDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: str
    sandbox: str
    isolation: str
    model: str | None = None
    timeout_seconds: int


class StatusResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    codex_found: bool
    codex_version: str | None = None
    # Readiness probes (all free — no model call):
    codex_authenticated: bool | None = None  # None = could not determine
    auth_detail: str | None = None  # non-identifying method phrase (ChatGPT / API key)
    version_supported: bool | None = None
    version_warning: str | None = None  # advisory; never blocks
    flags_warning: str | None = None  # a guarantee-bearing flag missing from --help
    ready: bool = False  # found AND authenticated
    readiness_detail: str
    raw_defaults: RawDefaults
    resolved_defaults: ResolvedDefaults
    default_errors: list[ErrorInfo] = Field(default_factory=list)
    rate_limit: RateLimit = Field(  # always present; status 'unknown' when no cache
        default_factory=lambda: RateLimit(status="unknown")
    )
    caveat: str
    fingerprint: str = FINGERPRINT


class AsyncLifecycle(BaseModel):
    """Structured discovery metadata for an *_async tool that runs as a background job
    via this server's *custom* job lifecycle rather than native MCP tasks/progress (#94).

    Lets a client that looks specifically for native MCP tasks or `notifications/progress`
    infer their absence structurally (not just from description prose), and discover the
    exact poll/result/consume/cancel/list tools and the JobStatus fields to branch on."""

    model_config = ConfigDict(extra="forbid")
    # No native MCP task object and no notifications/progress streaming — the run is
    # polled via the codex_job_* tools below. These are fixed for this server.
    native_task_support: Literal[False] = False
    progress_support: Literal["none"] = "none"
    lifecycle: Literal["codex_job_*"] = "codex_job_*"
    # The job-lifecycle tools to drive the run after the *_async call returns a job_id.
    poll_tool: str  # codex_job_status
    result_tool: str  # codex_job_result
    consume_tool: str  # codex_job_consume_result
    cancel_tool: str  # codex_job_cancel
    list_tool: str  # codex_job_list
    # JobStatus fields a client branches on while polling.
    status_field: str  # "status" — the lifecycle state
    result_ready_field: str  # "result_available" — true once the result can be fetched
    poll_after_field: str  # "poll_after_ms" — backoff to honor before the next poll


class ToolCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    cost: Literal["free", "active"]
    # Per-tool maturity (advisory). None ⇒ inherits the server-wide `stability`; set
    # only when a tool is more experimental than that norm (e.g. the async/job surface).
    stability: ToolStability | None = None
    use_when: str
    required_params: list[str] = Field(default_factory=list)
    key_optional_params: list[str] = Field(default_factory=list)
    returns: str
    # Error codes this tool may return. Advisory, not exhaustive: a guide for
    # branching/recovery, not a closed contract. Typed as ErrorCode so the schema
    # advertises the valid code set and entries are checked statically.
    error_codes: list[ErrorCode] = Field(default_factory=list)
    # Set only on the *_async tools: how to drive their background-job lifecycle, and
    # that the server uses that custom lifecycle instead of native MCP tasks/progress
    # (#94). None ⇒ a synchronous/lifecycle tool that needs no such metadata.
    async_lifecycle: AsyncLifecycle | None = None


class CapabilitiesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    name: str
    version: str
    fingerprint: str = FINGERPRINT
    transport: str
    stability: str
    active_tools: list[str]
    free_tools: list[str]
    tool_details: list[ToolCapability] = Field(default_factory=list)
    tiers: list[str]
    sandboxes: list[str]
    scope: list[str]  # what this server is for
    negative_scope: list[str]  # what it deliberately does NOT do
    prerequisites: list[str]
    deprecation_policy: str


ModelCatalogSource = Literal["cache", "static", "none"]


class ModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    display_name: str | None = None


class ModelCatalogResult(BaseModel):
    """Advisory list of Codex model slugs for the optional `model` param.

    Discovery only: `source` says where it came from and `advisory` states it is not
    authoritative (Codex validates the real slug at exec time). Returned by the
    codex_models tool and the codex://models resource; deliberately NOT embedded in
    codex_capabilities, whose payload is fingerprint-cacheable and must stay stable.
    """

    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    source: ModelCatalogSource
    models: list[ModelInfo] = Field(default_factory=list)
    # From the on-disk cache; None for the static fallback / none.
    fetched_at: str | None = None
    cache_client_version: str | None = None
    advisory: str
    # Set only when source == "none" (no cache and no static fallback).
    unavailable_reason: str | None = None
    fingerprint: str = FINGERPRINT


class JobStarted(BaseModel):
    """Returned by the *_async tools: a handle to poll, not a result."""

    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    job_id: str
    kind: str  # the tool the job runs, e.g. codex_delegate
    status: JobState = "running"
    started_at: str  # ISO-8601 UTC
    deadline_seconds: int  # wall-clock cap after which a poll reaps the job
    poll_after_ms: int = JOB_POLL_AFTER_MS  # initial poll delay; grows per poll (see JobStatus)
    # Results are retained `ttl_seconds` AFTER the job completes — the retention
    # window, not a countdown from now. `expires_at` is therefore null until the job
    # finishes; codex_job_status populates it once a terminal state is reached.
    ttl_seconds: int
    expires_at: str | None = None
    meta: Meta
    fingerprint: str = FINGERPRINT


class JobStatus(BaseModel):
    """Returned by codex_job_status: lifecycle state without the full result."""

    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    job_id: str
    kind: str
    status: JobState
    started_at: str
    elapsed_ms: int
    deadline_seconds: int
    # Suggested delay before the NEXT poll. For a running job this grows with
    # elapsed runtime (bounded) so successive polls back off instead of tight-
    # looping at the flat base; honor it rather than polling on a fixed interval.
    poll_after_ms: int = JOB_POLL_AFTER_MS
    # Results are retained `ttl_seconds` after the job COMPLETES. `expires_at` is null
    # while running (no completion time yet) and is set once the job is terminal.
    ttl_seconds: int
    expires_at: str | None = None
    result_available: bool = False  # true once status == done
    detail: str | None = None  # short human hint (e.g. failure reason)
    # Non-empty when a cancelled/timed-out job's throwaway worktree could not be
    # removed; each entry names the leaked path and reason.
    cleanup_warnings: list[str] = Field(default_factory=list)
    workspace: Workspace  # the resolved workspace this status was looked up in (#54)
    fingerprint: str = FINGERPRINT


class DryRunResult(BaseModel):
    """Free preview of what a run WOULD do — no Codex call, no spend."""

    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    tool: Literal["codex_dry_run"] = "codex_dry_run"
    cwd: str
    workspace_source: str | None = None
    workspace_warning: str | None = None
    tier: Tier
    sandbox: Sandbox
    isolation: Isolation
    scope: str | None = None
    base: str | None = None
    commit: str | None = None
    paths: list[str] = Field(default_factory=list)
    context_summary: ContextSummary | None = None
    prompt_bytes: int  # full UTF-8 size of the prompt that would be sent
    max_input_bytes: int
    truncated: bool = False
    truncation_hint: str | None = None
    redacted_paths_count: int = 0
    redacted_paths: list[str] = Field(default_factory=list)
    security_warnings: list[str] = Field(default_factory=list)
    fingerprint: str = FINGERPRINT


class WorktreePlan(BaseModel):
    """The baseline a `codex_delegate` run would seed from, previewed read-only with
    no worktree created. Counts are advisory — uncommitted tracked changes are
    reported but their replay into the worktree is not validated by the preview."""

    model_config = ConfigDict(extra="forbid")
    head_commit: str  # the HEAD commit the worktree detaches at
    head_subject: str | None = None  # short subject of HEAD, if readable
    tracked_files: int  # entries in the HEAD tree (blobs + submodule gitlinks)
    tracked_bytes: int  # approximate total size (blob sizes; gitlinks count as 0)
    uncommitted_tracked_files: int  # tracked files changed vs HEAD (would be replayed)
    untracked_files: int  # untracked files (delegate never copies these)
    note: str | None = None  # plain-language caveats about the previewed baseline


class DelegateDryRunResult(BaseModel):
    """Free preview of what a `codex_delegate`/`codex_delegate_async` run WOULD do —
    no Codex call, no spend, and no worktree created. `tier`/`sandbox` describe the
    previewed propose run, not this read-only preview."""

    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    tool: Literal["codex_delegate_dry_run"] = "codex_delegate_dry_run"
    cwd: str
    workspace_source: str | None = None
    workspace_warning: str | None = None
    tier: Tier = "propose"
    sandbox: Sandbox = "workspace-write"
    isolation: Isolation
    prompt_bytes: int  # full UTF-8 size of the delegate prompt that would be sent
    max_input_bytes: int  # the task byte limit the real run enforces
    worktree_plan: WorktreePlan
    fingerprint: str = FINGERPRINT


class JobSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    kind: str
    status: JobState
    started_at: str
    elapsed_ms: int
    result_available: bool = False
    expires_at: str | None = None


class JobListResult(BaseModel):
    """Returned by codex_job_list: the workspace's known jobs, newest first."""

    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    jobs: list[JobSummary] = Field(default_factory=list)
    workspace: Workspace  # the resolved workspace these jobs were listed from (#54)
    fingerprint: str = FINGERPRINT


def _object_union_schema(adapter: TypeAdapter) -> dict:
    """Wrap a model union's anyOf in a top-level object schema.

    MCP/FastMCP require an output schema whose top level is ``type: object``;
    a bare ``anyOf`` is rejected. We keep the discriminating ``ok`` key visible
    at the top and carry the full branch schemas (and their $defs) underneath.
    """
    union = adapter.json_schema()
    return {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean", "description": "true = success result, false = error result"},
        },
        "required": ["ok"],
        "anyOf": union["anyOf"],
        "$defs": union.get("$defs", {}),
    }


# Advertised output schemas (convention: a discriminated ok:true|false union). Each
# active tool advertises its own success shape so verdict/confidence appear only where
# they are meaningful (review), not as perpetually-null fields on consult/delegate (#31).
CONSULT_RESULT_SCHEMA = _object_union_schema(TypeAdapter(ConsultResult | ErrorResult))
REVIEW_RESULT_SCHEMA = _object_union_schema(TypeAdapter(ReviewResult | ErrorResult))
DELEGATE_RESULT_SCHEMA = _object_union_schema(TypeAdapter(DelegateResult | ErrorResult))
# codex_job_result / codex_job_consume_result serve every async kind, so their result
# may be any of the three success envelopes (or an error). Branch on `ok`, then `tool`.
JOB_RESULT_SCHEMA = _object_union_schema(
    TypeAdapter(DelegateResult | ConsultResult | ReviewResult | ErrorResult)
)
STATUS_SCHEMA = StatusResult.model_json_schema()
CAPABILITIES_SCHEMA = CapabilitiesResult.model_json_schema()
MODEL_CATALOG_SCHEMA = ModelCatalogResult.model_json_schema()
# codex_delegate_async returns only a job handle (or an error) — the eventual delegate
# result is fetched separately via codex_job_result (DELEGATE_RESULT_SCHEMA).
JOB_STARTED_SCHEMA = _object_union_schema(TypeAdapter(JobStarted | ErrorResult))
JOB_STATUS_SCHEMA = _object_union_schema(TypeAdapter(JobStatus | ErrorResult))
DRY_RUN_SCHEMA = _object_union_schema(TypeAdapter(DryRunResult | ErrorResult))
DELEGATE_DRY_RUN_SCHEMA = _object_union_schema(TypeAdapter(DelegateDryRunResult | ErrorResult))
JOB_LIST_SCHEMA = _object_union_schema(TypeAdapter(JobListResult | ErrorResult))

# JSON Schema enforced on Codex's final response for structured findings (passed via
# `codex exec --output-schema FILE`). It mirrors the agent-visible result fields we
# parse in normalize.py. Codex returns the final message as JSON conforming to this.
#
# OpenAI strict structured outputs require EVERY property to appear in `required`
# and EVERY object to set additionalProperties:false. "Optional" fields are modeled
# as nullable types (e.g. file/line) that are still listed in `required`.
_FINDINGS_ARRAY_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "severity": {"type": "string", "enum": ["critical", "high", "medium", "low", "nit"]},
            "title": {"type": "string"},
            "file": {"type": ["string", "null"]},
            "line": {"type": ["integer", "null"]},
            "line_end": {"type": ["integer", "null"]},
            "evidence": {"type": "string"},
            "risk": {"type": "string"},
            "recommendation": {"type": "string"},
        },
        "required": [
            "severity",
            "title",
            "file",
            "line",
            "line_end",
            "evidence",
            "risk",
            "recommendation",
        ],
    },
}
_STR_ARRAY_SCHEMA = {"type": "array", "items": {"type": "string"}}

# Review output: a verdict-bearing structured review. Used by codex_review_changes.
FINDINGS_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "verdict": {"type": "string", "enum": ["pass", "concerns", "fail", "unknown"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "findings": _FINDINGS_ARRAY_SCHEMA,
        "questions": _STR_ARRAY_SCHEMA,
        "assumptions": _STR_ARRAY_SCHEMA,
        "next_steps": _STR_ARRAY_SCHEMA,
    },
    "required": [
        "summary",
        "verdict",
        "confidence",
        "findings",
        "questions",
        "assumptions",
        "next_steps",
    ],
}

# Consult output: a read-only answer. Same shape MINUS verdict/confidence — Codex is
# never asked to invent a verdict for plain Q&A (#31). Used by codex_consult.
CONSULT_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "findings": _FINDINGS_ARRAY_SCHEMA,
        "questions": _STR_ARRAY_SCHEMA,
        "assumptions": _STR_ARRAY_SCHEMA,
        "next_steps": _STR_ARRAY_SCHEMA,
    },
    "required": ["summary", "findings", "questions", "assumptions", "next_steps"],
}
