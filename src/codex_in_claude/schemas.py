"""Pydantic models for the normalized tool result contract."""

from __future__ import annotations

import copy
import math
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from codex_in_claude._core.jobs import DEFAULT_POLL_AFTER_MS

# Bump this whenever the agent-visible surface changes: tool names, input or
# output schemas, descriptions, annotations, the ErrorCode set, the
# tier/sandbox/isolation/scope value sets, the capability guarantees, the
# initialize response (serverInfo/protocolVersion/capabilities/instructions),
# resource metadata, or the codex_capabilities payload. Clients cache by it. The
# committed manifest snapshot (tests/fixtures/manifest_snapshot.json, guarded by
# tests/test_manifest.py) fails CI on any covered change, so such a change can't
# land unreviewed; its failure message directs you to bump this and regenerate
# the fixture in the same commit. It is an acknowledgment guard — it surfaces the
# drift, it does not mechanically force the integer bump (the snapshot and this
# string are independently editable).
FINGERPRINT = "codex-in-claude/0.1/schema-23"

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
    # Tool-argument validation failed at the MCP call-tool boundary (unknown/extra
    # arg, missing required arg, wrong type, or out-of-enum Literal value). Re-emitted
    # from the Pydantic ValidationError that FastMCP raises BEFORE the handler runs, so
    # the failure carries the structured envelope instead of raw validator prose (#136).
    "invalid_arguments",
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
    # Idempotency (client-supplied idempotency_key on spend-committing tools):
    # the same key was reused with different effective arguments (a duplicate would be
    # a mismatched result, so it is refused rather than silently returning the other run).
    "idempotency_conflict",
    # a prior run for this key+arguments completed but its result is no longer available
    # (consumed via codex_job_consume_result, or count-cap evicted) within the dedup
    # window; refused rather than silently starting a new paid run.
    "idempotency_result_unavailable",
    # a concurrent reservation for this key is still being published; transient — retry.
    "idempotency_in_progress",
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
    # RFC3339 UTC (F6, schema-19); null when the captured epoch is absent or not
    # datetime-representable — conversion never raises (tolerant parsing).
    resets_at: str | None = None
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
    tier: Tier = Field(
        description=(
            "Codex intent tier of the run this envelope describes — consult (read-only, no "
            "writes), propose (writes only inside a throwaway worktree), or apply. For a call "
            "that runs Codex it is that call's own tier; for a retrieved background-job result "
            "(codex_job_result/consume) it is the ORIGINATING run's tier (a completed delegate "
            "reads 'propose'); for a codex_delegate_dry_run preview it is the previewed run's "
            "tier. Job-lifecycle calls (codex_job_*) run no Codex, so their GENERATED error "
            "envelopes report 'consult'. This is orthogonal to the MCP readOnlyHint annotation, "
            "which describes whether the call mutates this server's own job state (so "
            "codex_job_cancel/consume are readOnlyHint:false yet tier 'consult'). On a lifecycle "
            "error envelope, meta.job_kind carries the inspected job's own kind/posture."
        )
    )
    sandbox: Sandbox = Field(
        description=(
            "Sandbox the run this envelope describes uses: read-only, workspace-write (worktree, "
            "no network egress), or danger-full-access. It tracks `tier` across the same cases — "
            "the call's own run, a retrieved job's originating run, or a dry-run's previewed run; "
            "lifecycle-generated errors report 'read-only'. Like tier, this describes Codex "
            "execution posture, not whether the call mutates this server's job state (see "
            "readOnlyHint)."
        )
    )
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
    # Kind of the background job a lifecycle error envelope refers to (e.g.
    # "codex_delegate"), set only when codex_job_* resolved an existing record. It
    # carries the inspected job's own posture — a propose-tier delegate vs a read-only
    # consult/review — without overloading tier/sandbox, which describe the Codex run the
    # envelope is about (for a lifecycle error, the read-only lifecycle call itself). None
    # for not-found/pre-lookup errors and for every non-lifecycle call.
    job_kind: str | None = None
    # Set to True ONLY on a response that replayed an existing run because the caller
    # passed an idempotency_key matching an in-flight/completed run — a signal that no
    # new Codex spend occurred. Null (like the other optional meta fields) otherwise;
    # it is patched onto the outgoing envelope for a replay and is never persisted into
    # a job's result.json, so a later ordinary read of that job never looks replayed.
    idempotency_replayed: Literal[True] | None = None
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


