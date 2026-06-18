"""Pydantic models for the normalized tool result contract."""

from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

# Bump this whenever the agent-visible surface changes: tool names, input or
# output schemas, the ErrorCode set, the tier/sandbox/isolation/scope value sets,
# or the capability guarantees. Clients cache by it.
FINGERPRINT = "codex-in-claude/0.1/schema-3"

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


ErrorCode = Literal[
    # Setup / auth
    "codex_not_found",
    "codex_auth_required",
    "unexpanded_env_placeholder",
    # Configuration
    "unsupported_tier",
    "unsupported_sandbox",
    "unsupported_isolation",
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
    context_summary: ContextSummary | None = None
    job_id: str | None = None  # set on background-job results; None for sync calls
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    fingerprint: str = FINGERPRINT


class SuccessResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    tool: str
    summary: str
    verdict: Verdict = "unknown"
    confidence: Confidence = "medium"
    findings: list[Finding] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    # Unified diff produced by a `propose` run (changes confined to a temp worktree
    # and NOT applied to the live tree). None for consult/review.
    diff: str | None = None
    raw_response: RawResponse = Field(default_factory=RawResponse)
    meta: Meta


class ErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: ErrorCode
    message: str
    repair: str
    offending_param: str | None = None
    retryable: bool = False


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
    caveat: str
    fingerprint: str = FINGERPRINT


class ToolCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    cost: Literal["free", "active"]
    use_when: str
    required_params: list[str] = Field(default_factory=list)
    key_optional_params: list[str] = Field(default_factory=list)
    returns: str


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


class JobStarted(BaseModel):
    """Returned by the *_async tools: a handle to poll, not a result."""

    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    job_id: str
    kind: str  # the tool the job runs, e.g. codex_delegate
    status: JobState = "running"
    started_at: str  # ISO-8601 UTC
    deadline_seconds: int  # wall-clock cap after which a poll reaps the job
    poll_after_ms: int = 1000
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
    poll_after_ms: int = 1000
    ttl_seconds: int
    expires_at: str | None = None
    result_available: bool = False  # true once status == done
    detail: str | None = None  # short human hint (e.g. failure reason)
    # Non-empty when a cancelled/timed-out job's throwaway worktree could not be
    # removed; each entry names the leaked path and reason.
    cleanup_warnings: list[str] = Field(default_factory=list)
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
    worktree_plan: str | None = None  # for propose: where the temp worktree lands
    security_warnings: list[str] = Field(default_factory=list)
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


# Advertised output schemas (convention: a discriminated ok:true|false union).
RESULT_SCHEMA = _object_union_schema(TypeAdapter(SuccessResult | ErrorResult))
STATUS_SCHEMA = StatusResult.model_json_schema()
CAPABILITIES_SCHEMA = CapabilitiesResult.model_json_schema()
JOB_STARTED_SCHEMA = _object_union_schema(TypeAdapter(JobStarted | SuccessResult | ErrorResult))
JOB_STATUS_SCHEMA = _object_union_schema(TypeAdapter(JobStatus | ErrorResult))
DRY_RUN_SCHEMA = _object_union_schema(TypeAdapter(DryRunResult | ErrorResult))
JOB_LIST_SCHEMA = _object_union_schema(TypeAdapter(JobListResult | ErrorResult))

# JSON Schema enforced on Codex's final response for structured findings (passed via
# `codex exec --output-schema FILE`). It mirrors the agent-visible result fields we
# parse in normalize.py. Codex returns the final message as JSON conforming to this.
#
# OpenAI strict structured outputs require EVERY property to appear in `required`
# and EVERY object to set additionalProperties:false. "Optional" fields are modeled
# as nullable types (e.g. file/line) that are still listed in `required`.
FINDINGS_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "verdict": {"type": "string", "enum": ["pass", "concerns", "fail", "unknown"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low", "nit"],
                    },
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
        },
        "questions": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "next_steps": {"type": "array", "items": {"type": "string"}},
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
