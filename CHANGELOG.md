# Changelog

All notable changes to this project are documented here. Pre-1.0, minor versions may change the
agent-visible MCP surface; the result `fingerprint` changes when they do.

## [Unreleased]

### Added

- **Optional `idempotency_key` on the six spend-committing tools** (`codex_consult`,
  `codex_review_changes`, `codex_delegate` and their `_async` variants) so a retry after a
  transport drop **replays** the existing run instead of starting — and paying for — a duplicate
  Codex call (#176, audit F4). Dedup is scoped to (resolved workspace, exact tool, argument hash)
  and backed by a disk-backed, `O_EXCL`-guarded index beside the job store, so it holds within and
  across server processes for the job-TTL window. A sync retry awaits/returns the in-flight run's
  result; an `_async` retry returns the existing job's real handle. Reuse with **different**
  arguments is refused (`idempotency_conflict`); a key whose prior result was already
  consumed/evicted is `idempotency_result_unavailable`; a still-publishing reservation is
  `idempotency_in_progress` (retryable). A replayed response carries `meta.idempotency_replayed:
  true`, and a run started with a key is treated as durable — a single waiter's timeout or
  cancellation no longer cancels it (only `codex_job_cancel` does). Omit the param for the prior
  no-dedup behavior. Agent-visible surface change → three new error codes, one new repair step, a
  new `meta.idempotency_replayed` field, and per-tool advertised `error_codes`; fingerprint
  `codex-in-claude/0.1/schema-20` → `codex-in-claude/0.1/schema-21`.

### Changed

- **`tools/list` wire response shrunk ~44% (real MCP catalog ~103.5 KB → ~57.6 KB)** by
  advertising an opaque `meta` branch (#173, audit F1). Every success envelope's `meta` was the
  full `Meta` model inlined per tool (~3.5 KB × 6 tools ≈ 21 KB, since FastMCP dereferences
  `$defs` on the wire); success schemas now carry a compact `{"type": "object"}` pointer — the
  server still emits the full `Meta`, so strict clients validating `structuredContent` against the
  advertised schema still pass. The full contract is published once at the new
  **`codex://result-meta`** resource, mirroring `codex://error-envelope`, with a
  `result_meta_resource` pointer in `codex_capabilities`. The now-orphaned `$defs` closure
  (`RateLimit`/`RateLimitWindow`/`Usage`/`ContextSummary`) is pruned per-schema by reachability, so
  definitions still referenced outside `Meta` (e.g. `codex_status` → `RateLimit`, `codex_dry_run`
  → `ContextSummary`) are retained. The near-verbatim "Progress & recovery" docstring paragraph
  (3×) is collapsed to one binding sentence. Agent-visible surface change → fingerprint
  `codex-in-claude/0.1/schema-19` → `codex-in-claude/0.1/schema-20`.

### Added

- **`codex_capabilities(include_schemas=[...])`** — an opt-in, tool-reachable fallback that embeds
  the full `error-envelope` and/or `result-meta` schemas in the response for resource-blind
  clients (#179, audit F7; the shared mechanism the #173 opaque-`meta` caveat requires). Omitted
  from the default payload, so it does not re-bloat discovery. Covered by `schema-20`.
- **Wire-size budget** — the `tools/list` catalog size is now pinned in CI (`test_wire_catalog_under_cap`,
  cap 64 KB with headroom) so serialized weight, not just content, is guarded; the manifest snapshot
  additionally captures the `codex://result-meta` content so Meta-contract changes move the guard.

## [0.7.0] - 2026-07-01

A background-jobs hardening release. Sync calls now run through the detached worker and stream
progress; the agent-visible surface changed (result `fingerprint`
`codex-in-claude/0.1/schema-18` → `codex-in-claude/0.1/schema-19`), so pre-1.0 this is a minor
release; clients that cache by `fingerprint` re-fetch the contract.

### Added

- **Sync active calls stream coarse `notifications/progress` while running** (#169). When the
  client supplies a `progressToken`, `codex_consult`/`codex_review_changes`/`codex_delegate` emit
  throttled (≥1s apart), message-only progress derived from the worker's Codex event count — no
  fake totals, never raw event content. Clients that request no progress see no change.

### Changed

- **Sync `codex_consult`/`codex_review_changes`/`codex_delegate` now run through the detached
  worker and are recorded as jobs** (#169). The result is written to the job store before the
  response returns: `meta.job_id` names the record, so a connection dropped mid-run no longer
  forfeits the paid result — the work continues detached and is recoverable via
  `codex_job_list` → `codex_job_result` (retained for the job TTL, evictable by the per-workspace
  cap). Explicit cancellation still stops the run and the spend. Because the run now creates an
  observable, mutable job record, `codex_consult` and `codex_review_changes` no longer advertise
  `readOnlyHint: true` (the same reading as #138 applied consistently). Agent-visible surface
  change, covered by the `schema-19` fingerprint bump.
- **The initialize response no longer advertises the `prompts` capability** (#169). The server
  registers no MCP prompts; advertising an empty, static catalog misled clients. Covered by
  `schema-19`.
- **Read-only tools omit `destructiveHint`/`idempotentHint`** (#169). The MCP spec assigns those
  hints meaning only when `readOnlyHint` is false; asserting them on read-only tools claimed
  semantics the protocol does not define there. Covered by `schema-19`.
- **`codex_job_result`/`codex_job_consume_result` advertise a slimmed, opaque success
  branch instead of the full three-model union** (#169). The prior `JOB_RESULT_SCHEMA`
  re-embedded `DelegateResult`/`ConsultResult`/`ReviewResult` (and their shared `$defs`)
  in full on both tools — about 14.6KB of advertised schema neither tool needed, since a
  finished job's payload always matches the shape the originating tool already
  advertises. The success branch is now `{"ok": true, "tool": <enum>}`: branch on `tool`
  and treat the payload as that tool's own success schema (unchanged; still validated
  server-side before return). No `$defs` are embedded. Agent-visible surface change, but
  `fingerprint` does not move again here — `codex-in-claude/0.1/schema-19` already covers
  it from the `resets_at` change below.
- **`RateLimitWindow.resets_at` is now RFC3339 UTC instead of epoch seconds** (#169). Agents had to
  know the field was epoch seconds and convert it themselves; it's now a directly readable
  timestamp string (e.g. `"2025-06-15T15:06:40+00:00"`), or `null` when the captured epoch is
  absent or not datetime-representable — conversion is tolerant and never raises. Agent-visible
  surface change: `fingerprint` bumps `codex-in-claude/0.1/schema-18` → `codex-in-claude/0.1/schema-19`.
- **`collaborating-with-codex` now triggers at advisor-style self-initiated decision points** (#167).
  The skill description's triggers were all user-phrases ("ask Codex", "get a second opinion"), so
  agents never surfaced the skill unprompted. The description now also fires — explicitly *alongside*
  a process skill (planning, debugging, verification), not instead of it — when about to commit to
  one of several viable approaches on hard-to-reverse work, when a second consecutive fix for the
  same bug has just failed, or when about to declare a risky change complete on self-checks alone.
  Modeled on the decision points of Claude Code's advisor tool, which cannot itself be pointed at an
  MCP backend. Discovery-layer behavior was baseline/after tested with subagents (the stuck-mid-debugging
  and approach-commitment cases went from 0/2 to 3/3 and 2/2; trivial work still correctly spends
  nothing). Skill markdown only — no MCP surface change, no `fingerprint` bump.

## [0.6.0] - 2026-06-28

A hardening-and-contract release. The agent-visible surface changed (result `fingerprint`
`codex-in-claude/0.1/schema-12` → `codex-in-claude/0.1/schema-18`), so pre-1.0 this is a minor
release; clients that cache by `fingerprint` re-fetch the contract. Several tool contracts tightened
(see the **Changed** breaking items: the error envelope reshape, the per-tool output-schema split,
and review's new exit-0 rejection), and a batch of safety and resource-cleanup fixes landed across
the worktree, subprocess, and background-job paths.

### Security

- **Worktree git ops no longer run repo-configured hooks, fsmonitor, or signing in the server
  process** (#156). The propose-tier worktree machinery runs porcelain git (`worktree add`,
  `git apply`, `add`, `commit`) in the long-lived MCP server process, not in Codex's sandbox, so a
  repo's git config could run code: `post-checkout` (on `worktree add`), `post-commit` (which
  `--no-verify` does **not** suppress), fsmonitor, and a configured commit-signing program. Every git
  invocation is now prefixed with `-c core.hooksPath=<empty dir>` (disables all hooks) and
  `-c core.fsmonitor=false`, and the baseline commit adds `--no-gpg-sign`. (`-c` flags are used
  rather than `GIT_CONFIG_*` env so the hardening does not silently fail open on git < 2.31.) This
  matches the side-effect-free posture `gitdiff.py` already takes. Hardening under the own-repo trust
  model; gitattributes `clean`/`smudge`/`process` filters still run at checkout/staging/diff and
  remain a documented residual (full filter isolation is a separate, larger redesign).

### Fixed

- **`meta.model` no longer misreports provenance when `--model` is dropped by help-gating** (#158).
  If the installed `codex` CLI does not advertise `--model` in `exec --help`, the flag is gracefully
  dropped and the run proceeds on Codex's default model — but `meta.model` (and the
  `raw_response.model` derived from it) still echoed the *requested* slug, overstating which model
  ran. Both are now reconciled to `null` whenever `--model` is in `meta.compat_warnings`, so reported
  provenance matches the model actually used. Runtime behavior only; `meta.model` was already
  nullable, so no agent-visible surface change and no `fingerprint` bump.
- Bound subprocess output and git-diff capture in memory to prevent OOM of the long-lived stdio
  server (#155). Subprocess stdout is captured under `CODEX_IN_CLAUDE_MAX_OUTPUT_BYTES` (default
  10 MiB); stderr is bounded to a separate ~1 MiB reserve — each with a head+tail window that
  preserves trailing usage/rate-limit events. The diff is streamed through the redactor so it never
  materializes whole. Exceeding the cap marks capture truncated and does not kill the run; the
  process tree is still torn down on timeout or cancellation.
- **Timeout now covers the full output-drain lifecycle** (#155). A subprocess that exits immediately
  but leaves a descendant holding an inherited stdout/stderr pipe could previously block
  `_wait_streaming` indefinitely (the configured timeout only fired on the direct child's exit, not
  on the subsequent thread joins). A `threading.Timer` watchdog now kills the process GROUP at the
  deadline, closing descendant-held pipes and allowing pump threads to reach EOF within
  `timeout_seconds`.
- **Subprocess/exception text is now redacted on failure paths.** A secret that `codex` or `git`
  echoes before failing could reach `error.message` (and the caller's logs/context) verbatim: the
  `nonzero_exit` detail in `classify_failure`, the `WorktreeError` messages and `plan()` detail in
  `_core/worktree.py`, and the `gitdiff_error` detail all now route through `redact_text`, matching
  the success path. Defense-in-depth, internal only — no agent-visible surface change. (#152)
- **`worktree add` cleanup no longer leaks a temp dir on git timeout/`OSError`.** The cleanup around
  `git worktree add` caught only `WorktreeError`, so a `subprocess.TimeoutExpired` (git hang) or
  `OSError` (spawn failure) escaped and orphaned the `mkdtemp` parent dir. It now catches broadly and
  does a best-effort teardown, symmetric with the following seed block. (#153)
- **Async job spawn is now transactional.** `JobStore.start()` spawned the detached worker before
  persisting `meta.json`; if persistence failed after a successful spawn (disk full, fs error), a
  paid worker kept running with no discoverable record — invisible to status/list/cancel and (for
  delegate) its worktree was never reaped. Post-spawn persistence is now guarded so a failure reaps
  the worker's process group, runs the guarded cleanup of any external paths the worker already
  declared (e.g. a worktree), and removes the job dir before re-raising. (#154)
- **A corrupt `activity.json` with an out-of-range epoch no longer crashes job status/list.**
  `_read_activity` accepted any *finite* `last_event_epoch`, but a finite value still out of range
  for `datetime.fromtimestamp()` (e.g. `1e308`) raised `OverflowError`/`OSError`/`ValueError`,
  turning `codex_job_status`/`codex_job_list` into `internal_error`. The single validation point now
  also probes representability and degrades an unusable epoch to `None` (the event count stays
  valid), matching the existing non-finite handling. Internal hardening only — no agent-visible
  surface change. (#150)
- **Invalid-argument tool calls now return the structured error envelope.** An unknown/extra
  argument, a missing required argument, a wrong type, or an out-of-enum value for a `Literal`-typed
  param (e.g. `scope`, `isolation`, `detail`) is rejected by FastMCP/Pydantic *before* the handler
  runs — previously surfacing as `isError: true` with `structured_content: null` and raw validator
  prose, bypassing the documented contract (no symbolic `code`, `repair`, `request_id`, or
  `fingerprint`). This is the statistically most common first-repair case. A new call-tool middleware
  catches that `ValidationError` and re-emits it as the normal `ok: false` envelope with a new
  `invalid_arguments` error code: an `invalid_arguments[]` list of `{field, reason, allowed_values}`
  (enum `allowed_values` are read from the tool's input schema, not parsed prose; the rejected value
  is deliberately not echoed, since a param can accept arbitrary input that may be a secret), with
  `details{field, reason, allowed_values}` mirroring the first entry and a `repair` pointing
  at the tool's inputSchema and `codex_capabilities`. Only genuine argument-validation failures are
  mapped;
  unrelated validation errors propagate untouched. `codex_status`, `codex_capabilities`, and
  `codex_models` now advertise a success|error output-schema union so the envelope they can now
  return conforms to their declared schema, and every tool advertises `invalid_arguments` in
  `codex_capabilities`. (#136)
- **Async consult/review launchers no longer advertise `readOnlyHint: true`.** `codex_consult_async`
  and `codex_review_changes_async` create an observable (`codex_job_list`), mutable
  (`codex_job_cancel`/`codex_job_consume_result`), spend-committing job record that outlives the
  response, so annotating them read-only was a safety-relevant honesty bug that could lead clients to
  auto-approve. Both now carry the async-spawn annotation (`readOnlyHint: false`,
  `idempotentHint: false`, `openWorldHint: true`, `destructiveHint: false`), matching
  `codex_delegate_async`. The synchronous `codex_consult`/`codex_review_changes` stay `readOnlyHint:
  true` (network egress and spend alone are not shared-state mutation, and they retain no handle).
  (#138)
- **`codex_job_cancel` now advertises `idempotentHint: true`.** Cancel is effectively idempotent: an
  already-terminal job is returned unchanged and cancellation re-validates concurrent completion, so a
  retry after a lost response is safe and has no additional effect. It previously inherited the
  `_JOB_MUTATE` preset's `idempotentHint: false`, which could deter agents from that safe retry. It
  keeps `readOnlyHint: false` (it mutates job state). `codex_job_consume_result` stays non-idempotent —
  a repeat consume returns not-found, a different response, since the first call deletes the record.
  (#141)

### Added

- `codex_job_status` now reports advisory polled event-activity for async jobs —
  `events_seen`, `last_event_at`, `event_age_ms` — so a long-running job can be told
  apart from a stalled one.
  Advertised via `AsyncLifecycle.activity_support` (`"codex_events"`); native
  `progress_support` is unchanged (`"none"`). (#139)
- Manifest-snapshot acknowledgment guard (`tests/test_manifest.py` +
  `tests/fixtures/manifest_snapshot.json`) that fails CI whenever the full agent-visible surface —
  tool/resource wire shapes, descriptions, annotations, the initialize response, the error envelope,
  and the `codex_capabilities` payload — changes, surfacing the drift for review and directing the
  author to bump `FINGERPRINT` (#140).
- `codex://error-envelope` resource publishing the full error schema; a pointer to it in
  `codex_capabilities`.
- CI gate capping the serialized `tools/list` catalog size.

### Changed

- **BREAKING: `codex_review_changes` now rejects an exit-0 run whose output ignored `--output-schema`**
  (#159). When `codex` exits 0 but the last message is missing/blank or not parseable as a JSON
  object, the review no longer silently downgrades to a prose `summary` with `verdict="unknown"` —
  it returns an explicit error: `invalid_json` (absent/blank or unparseable) or `schema_violation`
  (valid JSON but not an object), with the raw output kept as a bounded, secret-redacted preview in
  `error.message`. The structured verdict/findings *are* the product for a review, so a schema-less
  response is surfaced rather than masked. `codex_consult` deliberately keeps its prose-passthrough
  (a plain Q&A answer is itself a valid result). No new error codes (both already existed); bumps
  `FINGERPRINT` because review's exit-0 behavior is agent-visible.
- **Softened over-promising prompt-injection wording in agent-visible tool descriptions** (#157).
  `extra_context` and the `codex_review_changes` description claimed embedded directives "are never
  obeyed" / "the reviewer never obeys" them — an absolute guarantee about LLM behavior the
  implementation cannot make. Reworded to best-effort: Codex is *instructed* to treat embedded
  directives as data, not commands — a prompt-injection mitigation, not a guarantee — and the
  `extra_context` caveat now travels with the surface (don't include live secrets; Codex can read
  files it's pointed at and redaction does not cover that field). Wording only; bumps `FINGERPRINT`.
- **BREAKING:** Error envelope reshaped to the agent-friendly-mcp §6 contract: `retryable` →
  `temporary`; flat `repair`/`repair_tool`/`repair_tool_params`/`offending_param`/`allowed_values`
  fold into `repair{next_step,tool,arguments,alternative}` (symbolic `next_step`) and
  `details{field,reason,allowed_values}`. Absent optionals are stripped (placeholder nulls gone);
  `retry_after_ms` is always present. (#135)
- **BREAKING:** Per-tool `outputSchema`s now publish the success shape plus one compact opaque
  error branch; the full error schema moves to the `codex://error-envelope` resource. Cuts the
  preloaded `tools/list` catalog ~44% (≈180 KB → ≈101 KB). (#137)
- **BREAKING:** Removed unused `StatusResult.default_errors`.
- Background-job *error* results written by a pre-upgrade server that no longer match the
  schema-16 error envelope are returned as a corrupt `internal_error` result; compatible *success*
  results are still returned (with `fingerprint` re-stamped).
  (Migration: invalidate stale error results.)
- The result `fingerprint` changes (`codex-in-claude/0.1/schema-12` → `codex-in-claude/0.1/schema-18`)
  for the agent-visible changes above (the async `readOnlyHint` fix #138 advanced it to `schema-13`;
  the `codex_job_cancel` `idempotentHint` fix #141 advanced it to `schema-14`; the `invalid_arguments`
  envelope #136 advanced it to `schema-15`; the error-envelope reshape #135 and catalog shrink #137
  advanced it to `schema-16`; the polled event-activity feature #139 advanced it to `schema-17`; and
  the review exit-0 rejection #159 plus the softened prompt-injection wording #157 advanced it to
  `schema-18`).
  Pre-1.0, these changes make the next release a minor; clients that
  cache by `fingerprint` re-fetch the contract.

## [0.5.0] - 2026-06-26

The agent-visible surface changed (result `fingerprint` `codex-in-claude/0.1/schema-11` →
`codex-in-claude/0.1/schema-12`), so pre-1.0 this is a minor release. Clients that cache by
`fingerprint` re-fetch the contract.

### Added

- **`codex_status` now reports Codex rate-limit quota.** A new `rate_limit` block reports how much of
  the 5-hour (`primary`) and weekly (`secondary`) windows remains, with `status`
  (`available`/`limited`/`exhausted`/`unknown`), per-window `remaining_percent`,
  `resets_at`/`seconds_until_reset`, `is_stale`, and `home_unverified` (provenance) flags. The
  snapshot is captured opportunistically from paid
  `codex_consult`/`codex_review_changes`/`codex_delegate` calls (zero extra spend) and cached
  locally; the live snapshot is also attached to each active call's `meta.rate_limit` (`source`
  distinguishes `current_run` from `plugin_cache`). Staleness is interpreted against each window's own
  reset clock with an asymmetric rule — an unobserved (reset-passed or missing) window degrades to
  `unknown` rather than reporting as available — so an old snapshot can't mislead. Configurable via
  `CODEX_IN_CLAUDE_RATE_LIMIT_FILE` and `CODEX_IN_CLAUDE_RATE_LIMIT_STALE_SECONDS`.

### Changed

- The result `fingerprint` changes (`codex-in-claude/0.1/schema-11` → `codex-in-claude/0.1/schema-12`)
  because the agent-visible surface gained the `rate_limit` block on `codex_status` and `meta`.

## [0.4.1] - 2026-06-24

### Changed

- **Tracked Codex version bumped to `0.142`.** `SUPPORTED_VERSIONS` now tracks `(0, 142)`; the
  contract, compatibility, and README notes are verified against `codex-cli 0.142.0`. The mechanical
  drift check passes (all `ALWAYS_SEND_FLAGS`, `HELP_GATED_FLAGS`, and sandbox values present) and the
  advisory model catalog is unchanged. Advisory only — an untracked version warns but never blocks.
  No agent-visible surface change, so the result `fingerprint` is unchanged.

## [0.4.0] - 2026-06-22

The agent-visible surface changed (result `fingerprint` `codex-in-claude/0.1/schema-10` →
`codex-in-claude/0.1/schema-11`), so pre-1.0 this is a minor release. Clients that cache by
`fingerprint` re-fetch the contract.

### Added

- `codex_models` tool and `codex://models` resource expose an advisory catalog of
  Codex `model` slugs, read from Codex's on-disk cache (`$CODEX_HOME/models_cache.json`)
  with a bundled static fallback. Discovery only — `model` stays pass-through and
  `codex exec` validates the real slug. (`FINGERPRINT` → `schema-11`.)

- **`deliberating-with-codex` skill (#117).** A documentation-only skill that composes the existing
  Codex tools into three deliberate two-model patterns — Judge (Codex critiques your draft/diff),
  two-member panel (you and Codex attempt independently, you synthesize), and a one-pass
  review–revise loop — gated behind a value/risk check, with a false-agreement warning,
  total-Codex-call caps, a scope/safety preflight, and a schema-compatible synthesis checklist. Built
  only from the shipped tools: no MCP-surface change, so the result `fingerprint` is unchanged.
  Cross-linked with `collaborating-with-codex`, which remains the tool reference and guardrail home.

### Changed

- **Disclose OpenAI data egress and redaction limits in the agent-visible surface (#114).**
  Documentation-only wording fixes so an agent can determine, without making a call, that
  `codex_consult`/`codex_review_changes`/`codex_delegate` (and their `*_async` variants) transmit repo
  content to OpenAI, and what secret redaction does and does not cover. Each active tool's docstring
  and `codex_capabilities` `returns` now name the egress and the unredacted inputs; `negative_scope`
  gains an egress entry and a redaction-limits entry, and its delegate no-network line now states that
  `workspace-write` blocks egress only for commands Codex runs in the sandbox — the model call still
  sends task/repo context to OpenAI; the `codex_status` caveat now covers review and delegate, not
  just consult. No MCP-surface change (tool names, params, error codes, and value enums are
  unchanged), so the result `fingerprint` is unchanged.
- **Tighter tool descriptions for cleaner selection (#115).** Documentation-only wording fixes to
  three descriptions that mislead tool selection: `codex_consult`'s `use_when` now qualifies "diff"
  as an ad-hoc inline paste and points at `codex_review_changes` for git-scoped diffs, and its
  docstring presents `workspace_root` as optional context for repo-grounded questions rather than a
  requirement; `codex_job_status` no longer reads as delegate-only ("Use after any `*_async` call",
  naming all three); and each `*_async` tool's `use_when` is now a standalone sentence that names its
  sync counterpart instead of deferring to it with "Same as …". No MCP-surface change (tool names,
  params, error codes, and value enums are unchanged), so the result `fingerprint` is unchanged.

## [0.3.0] - 2026-06-21

The agent-visible surface changed (result `fingerprint` `codex-in-claude/0.1/schema-5` →
`codex-in-claude/0.1/schema-10`), so pre-1.0 this is a minor release. Clients that cache by
`fingerprint` re-fetch the contract.

### Added

- **Structured repair fields for size and workspace errors (#95).** Some error envelopes still
  required prose parsing for the first repair. `ErrorInfo` gains three optional, backward-compatible
  fields: `input_too_large` now carries `limit_bytes` and `actual_bytes` (so an agent can trim by an
  exact amount), and `workspace_outside_roots` carries `candidate_roots` — populated *only* from the
  MCP roots the client already supplied, never arbitrary local paths. The prose `repair`/`message` are
  retained. The shared workspace-error path is consolidated into one helper so the new field can't
  drift across tools. New `ErrorInfo` fields are agent-visible, so the result `fingerprint` bumps
  `schema-9` → `schema-10`.
- **Async job lifecycle is advertised structurally in `codex_capabilities` (#94).** Each `*_async`
  tool's capability entry now carries an `async_lifecycle` object declaring that the server uses its
  own custom job lifecycle rather than native MCP tasks/progress (`native_task_support: false`,
  `progress_support: "none"`, `lifecycle: "codex_job_*"`) and naming the exact poll/result/consume/
  cancel/list tools plus the `JobStatus` fields to branch on (`status`, `result_available`,
  `poll_after_ms`). A client looking specifically for native MCP tasks/progress can now infer their
  absence — and discover the polling contract — from the structured envelope instead of parsing
  description prose. Sync and job-lifecycle tools omit the field. The capabilities surface grows, so
  the result `fingerprint` bumps `schema-8` → `schema-9`.
- **Automated codex-release watch.** `.github/workflows/codex-release-watch.yml` runs weekly (and on
  demand), fetches the latest published `@openai/codex` version from npm, and — when its minor isn't
  in `cli_contract.SUPPORTED_VERSIONS` — opens an idempotent tracking issue pre-filled with the
  `docs/UPGRADING-CODEX.md` checklist. No-spend and CLI-free: it only detects the new minor; the
  drift check and semantic review still run locally where the real codex CLI is authenticated. The
  decision logic lives in `scripts/check_codex_release.py`.
- **Formal codex-upgrade procedure.** `docs/UPGRADING-CODEX.md` documents the repeatable, ordered
  checklist for incorporating a new `codex` CLI version (drift detection, semantic review,
  replace-vs-add the tracked minor, lockstep files, breaking-vs-not, verification). The terse
  "When codex changes" section in `COMPATIBILITY.md` now points at it. Paired with
  `scripts/check_codex_contract.py`, a no-spend drift check that diffs the installed CLI's
  `--version`/`exec --help` against the contract's flag classes and sandbox values (reusing the
  server's own help parser).

### Changed

- **Input schemas describe their ambiguous params (#93).** Tool input schemas were strict but thin —
  key params (`workspace_root`, `base`, `commit`, `paths`, `model`, `timeout_seconds`, `question`,
  `task`, `extra_context`, `job_id`, `scope`, `detail`, `isolation`) exposed only `type`/`default`, so
  an agent had to read docstring prose for their semantics and constraints. Each now carries a
  `description` in the advertised schema, defined once via reusable `Annotated[..., Field(...)]`
  aliases so the wording can't drift between tools. `timeout_seconds` documents its 10..600 clamp
  (out-of-range is coerced, not rejected) rather than adding `ge`/`le`, so the schema agrees with
  `config.clamp_timeout()` runtime — deliberately no numeric/pattern constraints are added (a schema
  rule disagreeing with runtime validation would be worse than none). Accepted values are unchanged,
  but the advertised input schema did change, so the result `fingerprint` bumps `schema-7` →
  `schema-8` (clients cache by it).
- **Tracked Codex version bumped to `0.141`.** `SUPPORTED_VERSIONS` now tracks `(0, 141)`; the
  contract, compatibility, and README notes are verified against `codex-cli 0.141.0`. Advisory only —
  a version mismatch warns but never blocks, and the tested set stays overridable via
  `CODEX_IN_CLAUDE_SUPPORTED_VERSIONS`.

### Fixed

- **MCP `isError` now reflects semantic tool failures (#91).** A handler-level failure was returned
  as `ok: false` structured data but the MCP tool result still reported `isError: false`, so a
  conformant client keying off the protocol flag (rather than parsing our envelope) misclassified a
  failed call as a success. A single FastMCP boundary middleware now flips `isError: true` whenever a
  tool returns an envelope with `ok is False`, while leaving the `ErrorInfo` envelope intact in
  `structured_content` (and its text fallback). Agent-visible result semantics changed, so the result
  `fingerprint` bumps `schema-5` → `schema-6`.
- **Stop advertising MCP-unreachable error codes (#92).** `codex_capabilities` advertised
  `unsupported_isolation`, `unsupported_detail`, and `invalid_scope` as per-tool error codes, but
  those `ErrorInfo` envelopes can never be returned over a real MCP call: `isolation`, `detail`, and
  `scope` are `Literal`-typed params, so FastMCP rejects an out-of-enum value with a generic
  validation error (`isError: true`, no structured content) *before* the handler's `_resolve_*` /
  gitdiff guards run. Those three codes are now stripped from the advertised per-tool `error_codes`
  (a central `_SCHEMA_GATED_CODES` filter makes it structurally impossible to re-leak one). They
  remain in the `ErrorCode` enum and the in-handler guards as direct-call defense-in-depth, so
  behavior is unchanged — only the advertised discovery surface. The advertised error-code surface
  changed, so the result `fingerprint` bumps `schema-6` → `schema-7`.

### Security

- **Enforce SHA-pinning of GitHub Actions (#101).** Every workflow `uses:` was already pinned to a
  full commit SHA, but nothing prevented a future edit from reintroducing a mutable `@v4` tag or
  `@main` branch reference — repo settings still allow all actions and don't require pinning. A new
  `scripts/check_github_actions_pinning.py` (pure stdlib) scans the committed workflow YAML and fails
  if any `uses:` is not immutably pinned (external action/reusable workflow → `owner/repo[/path]@`
  40-hex SHA; Docker action → `@sha256:` digest; local `./` actions exempt). It runs as a step in the
  reusable test gate, so it rides the already-required status checks rather than depending on a new
  branch-protection setting. No agent-visible MCP surface change, so the result `fingerprint` is
  unchanged.

## [0.2.0] - 2026-06-20

The agent-visible surface changed (result `fingerprint` `codex-in-claude/0.1/schema-3` →
`codex-in-claude/0.1/schema-5`), so pre-1.0 this is a minor release. Clients that cache by
`fingerprint` re-fetch the contract.

### Added

- **Legible failure on stdio transport death.** `main()` now wraps the transport loop: a fatal error
  out of `mcp.run()` logs an actionable stderr breadcrumb (server name, version, reason, and a `/mcp`
  reconnect hint) and exits nonzero instead of dying silently, while clean disconnects
  (EOF / broken pipe / `SIGINT` / `SIGTERM`) are logged as shutdown rather than crashes. A minimal
  `SIGINT`/`SIGTERM` breadcrumb chains to the prior disposition (and leaves an inherited-ignored
  signal ignored). A stdio server can't be transparently auto-restarted — the client owns the pipe
  and `initialize` handshake — so recovery stays a manual `/mcp` reconnect, now documented in the
  README troubleshooting section. ([#76](https://github.com/briandconnelly/codex-in-claude/issues/76))
- **Per-tool stability + `listChanged` discovery metadata.** `codex_capabilities` now advertises an
  advisory per-tool `stability` field: the newer async (`codex_*_async`) and background-job lifecycle
  (`codex_job_*`) tools are marked `experimental`, while the sync core omits the field to inherit the
  server-wide `stability` ("alpha") — so an agent can tell the stateful M4 surface from the settled
  consult/review/delegate core. It is per-tool maturity metadata, distinct from the
  consult/propose/apply intent tier. The server also declares the tools `listChanged` capability (now
  pinned by a test) so clients know the contract even though the tool list is static per version.
  Adds an output-schema field, so the result `fingerprint` bumps `schema-4` → `schema-5`.
  ([#71](https://github.com/briandconnelly/codex-in-claude/issues/71))

### Changed

- **Tool input schemas declare their JSON Schema dialect.** Every tool's advertised input
  schema now carries `$schema` (`draft 2020-12`, the dialect Pydantic/FastMCP generate), so a
  client knows which draft to validate against (agent-friendly-mcp §3). The schemas were already
  *closed* (`additionalProperties: false`) and already reject unknown/misspelled arguments with a
  validation error rather than silently dropping them — that behavior is now pinned by a regression
  test across all tools. Accepted params, enums, and error codes are unchanged, but the advertised
  input schema did change, so the result `fingerprint` bumps `schema-3` → `schema-4` (clients cache
  by it). ([#70](https://github.com/briandconnelly/codex-in-claude/issues/70))
- **Sync active tools document their no-progress behavior.** The blocking `codex_consult`,
  `codex_review_changes`, and `codex_delegate` tool descriptions now state that they return only when
  Codex finishes and do not stream incremental `notifications/progress`, and point agents to the
  `*_async` variant + `codex_job_status` when they need live status or recoverability for a long run
  (a `codex_delegate` can run ~20s+). The domain `codex_job_*` surface remains the deliberate
  long-running-operation hedge; this is a description-only clarification (no `fingerprint` change).
  ([#72](https://github.com/briandconnelly/codex-in-claude/issues/72))

### Fixed

- **`codex_review_changes` now reviews explicitly-named untracked files.** With
  `scope="working_tree"` and `paths` targeting a brand-new (never-staged) file, the review
  silently returned "No changes to review" because `git diff HEAD` only sees tracked files. Named untracked
  (non-gitignored) files are now gathered too — staged into a throwaway index and diffed against the
  empty tree — so writing a file and reviewing it no longer requires a `git add` round-trip. Default
  behavior is unchanged (no `paths` ⇒ tracked changes only). Gathering is filter-free and writes no
  objects into the repo's own store, preserving the read-only/redacted posture.
  ([#74](https://github.com/briandconnelly/codex-in-claude/issues/74))

### Security

- **Broader best-effort secret redaction.** The diff/prose redactor now also catches shape-only
  (unlabeled) secrets: JWTs (`eyJ…` three-segment tokens), vendor key prefixes (OpenAI `sk-`/`sk-proj-`,
  Stripe `sk_live_`/`sk_test_`, Google `AIza…`), and connection-string passwords (`scheme://user:pass@host`,
  password redacted while scheme/user/host are preserved). Still best-effort defense-in-depth, not a
  guarantee; the agent-visible surface is unchanged (no `fingerprint` bump).
  ([#73](https://github.com/briandconnelly/codex-in-claude/issues/73))

## [0.1.0] - 2026-06-19

Initial release: a Claude Code plugin that calls the OpenAI Codex CLI through a FastMCP server, so
an agent can hand work to Codex and get back a structured, bounded result.

### Added

- **Consult and review tools.** `codex_consult` gets a read-only second opinion; `codex_review_changes`
  produces a structured review (`verdict`/`confidence`) of the `working_tree`, a `branch`, or a single
  `commit`, optionally narrowed with `paths` and given author intent via `extra_context`. Both run under
  Codex's read-only sandbox — they are static reviews and do not execute the project's tests.
- **Delegation (propose tier).** `codex_delegate` implements a task in an isolated, throwaway git
  worktree and returns a reviewable diff that is never applied to your working tree. The inline diff is
  bounded (default 200 KB, `CODEX_IN_CLAUDE_MAX_DELEGATE_DIFF_BYTES`) and flags truncation in `meta`.
- **Background jobs.** `codex_consult_async`, `codex_review_changes_async`, and `codex_delegate_async`
  run detached and return a `job_id` immediately, so a long consult/review/delegate never blocks the
  caller. Manage them with `codex_job_status`, `codex_job_result`, `codex_job_consume_result`,
  `codex_job_cancel`, and `codex_job_list`. Job state is disk-backed under the state dir, survives
  server restarts, reconciles dead workers via PID liveness, and is bounded by a wall-clock deadline,
  TTL, and per-workspace count cap. `codex_job_status` returns a growing `poll_after_ms` backoff hint.
  Successful `codex_job_status`/`codex_job_list` (and `codex_job_cancel`) responses carry a compact
  `workspace` object (`cwd`, `workspace_source`, `workspace_warning`) so an agent can see which repo a
  lifecycle call targeted — and notice a cwd fallback — instead of silently polling the wrong
  workspace. ([#54](https://github.com/briandconnelly/codex-in-claude/issues/54))
- **Free preview and introspection tools.** `codex_status` (run first), `codex_dry_run` and
  `codex_delegate_dry_run` (zero-spend previews that report the prompt bytes and worktree baseline a
  real call would use, and run the same validations), and `codex_capabilities` (per-tool params,
  `output_schema`, and advisory `error_codes`). All spend nothing.
- **Structured result contract.** Every tool returns a single envelope (`src/codex_in_claude/schemas.py`)
  with per-tool success shapes: consult → answer + optional findings/questions/assumptions/next_steps;
  review → verdict + confidence; delegate → diff + summary. Errors carry machine-actionable repair
  metadata — `allowed_values`, `repair_tool`/`repair_tool_params`, and `retry_after_ms` — alongside a
  prose `repair` string. A rate-limited Codex run surfaces as `codex_rate_limited` with a populated
  `retry_after_ms` so callers back off deterministically. Fixed-value params (`scope`, `isolation`)
  advertise their choices as schema enums.
- **Detail levels for compact envelopes.** `codex_consult`, `codex_review_changes`, `codex_delegate`,
  and async result retrieval (`codex_job_result`, `codex_job_consume_result`) accept
  `detail="summary"` (the default) or `detail="full"`. The summary default omits the often-large,
  duplicative raw model text (`raw_response.text`) — the structured fields stay authoritative and the
  parser shape is stable (`raw_response` is still present with its `text` nulled). `detail="full"`
  returns the complete raw output for diagnostics. An invalid value is rejected as
  `unsupported_detail`. ([#56](https://github.com/briandconnelly/codex-in-claude/issues/56))
- **Safety boundaries.** Secret redaction, input-byte bounding (`CODEX_IN_CLAUDE_MAX_INPUT_BYTES`), an
  unexpanded-env-placeholder pre-flight check, and a per-tool boundary that converts an unexpected
  exception into an `internal_error` envelope instead of taking down the session. Diagnostic logging
  goes to stderr (never the stdio JSON-RPC channel), optionally to a file.
- **CLI contract.** Every assumption about the `codex` CLI lives in `src/codex_in_claude/cli_contract.py`. Guarantee-bearing
  flags are sent unconditionally and fail loudly as `cli_contract_changed` (zero spend) if rejected;
  depth-only flags are feature-detected and dropped gracefully.
- **Configuration knobs.** `CODEX_IN_CLAUDE_STATE_DIR`, `CODEX_IN_CLAUDE_JOB_TTL`,
  `CODEX_IN_CLAUDE_JOB_MAX_SECONDS`, `CODEX_IN_CLAUDE_JOB_MAX_COUNT`,
  `CODEX_IN_CLAUDE_MAX_INPUT_BYTES`, `CODEX_IN_CLAUDE_MAX_DELEGATE_DIFF_BYTES`,
  `CODEX_IN_CLAUDE_LOG_LEVEL`, and `CODEX_IN_CLAUDE_LOG_FILE`.
- **Slash commands.** `/codex:status`, `/codex:consult`, `/codex:review`, `/codex:delegate`,
  `/codex:delegate-async`, and `/codex:dry-run`.
- **`collaborating-with-codex` guidance skill** for agents working alongside this plugin.
- Result fingerprint: `codex-in-claude/0.1/schema-3`.

### Security

- **Redact secrets from delegate diffs.** `codex_delegate`/`codex_delegate_async` now run the
  proposed worktree diff through the same secret redaction as review diffs before returning it:
  secret-looking file hunks (e.g. `.env`, `*.pem`, `id_rsa`) are dropped (header kept), inline
  secret values become `[redacted: secret value]`, and the redacted paths are reported in
  `meta.redacted_paths`. The `context_summary` diffstat still reflects the full pre-redaction change.
  ([#57](https://github.com/briandconnelly/codex-in-claude/issues/57))
- **Redact secrets from Codex free-text output.** The inline-value redaction is now also applied to
  the free-text Codex returns — `summary`, `findings`/`questions`/`assumptions`/`next_steps`, and
  `raw_response.text` on `codex_consult`, `codex_review_changes`, and `codex_delegate` (sync and
  async) — so a secret echoed in prose (e.g. quoting a config file it read) becomes
  `[redacted: secret value]` rather than reaching the transcript verbatim. File-hunk dropping does
  not apply to prose; this is inline-value replacement only. Best-effort defense-in-depth, consistent
  with the diff redaction above; the schema is unchanged.
  ([#58](https://github.com/briandconnelly/codex-in-claude/issues/58))
- **Harden job recovery against PID reuse after a restart.** Background-job liveness no longer trusts
  a persisted PID via a bare `kill(0)` probe after the server restarts. Each worker now holds an
  exclusive advisory lock on `<job_dir>/worker.lock` for its lifetime, and the store uses that lock as
  the authority for liveness — a PID reused by an unrelated process cannot hold it, so
  `codex_job_status`, `codex_job_cancel`, and deadline reaping never report or signal an unrelated
  process. An unowned, unverifiable post-restart record is treated as not-running rather than signaled,
  and process-group signals are sent only to a verified group leader. Requires a local filesystem
  (POSIX `fcntl`). ([#55](https://github.com/briandconnelly/codex-in-claude/issues/55))
