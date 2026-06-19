# Changelog

All notable changes to this project are documented here. Pre-1.0, minor versions may change the
agent-visible MCP surface; the result `fingerprint` changes when they do.

## [Unreleased]

### Added
- **Breaking (agent-visible surface):** new free `codex_delegate_dry_run(task, …)` tool — a
  zero-spend preview of a `codex_delegate`/`codex_delegate_async` run, mirroring how `codex_dry_run`
  previews `codex_review_changes`. It reports the baseline the throwaway worktree would seed from
  (HEAD commit + subject, tracked-file count and approximate size, uncommitted-tracked and untracked
  counts) plus the prompt bytes that would be sent and the resolved workspace/isolation — with **no**
  model call and **no** worktree created. It runs the same zero-spend validation the real delegate
  does (workspace, isolation, task size, git-repo-with-HEAD), so a failure here is one the paid call
  would also hit. The baseline preview is read-only and therefore advisory: uncommitted tracked
  changes are counted but their replay into the worktree is not validated (the `worktree_plan.note`
  field says so). Backed by a new read-only `worktree.plan()` helper in `_core`. `FINGERPRINT` →
  `schema-7`. (#29)
- CodeQL code scanning and dependency-review CI workflows, added now that the repository is public
  (both are free for public repos). CodeQL runs on push/PR to `main` plus a weekly schedule;
  dependency-review fails a PR that introduces a dependency with a high-or-worse advisory.
- **Breaking (agent-visible surface):** new `codex_rate_limited` error code. A `codex exec` run that
  fails because the account hit a usage/rate limit (ChatGPT window or API-key 429) now classifies as
  `codex_rate_limited` — `retryable=True` with a populated `retry_after_ms` (parsed from a
  `Retry-After`/"retry after Ns" value when codex provides one, else a 60s default) — instead of an
  opaque `nonzero_exit`. This lets a calling agent back off deterministically rather than
  retry-storming a transient limit. Signatures live in `cli_contract.py` (`RATE_LIMIT_PATTERNS`,
  `is_rate_limited`, `parse_retry_after_ms`); drift is still checked first so a genuine contract
  change is never masked. `FINGERPRINT` → `schema-6`. (#23)
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
- Documented what to do when the MCP server is unavailable mid-session. The
  `collaborating-with-codex` skill and the README now explain how to recognize a transport
  drop (`Connection closed` / `No such tool available`), how to recover (relaunch the server,
  confirm with `codex_status`), and an interim read-only
  `codex exec --sandbox read-only --skip-git-repo-check -` fallback — with the explicit caveat that
  it bypasses the plugin's diff gathering, secret
  redaction, input-byte bounding, and structured envelope, so it is a stopgap, not a replacement.
  Docs-only; `FINGERPRINT` unchanged. (#43)
- Documented that `codex_review_changes` and `codex_consult` are **static** reviews, not a verify
  mode: they run under the read-only sandbox, which blocks the writes a test/build/lint run
  typically needs (a writable cache/temp), so Codex can't rely on running the project's checks to
  confirm its findings. The tool docstrings and the
  `collaborating-with-codex` skill now state this and tell callers to verify findings by running the
  project's checks themselves. A writable "verify" mode would change the trust boundary of a
  read-only tool (Codex's `workspace-write` sandbox can modify the live tree) and belongs in a
  separate worktree-isolated feature, so #42 is resolved by documenting the current behavior.
  Prose-only; `FINGERPRINT` unchanged. (#42)
- **Breaking (agent-visible surface):** `verdict` and `confidence` are now review-only. They were
  on the shared success envelope for every active tool, so `codex_consult` (plain Q&A) always came
  back `verdict:"unknown"` and `codex_delegate` (which returns a diff) `verdict:"unknown"` too — a
  meaningless value an agent could wrongly branch on. The success envelope is now three precise
  per-tool shapes: `codex_consult` → answer + optional `findings`/`questions`/`assumptions`/
  `next_steps` (no verdict/confidence/diff); `codex_review_changes` → the only verdict-bearing shape
  (keeps `verdict`/`confidence`); and the `codex_delegate` result (returned directly, or from
  `codex_delegate_async` via `codex_job_result`/`codex_job_consume_result`) → `diff` + summary, no
  verdict/confidence. Each active tool now advertises its own `output_schema`, and `codex_consult`
  is no longer prompted to emit a verdict (a dedicated consult output schema drops the
  `verdict`/`confidence` fields). `FINGERPRINT` → `schema-8`. (#31)
- **Breaking (agent-visible surface):** the `codex_dry_run` result no longer carries the
  `worktree_plan` field. It was always `null` on the review path (it previewed only
  `codex_review_changes`); the new `codex_delegate_dry_run` now owns the populated, structured
  worktree plan, so the perpetually-null field is removed rather than left misleading. (#29)
- Async-job polling is more economical and legible. `codex_job_status` now returns a **growing**
  `poll_after_ms` for a running job — it scales with elapsed runtime (bounded at 10s) instead of the
  flat 1s, so an agent that honors the hint backs off naturally rather than polling ~20 times during
  a typical ~20s delegate; the `job_running` error from `codex_job_result` carries the same backed-off
  `retry_after_ms`. The `ttl_seconds`/`expires_at` semantics are now documented on the job schemas
  and `codex_job_status`: results are retained `ttl_seconds` **after completion**, so `expires_at` is
  null while a job runs and is set once it finishes (no more misreading a null expiry as "never
  expires"). Behavior/docs only: the `poll_after_ms` field already existed and only its runtime value
  changed — no tool/param/error-code/enum/schema-shape change — so `FINGERPRINT` is unchanged. (#30)
- The `collaborating-with-codex` skill now documents the propose-tier `workspace-write` no-network
  constraint (on both `codex_delegate` and the background `codex_delegate_async`), the optional
  `paths` filter on `codex_review_changes`, the `/codex:*` slash commands, and a "Common mistakes"
  section; reframes `codex_status` as run-first; tightens the `description` to triggering conditions
  only (dropping the workflow summary); and trims the env-knob list to a README/`codex_status`/
  `codex_capabilities` cross-reference. Reviewed by Codex (verdict: pass). Docs-only; `FINGERPRINT`
  unchanged.
- `codex_delegate`/`codex_delegate_async` docstrings and the `codex_capabilities` `negative_scope`
  now state that propose-tier runs execute under the `workspace-write` sandbox, which **blocks
  network egress** — a delegated task is self-contained and cannot `git push`/`fetch`, `gh`, `curl`,
  publish, or install dependencies (those fail with a DNS/host-resolution error). `COMPATIBILITY.md`
  gains a Sandbox modes section recording the `workspace-write` ⇒ no-egress property. Prose-only (no
  tool/param/error-code/enum/schema change), so `FINGERPRINT` is unchanged. (#24)
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
- Hardened the server against a single failed/long call taking down the whole tool surface for the
  rest of a session (the disconnect observed in #39). Three parts: (1) every model-/job-bearing tool
  is now wrapped in a boundary that converts an *unexpected* exception into the documented
  `internal_error` result envelope (logged with a traceback) instead of letting it escape as an
  opaque transport error — `asyncio.CancelledError` is a `BaseException` and still propagates, so MCP
  cancel semantics are preserved; (2) the server now emits diagnostic logging to **stderr** (and,
  with `CODEX_IN_CLAUDE_LOG_FILE`, a file) — never stdout, the stdio JSON-RPC channel — so a future
  disconnect leaves a trail (subprocess spawn/exit, timeout, and cancellation are logged with pid),
  controlled by `CODEX_IN_CLAUDE_LOG_LEVEL` (default `WARNING`); (3) a regression test pins that
  cancelling an in-flight `codex exec` kills its process group rather than orphaning it. `internal_error`
  is already an advertised code for these tools and no tool/param/enum changed, so this is **not** an
  agent-visible surface change and `FINGERPRINT` is unchanged. The structural complement — moving long
  read-only calls off the synchronous request path — is tracked separately in #41. (#39)
- `meta.usage.total_tokens` is now derived as `input_tokens + output_tokens` when the codex CLI
  emits a `token_count` event without a total (the current 0.140.0 behavior), instead of being
  perpetually `null` while the other usage fields are populated. Cached input tokens are a subset of
  input and are not added. An explicit CLI-provided total is still honored verbatim, preserving the
  forward-compat hook. Populating an existing field with a value is not a surface change, so
  `FINGERPRINT` is unchanged. (#28)
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
