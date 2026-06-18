# Changelog

All notable changes to this project are documented here. Pre-1.0, minor versions may change the
agent-visible MCP surface; the result `fingerprint` changes when they do.

## [Unreleased]

### Added
- Per-tool `error_codes` on each `codex_capabilities` entry: the (advisory, not exhaustive) set of
  error codes a tool may return, so agents can plan recovery branches without triggering the error
  first.
- CI now runs the gate on Python 3.14 (final since Oct 2025), which the trove classifiers already
  advertised but the matrix did not verify. A packaging test
  (`test_python_support_matrix_matches_classifiers`, `test_requires_python_floor_is_lowest_declared`)
  now asserts the `pyproject.toml` Python classifiers, the `.github/workflows/ci.yml` matrix, and the
  `requires-python` floor stay in lockstep, so the advertised support set and the verified one can't
  silently diverge again. (#17)

### Changed
- The first-read server instructions (FastMCP `instructions`) now summarize all four task families
  — `codex_consult`, `codex_review_changes`, `codex_delegate`, and `codex_delegate_async` + the
  `codex_job_*` lifecycle — plus the key prerequisite (`codex_status` first) and negative scope
  (delegate never edits your working tree; the plugin never bypasses Codex's sandbox/approvals).
  Previously they described only `codex_consult`/`codex_status`, so agents could miss the better tool
  for review/delegate tasks. Prose-only (no tool/param/error-code/enum/schema change), so
  `FINGERPRINT` is unchanged. (#7)
- Job lifecycle tools now carry MCP annotations that match each tool's real behavior. The
  inspection tools — `codex_job_status`, `codex_job_result`, `codex_job_list` — are now
  `readOnlyHint=true`/`idempotentHint=true`, while the state-changing `codex_job_consume_result`
  (deletes the retained record) and `codex_job_cancel` (stops a running process) stay non-read-only
  and non-idempotent. Previously all five shared one mutating preset, which over-marked the read
  tools and could create needless approval/planning friction for clients. Annotations aren't part of
  the fingerprinted result contract (tool names/params/error codes/value enums/schemas) and are
  refreshed via `list_tools`, so `FINGERPRINT` is unchanged. (#9)
- **Breaking (agent-visible surface):** error envelopes now carry machine-actionable repair metadata
  alongside the prose `repair` string. `ErrorInfo` gains optional `allowed_values` (concrete valid
  values for enum-like params — populated for `unsupported_isolation` and `invalid_scope`),
  `repair_tool` + `repair_tool_params` (the tool and args to call to recover — e.g. `job_running`
  points at `codex_job_status` and `job_not_found` at `codex_job_list`, each echoing the caller's
  `job_id`/`workspace_root` so the repair targets the same workspace), and `retry_after_ms`
  (suggested backoff for retryable errors). `ToolCapability` gains `error_codes` (see Added), typed
  as `ErrorCode` so the advertised code set is visible in the schema. Existing fields are unchanged;
  the new fields are optional and default to `null`. `FINGERPRINT` bumps to
  `codex-in-claude/0.1/schema-5`. (#10)
- **Breaking (agent-visible surface):** fixed-value tool parameters now advertise their allowed
  values as schema `enum` constraints instead of plain strings, so agents see valid choices before
  the first call rather than learning them from a tool-result error. Covers `scope`
  (`working_tree|branch|commit`) on `codex_review_changes`/`codex_dry_run` and `isolation`
  (`inherit|ignore-config|ignore-rules`) on `codex_consult`/`codex_review_changes`/`codex_delegate`/
  `codex_delegate_async`/`codex_dry_run`. Runtime validation is unchanged and still returns the
  structured `unsupported_isolation`/`invalid_scope` envelopes as defense-in-depth. `FINGERPRINT`
  bumps to `codex-in-claude/0.1/schema-4`. (#5)

### Fixed
- `codex_dry_run` now validates `isolation` the same way the active tools do, returning the
  structured `unsupported_isolation` error envelope instead of silently substituting the configured
  default. A dry run is meant to preview what a later active call would do, so an invalid value that
  the real call would reject must no longer slip through as a successful preview. No surface change
  (the `unsupported_isolation` code and the `isolation` param already exist), so `FINGERPRINT` is
  unchanged. (#6)
- Cancelling or timing out a `codex_delegate_async` job no longer leaks its throwaway git worktree.
  Previously the JobStore force-killed the worker with `SIGKILL`, so the worker's `finally` cleanup
  never ran and the temp worktree (with any generated source/build output) was left behind in the
  system temp dir. The store now terminates the worker gracefully (`SIGTERM`, then `SIGKILL` after a
  grace period) so it tears down its own worktree, and — as a hard-kill fallback — removes the temp
  worktree the worker declared it owns, constrained to the worktree temp area so a malformed
  manifest can never delete elsewhere. If cleanup still fails, the leftover path is named in the new
  `cleanup_warnings` field. (#3)
- `codex_delegate`/`codex_delegate_async` no longer risk attributing the caller's pre-existing
  uncommitted changes to Codex. If the throwaway worktree's baseline commit cannot be finalized
  after the live patch applies (`git add`/`git commit` failure, or a non-clean tree afterward),
  the run now fails fast with a structured `worktree_error` **before** any Codex call (zero spend)
  and the partial worktree is cleaned up, instead of silently mixing live changes into the
  returned diff. The agent-visible surface is unchanged, so `FINGERPRINT` is not bumped. (#4)

### Changed
- **Breaking (agent-visible surface):** `codex_delegate`/`codex_delegate_async` now bound the inline
  diff they return. A diff larger than the configured cap (default 200 KB, env
  `CODEX_IN_CLAUDE_MAX_DELEGATE_DIFF_BYTES`, 1 KB floor) is truncated and the result sets
  `meta.truncated=true` with a `meta.truncation_hint`; the diffstat in `meta.context_summary` still
  reflects the full diff. This keeps a large generated change from flooding the agent's context with
  unbounded, unpredictable token cost. `FINGERPRINT` bumps to `codex-in-claude/0.1/schema-3`. (#8)
- **Breaking (agent-visible surface):** `codex_job_status`/`codex_job_cancel` results gain a
  `cleanup_warnings: string[]` field (non-empty only when a cancelled/timed-out job's worktree could
  not be removed). `FINGERPRINT` bumps to `codex-in-claude/0.1/schema-2`. (#3)

### Added
- Initial release: a Claude Code plugin that calls the OpenAI Codex CLI via a FastMCP server.
- Tools: `codex_consult` (read-only second opinion), `codex_review_changes` (structured review of
  working_tree/branch/commit), `codex_delegate` (propose tier — implements a task in an isolated
  git worktree and returns a reviewable diff that is not applied), plus free `codex_status`,
  `codex_dry_run`, and `codex_capabilities`.
- Background jobs (M4): `codex_delegate_async` runs the propose tier detached and returns a
  `job_id` immediately, with free lifecycle tools `codex_job_status`, `codex_job_result`,
  `codex_job_consume_result`, `codex_job_cancel`, and `codex_job_list`. Job state is disk-backed
  under the state dir, survives MCP server restarts, reconciles dead workers via PID liveness, and
  is bounded by a wall-clock deadline plus TTL and per-workspace count-cap eviction.
- Config knobs: `CODEX_IN_CLAUDE_JOB_TTL`, `CODEX_IN_CLAUDE_JOB_MAX_SECONDS`,
  `CODEX_IN_CLAUDE_JOB_MAX_COUNT` (alongside the existing `CODEX_IN_CLAUDE_STATE_DIR`).
- Slash commands: `/codex:status`, `/codex:consult`, `/codex:review`, `/codex:delegate`,
  `/codex:delegate-async`, `/codex:dry-run`.
- `collaborating-with-codex` guidance skill.
- Driven by `codex exec` (not the experimental app-server protocol); centralized CLI contract,
  graceful flag gating, secret redaction, and an isolated-worktree delegation workflow.
- Result fingerprint: `codex-in-claude/0.1/schema-1`.