class InvalidArgument(BaseModel):
    """One field-level argument-validation failure (#136). Machine-actionable detail
    behind the `invalid_arguments` error code, one entry per Pydantic error."""

    model_config = ConfigDict(extra="forbid")
    field: str  # the offending argument name (accessor path for nested locations)
    reason: str  # the validator's human-readable message (bounded)
    allowed_values: list[str] | None = None  # enum options when the field is a Literal
    # The rejected value is deliberately NOT echoed: a Literal/string param accepts
    # arbitrary input, so a value could be a secret, and best-effort redaction cannot
    # reliably catch a plain one. The caller already holds what it sent; field + reason +
    # allowed_values drive the repair without copying input into the result (#136).


RepairStep = Literal[
    "retry_after_delay",
    "correct_arguments",
    "use_allowed_value",
    "reduce_input",
    "use_workspace_in_roots",
    "poll_job_status",
    "list_jobs",
    "start_new_job",
    "authenticate",
    "install_codex",
    "install_git",
    "init_git_repo",
    "update_plugin",
    "inspect_and_retry",
    "retry_then_report",
    "use_new_idempotency_key",
]


class Repair(BaseModel):
    """Machine-actionable recovery guidance (agent-friendly-mcp §6). `next_step` is a
    STABLE SYMBOLIC label an agent branches on (not prose); `alternative` carries the
    human-readable fallback. `tool`/`arguments` name a tool to call to recover."""

    model_config = ConfigDict(extra="forbid")
    next_step: RepairStep
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    alternative: str | None = None


class ErrorDetail(BaseModel):
    """§6 details{field, value, reason}. `value` is deliberately omitted: a Literal/string
    param accepts arbitrary input that could be a secret, and best-effort redaction cannot
    reliably catch a plain one; the caller already holds what it sent. Documented divergence.

    `field` and `fields` are mutually exclusive — at most one is set, never both: `field`
    names a single offending input; `fields` names a set of inputs whose *combination* is
    invalid — e.g. a combined-size limit where no single input is at fault on its own
    (#174/F2). Neither is required: a detail may instead carry only `reason`/`allowed_values`
    (e.g. an enum failure with no single named field). When `fields` is set it is non-empty
    and its entries are unique — both advertised in the published schema (`minItems: 1`,
    `uniqueItems: true`), not merely runtime-enforced."""

    model_config = ConfigDict(extra="forbid")
    field: str | None = None
    fields: (
        Annotated[list[str], Field(min_length=1, json_schema_extra={"uniqueItems": True})] | None
    ) = None
    reason: str | None = None
    allowed_values: list[str] | None = None

    @model_validator(mode="after")
    def _one_of_field_or_fields(self) -> ErrorDetail:
        if self.field is not None and self.fields is not None:
            raise ValueError("ErrorDetail: set at most one of field/fields, never both")
        if self.fields is not None and len(set(self.fields)) != len(self.fields):
            raise ValueError("ErrorDetail.fields must not contain duplicates")
        return self


class ErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: ErrorCode
    message: str
    # §6: both always present in the canonical schema. `temporary` was `retryable`.
    temporary: bool = Field(...)
    retry_after_ms: int | None = Field(..., ge=0)
    repair: Repair | None = None  # omitted only when no corrective path exists
    details: ErrorDetail | None = None
    # Multi-field validation carrier (#136); details mirrors the first entry.
    invalid_arguments: list[InvalidArgument] | None = None
    # Documented top-level extensions (unchanged):
    limit_bytes: int | None = None
    actual_bytes: int | None = None
    candidate_roots: list[str] | None = None

    @model_validator(mode="after")
    def _retry_after_only_when_temporary(self) -> ErrorInfo:
        if not self.temporary and self.retry_after_ms is not None:
            raise ValueError("retry_after_ms must be None when temporary is False")
        return self


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
    exact poll/result/consume/cancel/list tools and the JobStatus fields to branch on.

    Activity signal: the server exposes a polled (disk-persisted, poll-read) event-activity
    signal via `activity_support`/`event_count_field`/`last_event_field`/`event_age_field`.
    This is SEPARATE from `progress_support` — progress_support denotes native MCP
    notifications/progress on THIS async/poll path, which has none (the sync await path
    streams throttled notifications/progress instead), and stays "none"."""

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
    # Polled event-activity (#139). SEPARATE from progress_support: this is not
    # native notifications/progress, it is a disk-persisted, poll-read activity
    # signal. progress_support stays "none" so the native-progress meaning is intact.
    activity_support: Literal["codex_events"] = "codex_events"
    event_count_field: str  # "events_seen"
    last_event_field: str  # "last_event_at"
    event_age_field: str  # "event_age_ms"


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
    # Where a TOOL failure travels, stated before the first failure so a client need not
    # infer it from the outputSchema union (#175/F3). Scoped to tool calls deliberately:
    # resource-read failures use the JSON-RPC error carrier, not a tool result.
    tool_error_carrier: str = (
        "tool result with isError: true; the error envelope is in structuredContent, "
        "and content[0].text mirrors it as JSON"
    )
    error_envelope_resource: str = "codex://error-envelope"
    result_meta_resource: str = "codex://result-meta"
    # Opt-in tool-reachable fallback: the full error-envelope / result-meta schemas,
    # returned only when codex_capabilities(include_schemas=[...]) requests them, so a
    # resource-blind client can still reach the contracts from tools/list alone (#179,
    # #173). Omitted (exclude_none) from the default payload to keep it small.
    schemas: dict[str, Any] | None = None


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
    # Advisory polled event-activity (#139). Derived from Codex's --json stream;
    # silence is NOT proof of a stall and nothing auto-cancels on these. They show
    # RECENT output, complementing elapsed_ms (total runtime).
    events_seen: int = 0  # monotonic count of Codex events observed
    last_event_at: str | None = None  # ISO-8601 of the most recent event, or None
    event_age_ms: int | None = None  # now - last_event (to completion if terminal)
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


_OPAQUE_ERROR_BRANCH = {
    "type": "object",
    "required": ["ok", "error", "meta"],
    "properties": {
        "ok": {"const": False},
        "error": {
            "type": "object",
            "description": "Populated error envelope; full schema at resource codex://error-envelope",
        },
        "meta": {"type": "object"},
    },
}
_ERROR_POINTER_DESC = _OPAQUE_ERROR_BRANCH["properties"]["error"]["description"]

# The full Meta model is inlined per success branch (~3.5KB each) and dominates the
# tools/list wire response (audit F1, #173). Success branches instead advertise a compact
# opaque meta stub — the server still EMITS the full Meta, so a strict client validating
# structuredContent against this schema still passes ({"type":"object"} accepts any
# object). The full contract is published once at the codex://result-meta resource.
_RESULT_META_POINTER_DESC = (
    "Result metadata (cwd, tier, sandbox, model, timeout, usage, rate_limit, and more); "
    "full schema at resource codex://result-meta"
)
_OPAQUE_META = {"type": "object", "description": _RESULT_META_POINTER_DESC}
_META_REF = {"$ref": "#/$defs/Meta"}
# Descriptions that survive _strip_schema_noise: the two intentional resource pointers.
_KEPT_DESCRIPTIONS = frozenset({_ERROR_POINTER_DESC, _RESULT_META_POINTER_DESC})


# Keys whose VALUES are sub-schema maps (property name → sub-schema).
# The keys of these maps are NAMES (e.g. a field called "title"), NOT JSON-Schema
# annotation keywords, so they must never be stripped.
_SUBSCHEMA_MAPS = frozenset(
    ("properties", "$defs", "definitions", "patternProperties", "dependentSchemas")
)


def _strip_schema_noise(node: object) -> object:
    """Recursively drop generated `title`/`description`/`default`, keeping only the one
    intentional error-pointer description.

    Context-aware: keys that appear inside a *subschema map* (``properties``,
    ``$defs``, etc.) are property/definition NAMES, not JSON-Schema annotation
    keywords.  A Pydantic model field named ``title`` or ``default`` must not be
    removed from the map — only the object-level annotations should be stripped.
    """
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in ("title", "default"):
                continue
            if k == "description" and v not in _KEPT_DESCRIPTIONS:
                continue
            if k in _SUBSCHEMA_MAPS and isinstance(v, dict):
                # Preserve the map keys (they are names, not annotations);
                # recurse only into each sub-schema value.
                out[k] = {name: _strip_schema_noise(sub) for name, sub in v.items()}
            else:
                out[k] = _strip_schema_noise(v)
        return out
    if isinstance(node, list):
        return [_strip_schema_noise(v) for v in node]
    return node


def _opaque_meta_refs(node: object) -> object:
    """Replace every ``{"$ref": "#/$defs/Meta"}`` with the opaque meta stub.

    Meta is only ever referenced as a success envelope's ``meta`` field, so swapping the
    ref anywhere it appears (top-level branches and inside other $defs, covering future
    multi-model unions) collapses the ~3.5KB inlined object to a compact pointer (F1)."""
    if isinstance(node, dict):
        if node == _META_REF:
            return dict(_OPAQUE_META)
        return {k: _opaque_meta_refs(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_opaque_meta_refs(v) for v in node]
    return node


def _local_def_names(node: object) -> set[str]:
    """Names of every ``#/$defs/<name>`` ref reachable in ``node`` (non-recursive over defs)."""
    names: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            names.add(ref.split("/")[-1])
        for v in node.values():
            names |= _local_def_names(v)
    elif isinstance(node, list):
        for v in node:
            names |= _local_def_names(v)
    return names


