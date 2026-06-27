# Changelog

All notable changes to this project are documented here. Pre-1.0, minor versions may change the
agent-visible MCP surface; the result `fingerprint` changes when they do.

## [Unreleased]

### Fixed

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
  the top-level `offending_param`/`allowed_values` mirroring the first entry and a `repair` pointing
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

### Changed

- The result `fingerprint` changes (`codex-in-claude/0.1/schema-12` → `codex-in-claude/0.1/schema-15`)
  for the agent-visible changes above (the async `readOnlyHint` fix #138 advanced it to `schema-13`;
  the `codex_job_cancel` `idempotentHint` fix #141 advanced it to `schema-14`; the `invalid_arguments`
  envelope #136 advanced it to `schema-15`). Pre-1.0, these changes make the next release a minor;
  clients that cache by `fingerprint` re-fetch the contract.

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
