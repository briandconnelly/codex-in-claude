# Changelog

All notable changes to this project are documented here. Pre-1.0, minor versions may change the
agent-visible MCP surface; the result `fingerprint` changes when they do.

## [Unreleased]

### Added

- **Opt-in extra `codex` args passthrough via `CODEX_IN_CLAUDE_EXTRA_ARGS`** (#231). An operator-only
  env knob adds allowlisted global `codex` options to every paid `exec` call (consult/review/delegate)
  — `-c`/`--config KEY=VALUE`, `-p`/`--profile NAME`, `--enable`/`--disable FEATURE` — so a
  `model_provider`/profile can be selected even under `ignore-config` isolation (which drops
  `config.toml`, leaving `-c` the only lever). It is an allowlist, not arbitrary argv: anything else is
  refused **before any spend** with a new `extra_args_rejected` error code, and `-c` keys under
  `sandbox`/`approval_policy`/`shell_environment_policy` are refused because they would weaken the
  advertised sandbox / no-network / approval / host-env-isolation guarantees. Tokens are appended after the plugin's help-gated flags (never displacing the
  envelope-bearing `--json`/`--sandbox`/`--output-schema`/… flags) and are read from the
  worker-inherited env rather than persisted to any job spec, so a secret `-c` value never lands on
  disk and is never echoed in `codex_status` or an error envelope. When `codex` rejects a passthrough
  entry the failure is classified `extra_args_rejected` (operator config to fix) rather than
  `cli_contract_changed` — but only when the rejection names one of the injected descriptors, so a
  genuine plugin-flag drift still fails loudly. `codex_status` reports `extra_args_configured`/
  `extra_args_count`/`extra_args_valid` (never the raw values). A `--profile` layers an opaque on-disk
  TOML this server cannot inspect — a documented operator-trust boundary (see `COMPATIBILITY.md`).
  Backward-compatible addition; result `fingerprint` `codex-in-claude/0.1/schema-30` →
  `codex-in-claude/0.1/schema-31`.

- **`codex_transfer` tool: hand off the current Claude Code session to a resumable Codex thread**
  (#230). Imports a Claude Code session transcript (`.jsonl`) into a persistent Codex thread via the
  experimental `codex app-server` `externalAgentConfig/import` protocol and returns
  `resume_command` (`codex resume <thread_id>`) so the user can continue that exact conversation in
  Codex. Free — no model call or token spend (a local file conversion) — but it does create a thread
  in `$CODEX_HOME`. The thread id is read from the import-completed notification's `target` (the
  versioned, schema-emitted surface), falling back to the undocumented import ledger only for a
  byte-identical re-import; transferring a live, growing session is intentionally not idempotent (a
  new thread per call). New error codes `transfer_unsupported` (codex too old — JSON-RPC `-32601`),
  `transfer_failed` (import item failed), and `transfer_incomplete` (completed but no thread
  recorded). Ships the `/codex:transfer` slash command. Backward-compatible addition; result
  `fingerprint` `codex-in-claude/0.1/schema-29` → `codex-in-claude/0.1/schema-30`. Every
  `app-server` wire assumption lives in `cli_contract.py`; see `COMPATIBILITY.md`.

### Changed