def _prune_defs(doc: dict) -> dict:  # type: ignore[type-arg]
    """Drop ``$defs`` entries no longer reachable from the document body.

    Reachability is seeded from the doc EXCLUDING ``$defs`` (otherwise every definition
    would be trivially reachable by its own presence in the map), then closed transitively
    over refs between definitions. After the Meta ref is opaqued, its closure
    (RateLimit/RateLimitWindow/Usage/ContextSummary) is orphaned here — unless a
    definition is independently referenced elsewhere (e.g. StatusResult → RateLimit,
    DryRunResult → ContextSummary), in which case per-schema reachability keeps it."""
    defs = doc.get("$defs")
    if not defs:
        return doc
    body = {k: v for k, v in doc.items() if k != "$defs"}
    reachable: set[str] = set()
    frontier = _local_def_names(body)
    while frontier:
        name = frontier.pop()
        if name in reachable or name not in defs:
            continue
        reachable.add(name)
        frontier |= _local_def_names(defs[name])
    doc["$defs"] = {k: v for k, v in defs.items() if k in reachable}
    return doc


def published_schema(*success_models: type[BaseModel]) -> dict:  # type: ignore[type-arg]
    """Build a tool's advertised outputSchema: the success branch(es) plus ONE fully
    opaque error branch. The opaque branch references no $def, so $defs is exactly the
    success closure (no ErrorInfo, no dangling refs). Generated noise is stripped."""
    if len(success_models) == 1:
        adapter: TypeAdapter = TypeAdapter(success_models[0])  # type: ignore[type-arg]
    else:
        union = success_models[0]
        for m in success_models[1:]:
            union = union | m  # type: ignore[operator]
        adapter = TypeAdapter(union)  # type: ignore[type-arg]
    raw = adapter.json_schema(ref_template="#/$defs/{model}")
    if "anyOf" in raw:
        branches = list(raw["anyOf"])
    else:
        branches = [{k: v for k, v in raw.items() if k != "$defs"}]
    doc = {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean", "description": "true = success result, false = error result"},
        },
        "required": ["ok"],
        "anyOf": [*branches, _OPAQUE_ERROR_BRANCH],
        "$defs": raw.get("$defs", {}),
    }
    # Collapse the inlined Meta object to an opaque pointer, then drop the $defs it (and
    # its now-unreferenced closure) leaves behind (audit F1, #173).
    doc = _opaque_meta_refs(doc)
    assert isinstance(doc, dict)
    doc = _prune_defs(doc)
    result = _strip_schema_noise(doc)
    assert isinstance(result, dict)
    return result


# Advertised output schemas (convention: a discriminated ok:true|false union). Each
# active tool advertises its own success shape so verdict/confidence appear only where
# they are meaningful (review), not as perpetually-null fields on consult/delegate (#31).
# The error branch is a single fully-opaque branch (no $defs pollution, no dangling
# refs); the full error contract lives at resource codex://error-envelope.
CONSULT_RESULT_SCHEMA = published_schema(ConsultResult)
REVIEW_RESULT_SCHEMA = published_schema(ReviewResult)
DELEGATE_RESULT_SCHEMA = published_schema(DelegateResult)
# codex_job_result/_consume_result return exactly the envelope the originating tool
# produced. Advertising the full three-model union re-embedded ~14.6KB of $defs on BOTH
# tools (audit F1); instead the success branch is opaque and points at the originating
# tool's advertised outputSchema, which the client has already loaded. Payloads are
# validated against the real model server-side (_validate_job_success) before return.
_OPAQUE_JOB_SUCCESS_BRANCH = {
    "type": "object",
    "required": ["ok", "tool"],
    "properties": {
        "ok": {"const": True},
        "tool": {
            "enum": ["codex_consult", "codex_review_changes", "codex_delegate"],
            "description": (
                "Originating tool; the payload matches that tool's advertised "
                "outputSchema success branch — branch on this field."
            ),
        },
    },
}
JOB_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean", "description": "true = success result, false = error result"},
    },
    "required": ["ok"],
    "anyOf": [_OPAQUE_JOB_SUCCESS_BRANCH, _OPAQUE_ERROR_BRANCH],
    "$defs": {},
}
# These three tools return their success model on the happy path, but an invalid
# argument is re-emitted as an ErrorResult at the call-tool boundary (#136), so each
# advertises a success|error union — otherwise that envelope would violate the
# declared output schema for strict MCP clients.
STATUS_SCHEMA = published_schema(StatusResult)
CAPABILITIES_SCHEMA = published_schema(CapabilitiesResult)
MODEL_CATALOG_SCHEMA = published_schema(ModelCatalogResult)
# codex_delegate_async returns only a job handle (or an error) — the eventual delegate
# result is fetched separately via codex_job_result (DELEGATE_RESULT_SCHEMA).
JOB_STARTED_SCHEMA = published_schema(JobStarted)
JOB_STATUS_SCHEMA = published_schema(JobStatus)
DRY_RUN_SCHEMA = published_schema(DryRunResult)
DELEGATE_DRY_RUN_SCHEMA = published_schema(DelegateDryRunResult)
JOB_LIST_SCHEMA = published_schema(JobListResult)


def _harden_error_envelope_schema(schema: dict) -> dict:  # type: ignore[type-arg]
    """Post-process the raw Pydantic-generated ErrorResult schema to encode invariants
    that Pydantic models enforce at runtime but that JSON Schema cannot express without
    explicit work.

    1. Ensures top-level ``required`` includes ``"ok"`` — Pydantic omits it because
       ``ok`` carries a ``default`` of ``False``.
    2. Encodes the model invariant ``temporary == False ⇒ retry_after_ms is None``
       inside the ``ErrorInfo`` $def via a JSON Schema ``if/then`` conditional.

    Operates on a deep copy to avoid mutating Pydantic's cached output in place.
    """

    s = copy.deepcopy(schema)
    s["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    # 1. Require ok at the root.
    required = s.setdefault("required", [])
    if "ok" not in required:
        required.append("ok")
    # 2. Encode the temporary=False ⇒ retry_after_ms=null invariant in ErrorInfo.
    error_def = s["$defs"]["ErrorInfo"]
    error_def["if"] = {"properties": {"temporary": {"const": False}}, "required": ["temporary"]}
    error_def["then"] = {"properties": {"retry_after_ms": {"const": None}}}
    return s


# The full error envelope, published once (resource codex://error-envelope). Root is the
# outer ErrorResult (ok/error/meta) with all $defs — the canonical, discoverable contract.
ERROR_ENVELOPE_SCHEMA = _harden_error_envelope_schema(
    TypeAdapter(ErrorResult).json_schema(ref_template="#/$defs/{model}")
)

# The full result-metadata contract, published once (resource codex://result-meta). Every
# success envelope carries the opaque `meta` pointer above instead of inlining this ~3.5KB
# object per tool; this is the canonical, discoverable full shape (audit F1, #173).
RESULT_META_SCHEMA = TypeAdapter(Meta).json_schema(ref_template="#/$defs/{model}")
RESULT_META_SCHEMA["$schema"] = "https://json-schema.org/draft/2020-12/schema"

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