- **Separate binding rules from facts across instructions, capabilities, and tool descriptions**
  (#243). A prose-only sweep (from the agent-friendly-mcp audit, `separating-context-from-constraints`
  lens): the `codex_status`-first rule is now stated at one consistent strength across the three
  surfaces that carried three (initialize instructions, the `codex_status` docstring, and
  `codex_transfer` — the last demoted to advisory since transfer is free); the two imperatives that
  were buried in a `capabilities.negative_scope` fact bullet ("keep it self-contained / do any network
  step yourself") are dropped there and left to `codex_delegate`'s own no-network paragraph, keeping
  the bullet pure fact; and several rules attached as sentence tails or parentheticals
  (`codex_consult` verify-the-claims, `codex_review_changes` redaction, `codex_job_list`
  read-results-promptly, `codex_transfer` transcript-ambiguity, and the initialize error-carrier
  directive) are split into standalone imperative sentences. Also drops the stale "in-place edits are a
  later milestone" roadmap line from `negative_scope`. No semantic change — every guarantee and caveat
  is preserved (the redaction and job-eviction wordings were kept deliberately broad). Wording-only;
  result `fingerprint` `codex-in-claude/0.1/schema-32` → `codex-in-claude/0.1/schema-33`. Not breaking.

- **`codex_capabilities` / `codex_status` output schemas now serve their heavy payload
  schemas on demand** (#242). The two free discovery tools were the last outputSchemas still
  inlining their full success closure in `tools/list`; they now opaque their heavy nested
  fields — `codex_capabilities.tool_details` and `codex_status.rate_limit`/`raw_defaults`/
  `resolved_defaults` — to compact `{type, description}` pointers and prune the orphaned
  `$defs` (`ToolCapability`/`AsyncLifecycle`; `RateLimit`/`RateLimitWindow`/`RawDefaults`/
  `ResolvedDefaults`), the same opaque-pointer treatment `meta` already gets (#173). Every
  top-level scalar field stays advertised, so first-pass discovery is unchanged. The full
  schemas are published at two new resources, `codex://capabilities-result` and
  `codex://status-result`, and are also reachable as a resource-blind fallback via
  `codex_capabilities(include_schemas=["capabilities-result", "status-result"])`; their
  content is snapshot-guarded in the manifest under new `FINGERPRINT_COVERS` tokens
  (`capabilities_result_schema`/`status_result_schema`). Cuts ~4.2KB from cold-start
  `tools/list`. The emitted payloads are unchanged and the advertised schemas are widened,
  not narrowed — a backward-compatible change (not breaking). Result `fingerprint`
  `codex-in-claude/0.1/schema-31` → `codex-in-claude/0.1/schema-32`.

- **Bundled skills now cover `codex_transfer`** (#234). `collaborating-with-codex` gains a
  "Choosing a tool" row, `/codex:transfer` in the slash-command list, a per-tool bullet (free but
  not read-only — creates a persistent thread in `$CODEX_HOME`; `transcript_path` discovery; not
  idempotent for a live session), and a common-mistakes entry; `deliberating-with-codex` gets a
  one-line boundary note that a session hand-off is not a deliberation pattern. Skill markdown
  only — no fingerprint change.

- **Docs: README restructured for accuracy and audience** (#227). The quick start separates
  terminal commands from Claude Code input (the old single `sh` block was not pasteable); the
  `propose`-tier / `delegate`-tool naming is reconciled at first use; the configuration table
  gains the previously undocumented `CODEX_IN_CLAUDE_RATE_LIMIT_FILE` and
  `CODEX_IN_CLAUDE_RATE_LIMIT_STALE_SECONDS` and a note that `TIER_DEFAULT`/`SANDBOX_DEFAULT`
  only affect `codex_status` reporting; background-job and rate-limit reference detail moved to
  new `docs/REFERENCE.md` sections ("Background jobs", "Rate-limit reporting"); a contents line
  was added and duplicated safety prose removed.
- **Docs: AGENTS.md rules separated from context** (#227). "The result contract" no longer
  re-lists the `FINGERPRINT_COVERS` categories (the prose copy had already drifted from the
  code) and now points at the tuple; rules previously buried in prose — the
  `check_commit_message.py`/Git-PRs sync obligation, the `_core` import ban, and the
  Copilot-review obligations — are standalone bullets; compound bullets were split and the
  coverage-floor guidance deduplicated into Testing.

### Fixed

- **The test suite no longer corrupts the invoking repository when run with an inherited `GIT_DIR`**
  (#229). Under a pre-push hook launched from a linked worktree, `GIT_DIR` (and friends) are exported;
  the fixtures' `git add`/`commit`/`config` calls then operated on the *real* repo with a temp dir as
  the working tree — staging every tracked file as deleted and rewriting the real repo's config
  (`core.bare`, `core.worktree`, test identity). The shared test helpers now scrub the git-location
  vars (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_COMMON_DIR`, `GIT_OBJECT_DIRECTORY`) so
  every git subprocess a test spawns is anchored purely by `cwd`, via both a per-call scrub (`run_git`)
  and an autouse blanket fixture. Test-infrastructure only — production `_core` git subprocesses
  already pass a fresh, non-inherited environment and were never exposed; no `fingerprint` bump.

- **An omitted `base`/`commit` on a `branch`/`commit` review no longer leaks the Python literal
  `None` into the error message** (#244). `codex_review_changes`/`codex_dry_run` with `scope="branch"`
  and no `base` (or `scope="commit"` and no `commit`) previously produced `invalid base ref: None`;
  the message now distinguishes an omitted input ("base ref is required for a branch diff but was
  omitted") from a present-but-invalid one (which still keeps its `repr` so stray whitespace/quoting
  shows). Runtime error-message prose only — not manifest-covered, so no `fingerprint` bump. (The
  same issue's proposed `ErrorDetail.value` schema change was evaluated and declined: the enum fields
  it targeted — `scope`/`detail`/`isolation` — surface over MCP as `invalid_arguments`, not
  `ErrorDetail`; `timeout_seconds` is clamped rather than rejected; and echoing a value the caller
  just sent is redundant with `field`/`reason`/`allowed_values`. See #244 for the full rationale.)

- **Exception-derived `internal_error` messages no longer leave a dangling separator** (#203).
  Empty or fully redacted exception text now renders as just the exception class name in the
  generic tool boundary, background-job spawn failure, and worker crash sinks instead of ending
  with `": "`. Existing per-sink truncation and redaction behavior is unchanged. Runtime
  error-message prose only — not manifest-covered, so no `fingerprint` bump.

- **A transient read `OSError` on an idempotency record no longer classifies as a
  permanent result-unavailable** (#202). `IdempotencyIndex._read` mapped any `OSError`
  (e.g. EIO on flaky/network storage, a permissions race) to `"corrupt"`, and
  `_classify` mapped `"corrupt"` to `UNAVAILABLE` — `temporary: false`, repair
  `use_new_idempotency_key`. So a momentary I/O blip while reading the record of a
  healthy, replayable completed run told the agent, permanently and non-retryably, to
  start a new paid run under a fresh key — duplicate spend when a retry one second
  later would have replayed the stored result. `_read` now distinguishes a transient
  I/O failure (`"io_error"`) from genuinely malformed content (`"corrupt"`): corrupt
  records still fail closed (`UNAVAILABLE`), while an I/O error surfaces as a new
  `IO_ERROR` outcome kind that the server maps to a retryable `internal_error` envelope
  (`temporary: true`, repair: "retry the same call with the same idempotency_key").
  `sweep()` gives an `io_error` entry a generous multiple of the horizon to clear
  before reclaiming it (the record may be intact), but bounds the wait so a
  persistently unreadable entry cannot wedge its key behind an infinitely-retryable
  "temporary" error. No new error code is advertised (the existing `internal_error`
  code is reused), so no `fingerprint` bump.

## [0.8.0] - 2026-07-05

An agent-friendliness and spend-safety release. It completes the 2026-07 agent-friendliness audit
(findings F1–F10 and N1–N4) and hardens the idempotency and background-job paths. The agent-visible
surface changed across ten increments (result `fingerprint` `codex-in-claude/0.1/schema-19` →
`codex-in-claude/0.1/schema-29`), so pre-1.0 this is a minor release; clients that cache by
`fingerprint` re-fetch the contract. Several existing contracts changed shape or meaning — notably the
`tools/list` `meta` branch, lifecycle-error `meta.tier`/`sandbox`, and the `input_too_large`,
`isolation`, and `idempotency_key` surfaces.

### Added

- **Optional `idempotency_key` on the six spend-committing tools** (`codex_consult`,
  `codex_review_changes`, `codex_delegate`, and their `_async` variants) — a retry after a transport
  drop **replays** the existing run instead of paying for a duplicate Codex call (#176, audit F4).
  Dedup is keyed on (resolved workspace, exact tool, argument hash); a sync retry returns the in-flight
  result, an `_async` retry the existing job handle. New error codes `idempotency_conflict` (different
  arguments), `idempotency_result_unavailable` (prior result consumed/evicted), and
  `idempotency_in_progress` (retryable); replays carry `meta.idempotency_replayed: true`, and a keyed
  run is durable (only `codex_job_cancel` stops it). Omit the param for the prior no-dedup behavior.
  Covered by `schema-21`.

- **`codex_capabilities` discloses what the fingerprint covers** (#178, audit F6). A new
  `fingerprint_covers` field lists the machine-readable categories a `fingerprint` change may signal
  (tool names/schemas/descriptions/annotations, error codes, value enums, resource metadata, prompts,
  the initialize response, and more), so a client can reason about *what* invalidated its cache without
  reading source. Derived from a new authoritative `FINGERPRINT_COVERS` tuple. Covered by `schema-26`.

- **`codex_capabilities(include_schemas=[...])`** — an opt-in fallback that embeds the full
  `error-envelope` and/or `result-meta` schemas for resource-blind clients, omitted from the default
  payload so it does not re-bloat discovery (#179, audit F7). Covered by `schema-20`.

- **Advertised output schemas declare their JSON Schema dialect** (#185, audit N4). Every tool's
  `outputSchema` gains a root `$schema` of `https://json-schema.org/draft/2020-12/schema`, matching
  input schemas, so both directions of a tool's contract are self-describing. Part of the `schema-27`
  bump.

- **MCP resources advertise an explicit `name` and `title`** (#182, audit N1). The three `codex://`
  resources now expose intent-revealing identifiers (`codex-models` / "Codex model catalog", etc.)
  instead of function-derived names. Part of the `schema-27` bump.

- **The tool-failure carrier is named before the first failure** (#175, audit F3).
  `codex_capabilities.tool_error_carrier` and one sentence in the capability summary state that a tool
  failure returns as the tool result itself (`isError: true`, envelope in `structuredContent`), so a
  discovery-only client need not infer it from the `outputSchema` union. Part of the `schema-23` bump.

- **CI wire-size budget** — the `tools/list` catalog size is pinned in CI (cap 64 KB with headroom) so
  serialized weight is guarded, not just content; the manifest snapshot also captures the
  `codex://result-meta` content.

### Changed

- **`tools/list` wire response shrunk ~44% (~103.5 KB → ~57.6 KB)** by advertising an opaque `meta`
  branch (#173, audit F1). Success schemas now carry a `{"type": "object"}` pointer instead of the full
  `Meta` model inlined per tool; the server still emits the full `Meta`, so strict clients validating
  `structuredContent` against the advertised schema still pass. The full contract is published once at
  the new **`codex://result-meta`** resource, with a `result_meta_resource` pointer in
  `codex_capabilities`. Covered by `schema-20`.

- **The capability summary (MCP `instructions`) is restructured as rules-then-context** (#180, audit
  F8) — a does/does-not lead, each routing and safety rule as its own imperative sentence, then
  discovery rules, then a single background paragraph (async-job mechanics, cached rate-limit
  semantics). No rule or fact dropped; ordering only. Part of the `schema-27` bump.

- **Restructured the `collaborating-with-codex` skill for rules-vs-context auditability** (#221) —
  binding rules surfaced as guardrail bullets, the Knobs section rebuilt as per-param bullets with the
  idempotent-retry rule extracted as its own directive, and the description trimmed; added behavioral
  test scenarios under `skills/collaborating-with-codex/tests/scenarios.md`. Skill markdown only.

- **Internal: shared prep and idempotency-outcome mapping extracted for the sync/async tool pairs**
  (#204). Each tool and its `_async` twin duplicated ~50 lines of setup (isolation/detail/workspace
  resolution, meta, pre-flight, spec); that now lives in one `_prepare_*` helper per pair, with the
  shared idempotency outcome→envelope mapping consolidated in `_idem_terminal_error`. Behavior is
  byte-identical — `fingerprint` unchanged.

### Fixed

- **Keyed idempotent job starts no longer block the asyncio event loop** (#199). The `idempotency_key`
  start paths ran a blocking cross-process `flock` + index sweep + subprocess spawn on the event-loop
  thread, so a stalled sibling process holding the lock could freeze every concurrent request this
  process served. The blocking store calls now run off-loop via `asyncio.to_thread`, and lock
  acquisition is bounded (a shared 0.5s deadline degrading to the retryable `idempotency_in_progress`
  envelope); the off-loop unkeyed spawn is shielded so cancellation still stops its spend.
  Behavior/robustness only — no `fingerprint` bump.

- **Idempotent job starts no longer strand a reservation on a partial failure** (#200). Two paths in
  the `reserve → spawn → publish` cycle could leave the idempotency index denying a key with no job
  running until the ~24.5h sweep: a failed initial placeholder write is now rolled back, and a publish
  failure after a successful spawn returns the real running job's handle instead of a false "retry"
  (which double-spent). Behavior/robustness only — no `fingerprint` bump.

- **Keyed sync-timeout recovery no longer steers agents into a second paid run** (#201). A keyed
  synchronous call that hit its wait deadline correctly left the run alive, but emitted the static
  `timeout` repair pointing at the `_async` tool — following it started a second paid run under a
  different dedup identity. It now emits a `poll_job_status` repair pointing at the live run, and the
  `idempotency_key` description no longer over-promises cross-variant reuse (sync and `_async` are
  different tools and never share a key's run). Covered by `schema-29`.

- **The `timeout` error's repair hint points at the async escape hatch** (#195). The prior hint only
  steered a retry of the same synchronous call, which — having just hit the sync clamp — would likely
  time out again. It now leads with re-running via the matching `*_async` tool (which runs to the longer
  background-job deadline, default 1800s), then polling `codex_job_status`/`codex_job_result`. Repair
  prose only — no `fingerprint` bump.

- **Agent-facing instructions separate binding rules from context more cleanly** (#198). Four
  wording-only fixes: the `codex_status` rate-limit note states its strength (*prefer* to defer
  non-urgent calls when quota is `limited`/`exhausted`), the consult-vs-review routing line gains a real
  tiebreaker (a diff pasted inline vs changes already in git), the discovery sentence is split into two
  checkable rules, and `codex_dry_run` frames redaction as a best-effort check rather than proof.
  Description and initialize-response text only; the callable contract is unchanged. Part of the
  `schema-28` bump.

- **`isolation` param no longer advertises `'inherit'` as the unconditional default** (#183, audit N2).
  The default is env-configurable via `CODEX_IN_CLAUDE_ISOLATION`, and `isolation` is behavior-bearing,
  so the hardcoded `"'inherit' (default)"` misdescribed omission semantics. The description now lists
  the allowed values and points to `codex_status` for the resolved value, matching the `timeout_seconds`
  pattern. Covered by `schema-25`.

- **Resource-read failures carry the error envelope in JSON-RPC `error.data`** (#181, audit F9). A
  `resources/read` of an unknown or disabled URI returned a bare error with `error.data: null`,
  bypassing the unified error contract every tool honors. Unknown/disabled URIs now map to a new
  `resource_not_found` code (with a `list_resources` repair) and a bare `ErrorInfo` in `error.data`;
  `codex_capabilities.resource_error_carrier` states this up front. Messages stay generic — no URI or
  exception text echoed. Covered by `schema-24`.

- **Combined-size (`input_too_large`) failures on `codex_consult`/`codex_consult_async` name every
  offending input** (#174, audit F2). The limit applies to `question` + `extra_context` together, but
  the envelope hardcoded `details.field: "extra_context"` even when `question` alone was oversized.
  `ErrorDetail` gains a `fields: list[str]` carrier (mutually exclusive with `field`), so the envelope
  now reports `fields: ["question", "extra_context"]` when both contribute. Part of the `schema-23`
  bump.

- **`invalid_arguments` repair names the failing tool** (#184, audit N3). `error.repair.tool` was left
  null though the called tool's name is known and non-sensitive; it is now set, and each
  `repair.alternative` leads with "Correct the argument(s) first — " so the (tool set, arguments absent)
  combination can't read as "call the same tool again as-is". Rejected argument values are still never
  echoed. Part of the `schema-23` bump.

- **Job-lifecycle error envelopes no longer contradict their `readOnlyHint`** (#177, audit F5). The
  `codex_job_*` tools reported `meta.tier: "propose"` / `sandbox: "workspace-write"` — a delegate's
  posture — on their own error envelopes; these now report the read-only lifecycle posture
  (`consult`/`read-only`), documented as orthogonal to `readOnlyHint`, with the inspected job's posture
  preserved in a new **`meta.job_kind`** field. Retrieved job *results* keep their originating run's
  meta (a completed delegate still reads `propose`). Covered by `schema-22`.

- **Integration docs corrected for the `details.fields` carrier and the idempotency surface** (#206,
  #207). `COMPATIBILITY.md`, `docs/REFERENCE.md`, and `README.md` still described the old `details`
  shape; they now document `field` XOR `fields`, `docs/REFERENCE.md` gains an Idempotency section, and
  the bundled skill's Knobs list is corrected (async-variant scope, `idempotency_key`). Documentation
  only.

- **Corrected factual overclaims in the `collaborating-with-codex` skill** (#220) — `workspace_root`
  scope, timeout default, `codex_status` repair field, `error.details` presence, slash-command parity.
  Documentation only.

### Security

- **Redact exception-derived text in the four client-visible `internal_error` sinks** (#186, audit
  F10). `_internal_error_result`, `_spawn_failure_envelope`, `_job_result_corrupt`, and the background
  worker's crash sink interpolated raw `str(exc)` / `ValidationError` text (which can carry paths, URLs,
  or stored-payload fragments) with no redaction pass. All four now route through `redact_text` while
  preserving the exception class name for debugging; a schema-valid stored error payload is also
  redacted at the `_finished_job_envelope` boundary. Best-effort defense-in-depth (the error channel is
  the local client, not OpenAI); message text only — no `fingerprint` bump.

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
