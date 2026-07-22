# Changelog

All notable changes to this project are documented here. Pre-1.0, minor versions may change the
agent-visible MCP surface; the result `fingerprint` changes when they do.

## [Unreleased]

### Changed

- **`docs/UPGRADING-CODEX.md` now diffs against a retrievable previous `codex` build, not just the
  committed help snapshots** (#360). The old procedure rested on a false premise — that an in-place
  upgrade destroys the old binary, making those snapshots the only diff source. It doesn't: `codex`
  ships on npm, so any prior version installs side-by-side. A new **step 2A** owns evidence
  acquisition (step 2 keeps the judgment checklist) and extends the diffed surface beyond help text
  to the generated app-server JSON schemas and to behavior with no CLI surface at all. The committed
  snapshots keep a role, a truer one — they **authenticate** a retrieved stand-in binary, and remain
  the offline fallback. `COMPATIBILITY.md`'s marker probe gains its recording rules (presence matrix,
  failed-control handling), and `cli_contract.py` now names the seven generated schemas the plugin
  consumes, next to the constants they cover. Docs and comments only — no `FINGERPRINT` change, no
  behavior change.

- **Every egress caveat now discloses that user-global Codex skills auto-load too**, not just the
  project's `AGENTS.md` and `.agents/skills/` (#358). Skills under `$CODEX_HOME/skills/` (default
  `~/.codex/skills/`) are discovered from **outside** the workspace and their bodies can reach
  OpenAI on any active call — verified against `codex-cli 0.145.0`, and pre-existing rather than new
  (0.144.1 behaves identically). Updated: the server instructions, the `codex_status` caveat, all
  six active tool descriptions and their capability `returns`, `codex_capabilities`'
  `negative_scope`, `README.md`, `SECURITY.md`, `COMPATIBILITY.md`, `cli_contract.py`, and the
  `collaborating-with-codex` skill. **Bumps `fingerprint`** (a reword of covered descriptions and
  instructions); **not breaking** — behavior is unchanged and no documented guarantee weakens, since
  the contract only ever promised `ignore-config` drops `$CODEX_HOME/config.toml`, never that all
  `$CODEX_HOME` content stays local.

  Two narrower corrections ride along. `SECURITY.md` and `COMPATIBILITY.md` previously said
  isolation "still helps for `$CODEX_HOME` state" — true only of the *specific* state those flags
  name, so the claim is narrowed: `--ignore-user-config` does **not** suppress `$CODEX_HOME/skills/`
  despite reading as user-level isolation. And the delegate wording now places user-global skills
  outside the "tracked … seeded into the worktree" clause, because they are neither tracked nor
  seeded — scrubbing the worktree does not exclude them. Neither site is fingerprint-covered.

  `COMPATIBILITY.md`'s section (renamed to "Implicit Codex context" — `$CODEX_HOME` is not the
  workspace) remains the single home for the detail, and its re-verification probe now plants a
  global marker skill as well. Two edge cases it previously listed as unverified are now answered
  under 0.145.0: a project `.claude/skills/` is **not** discovered, and a parent-directory
  `AGENTS.md` above the git root is **not** loaded. `project_doc_max_bytes=0` remains unverified.

- **Tracked Codex version bumped to `0.145`.** `SUPPORTED_VERSIONS` now tracks `(0, 145)`; the CLI
  contract, help snapshots (`docs/codex-help/0.145.0/`), and `KNOWN_MODEL_SLUGS` fallback are
  verified against `codex-cli 0.145.0`. Advisory only — an untracked version warns in `codex_status`
  but never blocks, and the `CODEX_IN_CLAUDE_SUPPORTED_VERSIONS` override still applies. **No
  agent-visible surface change**, so no `fingerprint` bump; not breaking.

  0.145 required no code change. `codex exec/review/exec review --help` are byte-identical to
  0.144.1's (the only `codex --help` delta is one cosmetic word), the sandbox values and all
  `ALWAYS_SEND_FLAGS` are intact, the **contract-drift** signatures still match real observed output
  (probed: unknown flag, invalid `--sandbox` value, unknown feature name), the
  `model_reasoning_effort` config key is still applied (the backend still rejects a bad value with
  both `[ReasoningEffortParam]` and `[reasoning.effort]` markers), the `models_cache.json` slug set
  and reasoning-effort field shapes are unchanged, the allowlisted `CODEX_IN_CLAUDE_EXTRA_ARGS`
  option forms still parse, and the live integration suite passes against the new CLI. The **auth
  and rate-limit** stderr signatures were *not* re-observed for 0.145 — triggering them requires a
  real failing account state — so those patterns still rest on the earlier observations that
  introduced them.

  Verification for this bump used a **true A/B against a side-by-side install of the previous
  version** (`npm install --prefix <scratch> @openai/codex@0.144.1`) rather than only the committed
  help snapshots, which settled two questions the snapshots could not:

  - The app-server protocol diff (`generate-json-schema`) is **additive only** for the surface this
    plugin consumes. `RateLimitSnapshot.spendControlReached` is the sole genuinely new field, and it
    is deliberately not consumed yet (#359) — the rate-limit parse is key-by-key, so an unread field
    is inert. Every other unconsumed field on that response already existed in 0.144.1.
  - 0.145's new default-on `skill_search` feature causes **no observable change** under this
    plugin's isolation flags: both versions returned an identical skill catalog and identical
    auto-loaded context in a marker probe.

  That probe also surfaced a **pre-existing** disclosure gap, present on 0.144.1 and 0.145.0 alike:
  a user-global skill under `$CODEX_HOME/skills/` is auto-discovered and reaches the model despite
  `--ignore-user-config`. The behavior is unchanged by this bump; the disclosure was corrected
  across every caveat site under #358, below. #360 tracks folding the A/B technique into
  `docs/UPGRADING-CODEX.md`.

### Fixed

- **`branch` and `commit` diffs now gather atomically against pinned object IDs** (#355). A diff
  gather runs the context summary and the transmitted diff as separate git invocations, and the
  refs feeding them were symbolic — `working_tree` used `HEAD`, `branch` used `<base>...HEAD`,
  `commit` used the caller-supplied (possibly symbolic) ref. A concurrent commit/reset/checkout/
  ref-update between the two invocations could make the summary and the reviewed patch describe
  different objects while `coverage.status` still reported `"complete"` — the same inconsistency
  class as #336, but for the `branch`/`commit` scopes its working-tree state token deliberately left
  out. `gather_diff` now resolves `HEAD`, a branch `base`, and a `commit` ref to immutable commit
  object IDs **once**, up front (`git rev-parse --verify <ref>^{commit}`), and builds every
  summary/diff invocation from those IDs, so a mid-gather ref move can no longer split them.
  `<base_sha>...<head_sha>` preserves the three-dot merge-base semantics; validation and error
  messages are unchanged (the reachability check that was a separate `git rev-parse` is now folded
  into resolution). Three follow-on hardenings from the Codex review of this change: (1) an unborn
  HEAD now **fails closed** with a clear error rather than falling back to the mutable symbolic
  `HEAD` the fix set out to remove; (2) because pinning froze `working_tree`'s diff base, a
  concurrent HEAD move (reset/checkout) is now also disclosed via `tree_changed_during_gather` by
  comparing the pinned HEAD to HEAD at the end of the gather — the porcelain token alone could miss
  it; (3) a `commit=<annotated-tag>` review now peels the tag to its commit, so the review shows
  that commit's diff rather than the tag object's metadata (tagger/message). Internal correctness
  fix — the discovered surface (`DiffResult` shape, coverage fields) is unchanged, so no
  `fingerprint`/`RESULT_FORMAT` bump; not breaking.
- **`working_tree` reviews now disclose a concurrent edit made while the diff was gathered** (#336).
  A `working_tree` gather runs several sequential git invocations — the context summary, the
  transmitted diff, and the untracked enumeration — so a concurrent edit between them could make the
  summary describe different content than the diff Codex actually reviewed, or add/remove files after
  enumeration, while `coverage.status` still reported `"complete"` because no omission flag was set.
  The working_tree gather now brackets that window with a cheap best-effort state token
  (`git status --porcelain -z`, streamed through the bounded runner, scoped to the same pathspec and
  global-excludes as the diff); a mismatch sets a new `tree_changed_during_gather` value on
  `coverage.omission_reasons`, degrading coverage to `partial` (and, via the #319 rules, a `pass`
  verdict to `unknown`). It is a **consistency caveat** — "the tree was modified while it was read"
  — not a claim that specific content was omitted, so `partial`'s documented meaning is widened to
  cover it. The token is a porcelain classification, not a content hash: it trips on file
  additions/removals and status changes (including a concurrent `git add`), but **not** on a
  content-only re-edit of an already-modified file or an A→B→A round trip — its absence is therefore
  not proof the tree held still, and the `complete` documentation is corrected to say so.
  `branch`/`commit` scopes gather from git objects and skip the check; the `complete`
  documentation no longer claims those scopes are atomic, since their diff is still gathered by
  separate git invocations (ref-pinning tracked separately). Backward-compatible value-set widening
  of `CoverageOmissionReason` — bumps the result `fingerprint` (`schema-51` → `schema-52`) and the
  persisted result-format (`RESULT_FORMAT` `4` → `5`, since an older closed-schema reader could
  reject the new enum value); not breaking.
- **Carriage return in a git-produced filename no longer corrupts the untracked gather** (#353).
  The bounded git-subprocess runner (`_core/gitproc.run_lines`) spawned its child with
  `text=True`, so Python's universal-newline translation rewrote a raw `\r` (or `\r\n`) in git's
  `-z` output to `\n` **before** the NUL-splitter saw it. An untracked file whose name contained
  a carriage return was then looked up under the wrong path — a raw `FileNotFoundError` escaped
  the gather as an unstructured internal error, and if both `we\rird.py` and `we\nird.py` existed
  the carriage-return file was silently omitted yet still counted as included (a quiet
  `detected == included` coverage-contract violation the #322 F3 invariant could not catch). The
  runner now reads binary pipes wrapped in `TextIOWrapper(..., newline="")`, disabling the
  translation while keeping the `surrogateescape` byte round-trip, so every `-z` consumer — the
  untracked enumeration and the `core.excludesFile` read both route through this runner — is
  byte-exact. `_index_untracked` additionally maps a `FileNotFoundError` from a concurrently
  deleted untracked file onto a structured `RuntimeError`. Pre-existing (present before #331);
  no `fingerprint` change (byte-identical for ordinary filenames).
- **`invalid_reasoning_effort` machine repair for the local config-shape guard** (#332). The
  error code is emitted from two paths: the Codex backend rejecting a sent effort (table repair
  `correct_arguments` + `codex_models`, correct there), and the local pre-spend guard refusing a
  hostile *resolved* value before any subprocess (zero spend, the backend never saw it). The guard
  previously inherited the backend repair, misdirecting an agent that branches on `error.repair`
  to a useless `codex_models` call while the real fix went untouched. The guard now emits a
  provenance-specific repair with **no** tool: `correct_config` when the invalid value is the
  resolved `CODEX_IN_CLAUDE_REASONING_EFFORT` default, `correct_arguments` when it is an explicit
  per-call argument (only reachable via a direct in-process call — over MCP such a value is
  `invalid_arguments` at the boundary). `make_error` gains the ability to clear the table's repair
  tool (explicit `repair_tool=None`), and the `codex://params` reasoning_effort contract now
  documents the env-default pre-spend failure. Bumps the result `fingerprint` (`schema-50` →
  `schema-51`) for the corrected parameter contract; not breaking (`correct_config` is already a
  published `RepairStep`, and no field, tool, or error code was removed or retyped).

### Added

- **`result_ok` on job status and list entries** (#335). `codex_job_status` and each
  `codex_job_list` entry now carry `result_ok`, a done job's producer-declared outcome
  (`true` = success, `false` = a stored error envelope, `null` = running, no stored envelope,
  an unclassifiable payload, or a record finalized before this field). A stored failure is
  otherwise indistinguishable from a success at the list/status level — both show
  `status: "done"`, `result_available: true` — forcing a per-job fetch to triage. The outcome
  is stamped into the job record once, at finalization, and never backfilled onto older records.
  It reports the outcome recorded when the result was written and does **not** guarantee this
  reader can still parse the payload — a cross-release record may report an outcome yet fail
  `codex_job_result` with `job_result_incompatible`. `codex_capabilities` gains an
  `async_lifecycle.result_ok_field` entry so the field is discoverable structurally. Backward-
  compatible output addition — bumps the result `fingerprint` (`schema-49` → `schema-50`), not
  breaking; the persisted result-format (`RESULT_FORMAT`) is unchanged.

### Changed

- **The untracked-file diff gather counts in bounded memory** (#331). `_core/gitdiff.py`'s
  `_untracked_new_file_diff` — the `untracked="include"` / explicit-`paths` gathering path, also
  reachable from the free `codex_dry_run` — previously materialized two whole git outputs in memory:
  the `git ls-files --others -z` listing (unbounded in file count) and the `--numstat` output (one
  line per file). Both now stream through the shared `_core/gitproc.run_lines` runner, which gains a
  validated `sep` parameter (`"\n"` or `"\0"`) so a NUL-delimited listing — whose records may contain
  newlines — is split correctly. The `ls-files` listing is fed record-by-record into the per-path
  index build (a single enumeration, so `detected == included` cannot break under concurrent
  mutation), and the whole composed listing-plus-index-build phase is bounded by one deadline rather
  than only the producer's watchdog, so a pathological workspace can neither exhaust server memory nor
  stall a review past the timeout. An oversized (truncated) path record fails loudly instead of being
  hashed under a fabricated name. Reported counts, the streamed diff, and failure semantics are
  unchanged, so **no `fingerprint` change**. (Two siblings remain: `_summary`'s tracked-diff
  `--numstat` capture (#350) and `count_untracked`'s post-EOF stderr read (#351) — filed as
  follow-ups.)

## [0.14.0] - 2026-07-19

A discovery-slimming and sync-timeout release. The `tools/list` catalog gets lighter and a new
`codex://params` resource becomes the single home for the full parameter contracts, the sync tools
steer long-running work to their `_async` variants at selection time, and the built-in sync
`timeout_seconds` default rises from 180 to 300. The agent-visible surface changed (result
`fingerprint` `codex-in-claude/0.1/schema-46` → `schema-49`), so pre-1.0 this is
a minor release; clients that cache by `fingerprint` re-fetch the contract. Every change is
backward-compatible — no tool, field, or error code was removed or retyped.

### Added

- **`codex://params` resource and `parameter-contracts` capabilities fold-in** (#333). A new
  read-only resource serves the full lifecycle/validation semantics for parameters whose
  `tools/list` description is a compressed summary, backed by a single-source
  `PARAMETER_CONTRACTS` registry so the inline summary and the resource body cannot drift.
  `codex_capabilities(include_schemas=["parameter-contracts"])` embeds the same document for
  resource-blind clients. Its content is guarded by the new `parameter_contracts`
  `FINGERPRINT_COVERS` category and the manifest snapshot.

### Changed

- **Slimmed the `tools/list` catalog** (#333). MCP inlines each parameter description into every
  tool's schema, so a long shared description repeats on the wire. The `idempotency_key` and
  `reasoning_effort` inline descriptions are compressed to their selection-, safety-, and
  spend-critical facts (the full lifecycle/validation detail moves to `codex://params`),
  `workspace_root`/`isolation` are tightened, and the sync/async tool docstrings are slimmed —
  reducing the serialized `tools` catalog by ~6% (~85.2 KB → ~80.2 KB, snapshot measure) with
  **no weakened guarantee**: a table-driven per-tool freeze test asserts every egress/security
  guarantee (raw-input, files-read, auto-loaded `AGENTS.md`/`.agents/skills`, isolation, best-
  effort redaction, delegate no-network, review diff-redaction) still ships inline. This bumps the
  result `fingerprint` but is not breaking; the deeper `≤60 KB` target requires opaquing the output
  schemas (tracked separately).
- **Sync tools steer long-running work to their `_async` variants, and the default sync
  `timeout_seconds` rises from 180 to 300** (#338, #341). Two changes to how the synchronous tools
  handle work that can outlast a foreground call:
  - The `codex_consult` / `codex_review_changes` / `codex_delegate` descriptions, their `_async`
    counterparts, all six `codex_capabilities` `use_when` entries, and the server `instructions`
    block now name the shapes that can exceed the synchronous deadline — a high-reasoning-effort or
    broad repo-grounded consult, a multi-file or whole-branch review, or a substantial
    implementation task — and recommend the matching `_async` tool, so the steer reaches the agent
    at tool-selection time instead of only in the post-timeout repair, after the paid run was
    already lost.
  - The built-in default sync `timeout_seconds` rises from 180 to 300: a sync call that omits
    `timeout_seconds` now waits up to 300s before terminating. The 10–600s clamp and the
    `CODEX_IN_CLAUDE_TIMEOUT_SECONDS` operator override are unchanged, and a caller wanting the
    prior deadline can pass `timeout_seconds=180`. 300 is the smallest round value that recovers the
    mid-tier consult/review runs observed exceeding the old 180s cap; the destructive >~420s cliff
    stays the domain of the `_async` variants (separate 1800s job deadline), so the raise reduces
    the *frequency* of mid-tier sync timeouts rather than removing the cliff. A longer sync deadline
    only helps a client whose own foreground window is at least the server deadline; a client with a
    short window already backgrounds long sync calls, and the `timeout_seconds`/env override remains
    the escape hatch either way.

  The `collaborating-with-codex` skill routing and the `/codex:consult|review|delegate` command
  prompts carry the same steer. Wording and default-value changes that narrow no input and weaken no
  guarantee — the deadline was already documented as overridable — so they move the result
  `fingerprint` but are not breaking. A `codex_dry_run` size advisory remains tracked separately
  (#342).
- Internal: the stripped git-subprocess environment is now built by a single
  `gitdiff._base_git_env()` helper shared across `_core` (previously duplicated at five
  call sites), so the hardening posture cannot drift between them.

### Fixed

- **Untracked-file handling now honors the user's global gitignore** (#330). The git
  subprocesses that enumerate untracked files run with a HOME-stripped environment
  (deliberate hardening — no user hooks/fsmonitor/attributes), which also prevented git
  from resolving the user's **global** excludes (`core.excludesFile` from global config,
  or the default `~/.config/git/ignore` / `$XDG_CONFIG_HOME/git/ignore`). As a result a
  globally-ignored file (e.g. a `~/.config/git/ignore`-listed `.claude/settings.local.json`)
  was misclassified as untracked: it inflated the `untracked_files_detected` /
  delegate-plan `untracked` counts, and under `untracked="include"` its **contents were
  gathered and sent to OpenAI**, contrary to the documented "non-ignored untracked files"
  contract. The effective `core.excludesFile` is now resolved from the server's own
  environment (mirroring git's own precedence, including a repo-local override) and passed
  explicitly as `-c core.excludesFile=<path>` to only the untracked-enumeration calls, so
  the global ignore layer is honored without restoring `HOME` (no other global config
  becomes readable). The resolver drops inherited `GIT_DIR`-family variables so a stray
  `GIT_DIR` cannot anchor resolution to another repo, and `GIT_CONFIG` (which only
  `git config` honors) so it mirrors what `ls-files` actually reads. Repo-local
  (`.gitignore`, `.git/info/exclude`) and local/system `core.excludesFile` layers with
  ordinary absolute or relative paths were already honored; a `~`-containing local/system
  `core.excludesFile` previously failed to expand under the HOME-stripped child (a fatal
  error) and now resolves too, since the value is `~`-expanded in the server. Behavior-only
  fix restoring the already-documented meaning — no change to the agent-visible schema or
  descriptions, so the result `fingerprint` is unchanged.

## [0.13.0] - 2026-07-15

A review-honesty and rate-limit-recovery release. `codex_review_changes` no longer reports an
unreviewed working tree — the all-untracked shape most agent work takes — as a high-confidence
`pass`, and `codex_status` reads live rate-limit quota from the `codex app-server` again after
codex 0.144 moved it off the `codex exec` stream. Both are **breaking** on the agent-visible
surface: the result `fingerprint` moves twice (`codex-in-claude/0.1/schema-44` → `schema-46`) and
`RESULT_FORMAT` twice (`2` → `4`), so pre-1.0 this is a minor release and clients that cache by
`fingerprint` re-fetch the contract.

### Changed

- **`codex_review_changes` no longer reports an unreviewed tree as a high-confidence pass**
  (#319, **breaking**). A working tree whose only changes were untracked (new) files — the most
  common shape of agent work — used to short-circuit to `verdict: "pass"`, `confidence: "high"`
  with **no model call**, indistinguishable from a genuinely clean review. Now:
  - The result carries top-level `review_status` (`completed` | `not_run`) and a `coverage` object
    (`status` `complete` | `partial`; pathspec-scoped `untracked_files_detected`/`included`/`omitted`
    counts, null outside `working_tree` scope; a closed `omission_reasons` set of `untracked_omitted`
    / `truncated` / `redacted`). Untracked files are inventoried with `git ls-files --others` — a
    **count only, no contents read**, so the blind spot is disclosed at zero egress.
  - A review that never ran the model returns `verdict: "unknown"`, `confidence: "low"`,
    `review_status: "not_run"` — **never `pass`**. A model `pass` over `partial` coverage (omitted
    untracked files, a truncated diff, or a redacted file) is surfaced as `unknown`/`low` with the
    caveat prefixed to `summary`; concrete `fail`/`concerns` findings are always retained.
  - A new `untracked` input (`explicit_only` default | `include` | `exclude`) on
    `codex_review_changes`, `codex_review_changes_async`, and `codex_dry_run`. `explicit_only`
    preserves #74 (only untracked files named in `paths` are reviewed); `include` reviews every
    non-ignored untracked file (opt-in egress — it sends their contents); `exclude` includes none.
  - `codex_dry_run` now reports `would_call_model` and the same `coverage` object, and its
    `prompt_bytes` is `0` when the previewed call would send nothing — matching the paid path
    instead of reporting the size of a prompt never sent (#320).
  - Git invocations in the diff-gathering path now run with `-c core.fsmonitor=false`, so a
    working-tree review of an untrusted repo cannot execute a repo-configured fsmonitor program in
    the server process.
  - Hardening (from an implementation review): the untracked inventory is stream-counted in
    bounded chunks (an untrusted workspace with arbitrarily many untracked files cannot exhaust
    memory); an invalid `untracked` policy reaching the core is rejected as `invalid_arguments`
    rather than silently behaving like `exclude`; the coverage counts come from a single
    enumeration so `detected == included + omitted` can't be violated under concurrent mutation,
    and `Coverage` now validates that invariant; `review_status`/`would_call_model` are required
    (no unsafe positive default); and the empty-review repair hint is tailored to the active
    `untracked` policy.

  Bumps `FINGERPRINT` (`schema-44` → `schema-45`) and `RESULT_FORMAT` (`2` → `3`); clients that
  cache by `fingerprint` re-fetch the contract, and cross-release job replay of a review result
  written by an older version is refused rather than misread.

- **`codex_delegate_dry_run`'s worktree preview counts in bounded memory** (#323, #326). All three
  counts in `worktree.plan()` — untracked files, tracked files/bytes (`git ls-tree -r --long`), and
  uncommitted tracked files (`git diff --numstat`) — previously materialized their whole git listing
  in memory. The untracked count now delegates to the shared `gitdiff.count_untracked` inventory
  (the same NUL-delimited, fsmonitor-hardened enumeration `codex_review_changes`/`codex_dry_run`
  use), and the other two stream through a new shared `_core/gitproc.run_lines` runner (bounded
  per-line reader, concurrent capped stderr drain, process-group kill/reap on timeout or consumer
  failure — lifecycle guarantees ported from the diff streamer), so a repo with a pathological
  number of tracked, changed, or untracked files is counted without exhausting memory. Reported
  counts and failure semantics are unchanged — a git failure surfaces as a structured
  `worktree_error` (or, for `numstat`, still degrades to `0`) instead of a silently-authoritative
  `0` — so **no `fingerprint` change**. (The newline over-count originally filed as #323 did not
  reproduce: git C-quotes control characters, newline included, by default, so `plan()`'s non-`-z`
  line-count was already correct.)

### Fixed

- **`codex_status` reports live rate-limit quota again on codex 0.144+** (#321, **breaking**).
  codex 0.144 removed the `token_count` event that carried the quota block on the `codex exec`
  stream, so `rate_limit` had gone permanently `unknown` while the note told you to "run any Codex
  call to populate it" — advice that could never work. The data had moved to the app-server
  protocol, not disappeared. Now:
  - `codex_status` fetches quota **live** from `codex app-server` (`account/rateLimits/read`) — a
    read-only call with **no model-token spend** — reusing the hardened one-shot client that backs
    `codex_transfer`. The read is **ephemeral**: nothing is persisted, so `codex_status` stays a
    genuinely read-only call and no stale cache can mislead a spend decision. `rate_limit.source`
    is `app_server_live`.
  - Windows are re-slotted **by duration**, not by the app-server's slot order: `primary` is the
    shorter/rolling window (historically 5-hour), `secondary` the longer (weekly). The 0.144
    app-server reports only the windows that currently bind an account and may place the weekly
    window in the `primary` slot with no secondary — so a naive field rename would have kept the
    bug. An absent window is no longer treated as "unobserved," so a single healthy window now
    correctly reports `available` instead of a permanent `unknown`.
  - New `rate_limit.status` value `unavailable` (this codex/account exposes no quota data) and
    `rate_limit.source` value `app_server_live`; `codex_status`'s meaning changes from a cached
    paid-run snapshot to a live read (**breaking** under the versioning rules — a closed-schema
    output meaning changed, and `meta.rate_limit` is now `null` on current CLIs). A read that finds
    the method missing, the protocol drifted, or a malformed result is surfaced as `unavailable`
    (never as a plausible "no quota") with a note that the plugin may need an update — a loud
    signal, not another silent `unknown`. A committed real-shape fixture plus an integration test
    against the live app-server guard against the next such drift.
  - Untrusted app-server output is hardened: `planType` is length-bounded and
    `rateLimitReachedType` is accepted only from the known enum (an unknown value is dropped, never
    trusted as a false `exhausted`); a cached reason code degrades to `unknown` once every window
    has reset; a pathological numeric field (e.g. a 400-digit `usedPercent`) degrades to absent
    instead of raising; two windows are duration-sorted so `primary` is always the shorter horizon;
    and the read response is correlated on an unpredictable request id so a prequeued/unsolicited
    message can't be trusted as quota.
  - The dead exec-stream quota parser (`normalize.parse_rate_limit`), the per-run capture, and the
    snapshot cache (`CODEX_IN_CLAUDE_RATE_LIMIT_FILE`) are removed.

  Bumps `FINGERPRINT` (`schema-45` → `schema-46`) and `RESULT_FORMAT` (`3` → `4`) for the added
  enum values and the changed meaning of the `rate_limit` block.

## [0.12.0] - 2026-07-14

A reasoning-effort and operator-provenance release. Codex's reasoning effort is now a first-class,
discoverable control on every Codex-running tool, and the `CODEX_IN_CLAUDE_EXTRA_ARGS` passthrough
was narrowed so an operator config can no longer contradict the provenance the result envelope
reports. The passthrough narrows in three ways, each **breaking on the operator surface** — it now
refuses keys it used to accept — but nothing on the agent-visible surface was removed or retyped: no
tool, field, or error code changed shape. That surface did change four times (result `fingerprint`
`codex-in-claude/0.1/schema-40` → `codex-in-claude/0.1/schema-44`), so pre-1.0 this is a minor
release; clients that cache by `fingerprint` re-fetch the contract.

### Added

- **Reasoning-effort control, reporting, and per-model discovery** (#309). Every Codex-running tool
  (`codex_consult`, `codex_review_changes`, `codex_delegate`, their `_async` twins) and both dry
  runs take an optional `reasoning_effort` parameter, with a `CODEX_IN_CLAUDE_REASONING_EFFORT`
  server default (the per-call value wins; exact-`None` precedence, so an explicit empty string is
  passed through, not coalesced). The value is sent as a TOML-string-encoded
  `-c model_reasoning_effort=…` config override — codex-cli 0.144 has no dedicated flag — so the
  advertised open string round-trips exactly. A backend rejection of a sent value classifies as the
  new `invalid_reasoning_effort` error (a caller argument to correct, with a `codex_models`
  repair), never as contract drift; removal of the `-c` flag itself still fails loudly as
  `cli_contract_changed`. Shared shape bounds reject control characters, surrogate code points, and
  over-length values before any spend — in both dry runs too, which advertise the new error code.
  Reporting: `meta.reasoning_effort` (override provenance mirroring `meta.model`; `null` = Codex
  resolved it), `reasoning_effort` in `codex_status`'s `raw_defaults`/`resolved_defaults`, and both
  dry runs echo the effective `model`/`reasoning_effort` the previewed paid call would send
  (`codex_dry_run` also gains the `model` param for full preview parity). Discovery:
  `codex_models`/`codex://models` entries carry advisory `default_reasoning_effort` and
  `supported_reasoning_efforts`, read defensively from Codex's models cache. The persisted result
  format bumps (`RESULT_FORMAT` 1 → 2: `Meta` gained a field), so an older release replaying a new
  stored job result reports `job_result_incompatible` instead of corruption. Result `fingerprint`
  moves (`schema-41` → `schema-42`).

### Changed

- **BREAKING (operator surface): the extra-args passthrough can no longer set `model` or
  `model_reasoning_effort`** (#310, #309). `CODEX_IN_CLAUDE_EXTRA_ARGS` refuses those exact keys via
  `-c`/`--config` (plus, conservatively, case- and quote-varied lookalikes codex treats as distinct
  junk keys) at parse time with `extra_args_rejected`, before any spend: a passthrough value ran the
  call under the operator's setting while `meta.model` / `meta.reasoning_effort` still reported the
  per-call/server value — null in the common case — so the envelope's provenance was wrong. Migrate
  to `CODEX_IN_CLAUDE_MODEL` / `CODEX_IN_CLAUDE_REASONING_EFFORT` or the per-call parameters; both
  flow into `resolved_defaults` and `meta.*` correctly. Other `model_*` keys (`model_provider`,
  `model_providers.*`, `model_verbosity`, …) still pass through, and an opaque `--profile` remains
  the documented operator-trust boundary (COMPATIBILITY.md) — restated, not closed. `meta.model` now
  carries a published description defining it as override provenance (first-class controls only),
  not backend attestation. The `model` reservation is its own `fingerprint` move (`schema-40` →
  `schema-41`); the `model_reasoning_effort` reservation ships inside #309's bump above.

- **Bundled guidance and contributor docs** (#315, #317). The `collaborating-with-codex` skill now
  carries model- and reasoning-effort-selection guidance for the controls added in #309, and
  AGENTS.md describes agent identity through the `$agent_ids` roster allowlist instead of naming a
  specific agent. Skill/repo markdown only; no `fingerprint` change.

### Fixed

- **`codex_job_consume_result` no longer destroys a stored result it failed to deliver** (#306).
  Consume used to delete the job record *before* validating the payload, so a corrupt or
  cross-release result produced an `internal_error`/`job_result_incompatible` envelope about a
  record that no longer existed — unrecoverable by definition. The store's `result_payload` is now
  read-only and deletion is a separate, checked `discard` step that runs only after the envelope
  faithfully delivers the stored payload (a validated success **or** a validated stored error
  result; generated lifecycle/corruption/incompatibility envelopes never consume). Race semantics
  are analyzed and pinned by tests: one caller wins the delete per server process; a consume that
  loses the race — to a concurrent consume, TTL reaping, or count-cap eviction — reports
  `job_not_found` rather than delivering a second copy; a deletion failure still delivers the
  validated result and leaves the record to the TTL reaper. Removal is verified with a `stat`
  probe, `discard` reports a discriminated outcome (`REMOVED`/`MISSING`/`NOT_DONE`/`DELETE_FAILED`),
  and the record's `meta.json` marker is unlinked last — restored if the final `rmdir` fails — so a
  partial deletion failure leaves the record visible and reapable. Result `fingerprint` moves
  (`schema-42` → `schema-43`).

- **BREAKING (operator surface): quoted-root spellings no longer pass the extra-args root
  denylist** (#312). The root denial (`sandbox*`, `approval_policy`, `shell_environment_policy`)
  derived the root from the raw key while the exact-key denials normalized theirs, so a
  shlex-surviving quoted root (`-c '"sandbox_workspace_write".network_access=true'`) validated
  cleanly. This was **not** a sandbox bypass — codex's `-c` parser is literal (verified against
  `config_override.rs` at rust-v0.144.3), so the quoted spelling was a junk key codex never read, a
  silently-accepted no-op. The parser now normalizes the whole key once and derives the root from
  the normalized key, so root and exact-key checks share one conservative canonicalization and a
  misspelled-but-guarantee-shaped operator config gets loud feedback instead of silence. Also
  corrects the #287-era `_normalize_config_key` docstring: the normalization is deliberate
  over-matching so lookalike spellings can't probe the denylist, not a mirror of codex's TOML key
  parsing. Result `fingerprint` moves (`schema-43` → `schema-44`).

## [0.11.0] - 2026-07-13

A result-provenance release. Every result envelope now says which server release produced it, and
replaying a stored job result written by a different release is reported honestly as
incompatibility instead of corruption. Both changes are backward-compatible additions (a new field,
a new error code); the result `fingerprint` moves twice
(`codex-in-claude/0.1/schema-38` → `schema-40`), so clients that cache by `fingerprint` re-fetch
the contract.

### Added

- **Replaying a stored job result written by a different release is now reported as
  incompatibility, not corruption** (#305). Each job record carries the writer's persisted-format
  version (`RESULT_FORMAT`, stamped at spawn); a stored result that fails validation under a
  *different* recorded format returns the new `job_result_incompatible` error — `temporary: false`
  with a `start_new_job` repair — instead of `internal_error`'s dishonest "retry" advice (no retry
  can make a downgraded reader understand a newer payload). Same-format, missing-format, or
  unusable-format failures remain `internal_error` (corruption). Advertised on the five tools that
  can return a finished stored envelope (`codex_consult`, `codex_review_changes`, `codex_delegate`,
  `codex_job_result`, `codex_job_consume_result`). Downgrade replay itself remains unsupported —
  see COMPATIBILITY.md. Result `fingerprint` moves
  (`codex-in-claude/0.1/schema-39` → `codex-in-claude/0.1/schema-40`).

  Replay validation now also runs against the stored bytes as written: previously the reader
  patched `meta.job_id`/`meta.fingerprint` *before* validating, silently healing a corrupt value
  in those fields; such records now surface as `internal_error`. A committed snapshot
  (`tests/fixtures/result_format_snapshot.json`) guards the persisted format the way the manifest
  snapshot guards `FINGERPRINT`.

- **Every result envelope now reports the server release it came from** (`server_version`),
  beside the existing `fingerprint`. The two answer different questions and are not
  interchangeable: `fingerprint` is contract identity (which surface does this conform to —
  a cache key), `server_version` is release identity (which build produced this run). A
  downstream consumer — an MCP error audit, say — can now scope an analysis to a release
  instead of guessing from dates. Result `fingerprint` moves
  (`codex-in-claude/0.1/schema-38` → `codex-in-claude/0.1/schema-39`).

  A background job's result replays with the version of the run that *produced* it, never
  the version replaying it; a result persisted before this field existed replays with no
  version at all, rather than being stamped with a plausible-but-wrong one.

## [0.10.0] - 2026-07-12

A documentation-and-disclosure release. Nothing changed about what the tools do — no tool, field,
error code, or behavior was added, removed, or retyped, and the change is backward-compatible. But
the egress caveats now disclose that `codex exec` auto-loads the workspace's `AGENTS.md` and
`.agents/skills/`, which is a change to the *documented meaning* of the agent-visible surface, so the
result `fingerprint` moves (`codex-in-claude/0.1/schema-37` → `codex-in-claude/0.1/schema-38`) and
clients that cache by `fingerprint` re-fetch the contract. Pre-1.0, a surface change is a minor
release even when the code behind it is unchanged.

### Changed

- **`collaborating-with-codex` skill: close the isolation and independence gaps found by the
  2026-07-12 skill audit.** The server-down CLI fallback now carries the plugin's guarantee-bearing
  isolation flags at its strictest config isolation (`--ephemeral`, `--ignore-user-config`,
  `--ignore-rules`, `--disable remote_plugin`, explicit `--cd`) instead of `--sandbox read-only`
  alone, a rejected flag stops the fallback as CLI drift, and the guidance states that the
  read-only sandbox bounds writes, not reads (README's fallback one-liner matches). The independent-attempt
  workflow now requires Claude's attempt to be finalized before Codex's answer enters context
  (async-start, draft, then fetch — or draft before a sync call) instead of asking for unobservable
  non-conditioning after a sync call. The quota-snapshot guidance states a spend policy (defer
  non-urgent calls on `limited`/`exhausted`; treat `unknown`, stale, or `home_unverified` snapshots
  as uncertainty, neither permission nor denial) and covers `note`. The privacy rule is now atomic,
  with its facts moved to a new "Data exposure" section, and the polling rule is split into atomic
  obligations. Behavioral scenarios S11–S13 land with recorded baseline and treatment runs, and a
  packaging test now fails any scenario lacking a recorded passing treatment run. Skill files are
  not part of the MCP-discovered surface — no `fingerprint` change.

- **Egress caveats now disclose Codex's auto-loaded workspace context** (#300). `codex exec`
  automatically loads the resolved workspace's `AGENTS.md` and auto-discovers skills under
  `.agents/skills/` (verified against codex-cli 0.144.1), so their content can be sent to OpenAI
  even when the caller's prompt never mentions them — and the plugin's isolation flags do not
  suppress it. Every egress/privacy caveat (server instructions, `codex_status` caveat, tool
  descriptions and docstrings, `README.md`, `COMPATIBILITY.md`, `SECURITY.md`, the
  `collaborating-with-codex` skill) now states this; the behavioral assumption is recorded in
  `cli_contract.py`. Wording-only
  disclosure fix — no tool, field, or behavior changed; `fingerprint` `schema-37` → `schema-38`.

## [0.9.0] - 2026-07-11

A security-and-hardening release, centered on `codex_transfer` robustness and shutting off Codex's
`remote_plugin` connectors. The agent-visible surface changed across several increments (result
`fingerprint` `codex-in-claude/0.1/schema-29` → `codex-in-claude/0.1/schema-37`), so pre-1.0 this is
a minor release; clients that cache by `fingerprint` re-fetch the contract. Every change is
backward-compatible — no tool, field, or error code was removed or retyped.

### Security

- **Disable Codex's `remote_plugin` connectors on every model-bearing call** (#287). Codex 0.143+
  flipped `remote_plugin` to default-on, exposing named third-party connectors (GitHub, Gmail, Drive,
  Slack, …) to the model — network/data-disclosure channels outside the `--sandbox` filesystem
  boundary that the existing `--ignore-user-config` isolation did not neutralize (plugins load from
  marketplace snapshots, not `config.toml`). The server now sends `--disable remote_plugin` on every
  `codex exec` call as a guarantee-bearing flag — an unknown feature name fails loud as
  `cli_contract_changed` (zero spend) — and `CODEX_IN_CLAUDE_EXTRA_ARGS` refuses any attempt to
  re-enable it. Backward-compatible hardening; `fingerprint` `schema-36` → `schema-37`.

- **`codex_transfer` validates and bounds the identifiers the `codex app-server` reports on success**
  (#279) — the imported thread id, `$CODEX_HOME`, the ledger id, and `importId`. A drifted, oversized,
  control-character-bearing, or non-absolute value fails as `cli_contract_changed` (for the live
  protocol) or is skipped (for the best-effort ledger) instead of yielding a corrupt `resume_command`,
  which is now shell-quoted.

- **Full gitattributes filter isolation for propose-tier worktree git ops** (#163). Completes the
  repo-config hardening started in #156/#162. The propose-tier worktree git ops run in the server
  process, not Codex's sandbox, so a repo-configured `clean`/`smudge`/`process` gitattributes filter
  driver was repo-controlled code executing in-process. Every driver is now neutralized via
  highest-precedence `git -c` overrides enumerated per git call; a name that cannot be safely
  expressed as `-c` fails closed with zero spend. Same own-repo trust model as #156; internal only, no
  `fingerprint` change.

### Added

- **Opt-in extra `codex` args passthrough via `CODEX_IN_CLAUDE_EXTRA_ARGS`** (#231). An operator-only
  env knob adds allowlisted global `codex` options (`-c`/`--config KEY=VALUE`, `-p`/`--profile NAME`,
  `--enable`/`--disable FEATURE`) to every paid `exec` call, so a `model_provider`/profile can be
  selected even under `ignore-config` isolation. It is an allowlist: anything else — or a `-c` key
  under `sandbox`/`approval_policy`/`shell_environment_policy` that would weaken the advertised
  guarantees — is refused before any spend with a new `extra_args_rejected` error code. Secret `-c`
  values are read from the environment, never persisted to a job spec or echoed (in `codex_status` or
  errors); `codex_status` reports only `extra_args_configured`/`_count`/`_valid`. Backward-compatible;
  `fingerprint` `schema-30` → `schema-31`.

- **`codex_transfer` tool: hand off the current Claude Code session to a resumable Codex thread**
  (#230). Imports a Claude Code session transcript (`.jsonl`) into a persistent Codex thread via the
  experimental `codex app-server` and returns `resume_command` (`codex resume <thread_id>`) so the
  user can continue that exact conversation in Codex. Free — no model call — but it does create a
  thread in `$CODEX_HOME`, and transferring a live session is intentionally not idempotent (a new
  thread per call). New error codes `transfer_unsupported`/`transfer_failed`/`transfer_incomplete`;
  ships the `/codex:transfer` slash command. Backward-compatible; `fingerprint` `schema-29` →
  `schema-30`.

### Changed

- **Tracked Codex version bumped to `0.144`** (#286). `SUPPORTED_VERSIONS` now tracks `(0, 144)`; the
  CLI contract, help snapshots (`docs/codex-help/0.144.1/`), and `KNOWN_MODEL_SLUGS` fallback are
  verified against `codex-cli 0.144.1`. Advisory only — an untested version warns in `codex_status`
  but never blocks. The invoked flag surface, sandbox values, and app-server import contract are
  byte-identical to `0.142`, so no `fingerprint` change.

- **`fastmcp` 3.4.2 → 3.4.4** (supersedes the Dependabot bump in #260, which targeted 3.4.3 and could
  not pass CI on its own). Picks up upstream SSRF and OAuth hardening — transition-address SSRF,
  DNS-rebinding `Host`/`Origin` validation, and stricter OAuth redirect validation — plus proxy and
  JSON-schema fixes. It also rewraps tool-argument validation errors; the `invalid_arguments` fix
  below handles that.

- **`codex_capabilities` / `codex_status` serve their heavy payload schemas on demand** (#242). The
  last two discovery tools that inlined their full success closure in `tools/list` now opaque their
  heavy nested fields to compact pointers and publish the full schemas at two new resources
  (`codex://capabilities-result`, `codex://status-result`), also reachable via
  `codex_capabilities(include_schemas=[...])`. Cuts ~4.2 KB from cold-start `tools/list`; every
  top-level scalar stays advertised. Backward-compatible (schemas widened, not narrowed);
  `fingerprint` `schema-31` → `schema-32`.

- **Separate binding rules from facts across instructions, capabilities, and tool descriptions**
  (#243). A prose-only sweep from the agent-friendly-mcp audit: the `codex_status`-first rule now
  reads at one consistent strength across the three surfaces that carried three, imperatives buried in
  fact bullets or sentence tails were promoted to standalone directives, and a stale roadmap line was
  dropped. No semantic change; wording-only `fingerprint` `schema-32` → `schema-33`.

- **Consolidate the bundled Codex guidance into one router skill** (#290). `collaborating-with-codex`
  now owns shared safety and routes consult/review/delegate/transfer/async plus the independent-attempt
  and review–revise workflows to references loaded on demand; the former `deliberating-with-codex`
  entry point is removed. Bundled skills are outside `FINGERPRINT_COVERS`, so no `fingerprint` change.

- **Bundled skills now cover `codex_transfer`** (#234) — a "Choosing a tool" row, `/codex:transfer` in
  the slash-command list, and a per-tool note (free but not read-only; not idempotent for a live
  session). Skill markdown only.

- **Documentation freshness fixes** (#291). Added a `CODEOWNERS` file so every path has an owner, and
  swapped the README's hardcoded Python-version badge for a dynamic `pypi/pyversions` badge that
  tracks the trove classifiers. Docs/infra only.

- **Docs: README restructured for accuracy and audience** (#227). The quick start separates terminal
  commands from Claude Code input, the `propose`-tier / `delegate`-tool naming is reconciled at first
  use, the configuration table gains the previously-undocumented rate-limit variables, and
  background-job / rate-limit reference detail moved to `docs/REFERENCE.md`.

- **Docs: AGENTS.md rules separated from context, plus an input-domain testing rule** (#227, #273).
  "The result contract" now points at the `FINGERPRINT_COVERS` tuple instead of re-listing it (the
  prose copy had drifted), and buried rules became standalone bullets. A new Testing rule requires a
  new parameter's whole input domain — its boundary and invalid values — to be tested, not just the
  values current callers pass (prompted by #273, where a larger `BoundedCapture(head_bytes=…)` silently
  retained ~15× the byte cap while reporting `truncated=False`).

### Fixed

- **`codex_transfer` surfaces the child `codex app-server`'s stderr on crash-class failures** (#275).
  The captured stderr tail was never read by the error envelope, so a crashed (`cli_contract_changed`),
  wedged (`timeout`), or thread-less (`transfer_incomplete`) child left the agent a generic sentence.
  It now rides a dedicated, nullable, redacted, bounded `error.app_server_stderr_tail` — a separate
  channel from `error.message` because it is untrusted child output. Backward-compatible addition;
  `fingerprint` `schema-35` → `schema-36`.

- **`codex_transfer`'s app-server reader now bounds the memory it buffers** (#277). `_spawn_reader`
  fed every parsed message onto an unbounded queue, so a chatty app-server emitting valid progress
  faster than the single-consumer loop drained it grew process memory without bound until the timeout.
  The reader now enqueues only actionable messages onto a small bounded queue (restoring pipe
  backpressure), and a stop event releases a parked reader on teardown. Behavior-preserving; internal
  only. Codex (a different model) collaborated on the design.

- **`codex_transfer` rejects unresolvable transcript paths as `invalid_arguments`** (#278). An
  embedded NUL, a symlink loop, or an unstat-able path made `validate_transcript_path` raise, escaping
  as a retryable `internal_error` whose "retry" hint can never fix a bad path. Resolution and the file
  check are now guarded and return a stable, value-free `invalid_arguments` reason. Validation runs
  before any spawn, so there was never spend; error-code mapping only, no `fingerprint` change.

- **`codex_transfer` redacts and bounds every app-server-derived string before it reaches an error
  envelope** (#276). Four routes forwarded raw child text into `error.message` with only an 8 MiB
  per-line parser bound. A new `_display_text` helper redacts before truncating (cutting first can
  split a secret so no pattern matches) and bounds the result to 300 characters with an explicit
  `…[truncated]` marker. `error.message` prose only; no `fingerprint` change. (The success envelope's
  identifiers are handled by #279.)

- **`codex app-server`'s `stderr_tail` retains the tail, is byte-budgeted, and is safe to snapshot
  from another thread** (#254). The drain advertised a bounded tail but retained the prefix and counted
  characters, not UTF-8 bytes, so a verbose failure gave the operator startup noise instead of the
  error that killed it. It now accumulates into `_core.streamcap.BoundedCapture`, which budgets in
  bytes, gains `head_bytes=0` for a pure rolling tail, and is now lock-guarded against a torn read.
  Internal only; no `fingerprint` change.

- **Interactive streams get a bounded reader that cannot deadlock, and the app-server reader is now
  genuinely memory-bounded** (#255). `iter_bounded_lines` reads to `chunk_size`-or-EOF — correct for
  draining a finishing subprocess, fatal for a request/response protocol. A new
  `iter_bounded_lines_interactive` reads a binary stream via `read1` with an incremental decoder, so a
  line surfaces on its newline and a multibyte character is never split across a chunk boundary;
  `appserver` now uses it, which also fixes a latent unbounded-line buffer. Internal only; no
  `fingerprint` change.

- **The `invalid_arguments` envelope survives fastmcp ≥ 3.4.3's exception rewrap** (#271). Since 3.4.3
  a bad tool call raises `fastmcp.exceptions.ValidationError` (not a Pydantic subclass, no `.errors()`),
  so the middleware bypassed the result contract and emitted raw validator prose — which also echoed
  the rejected argument value. It now accepts both exception shapes and reads the structured errors off
  the wrapper's `__cause__`. Compatible across the supported `fastmcp>=3.4` range; the emitted envelope
  is unchanged, so no `fingerprint` bump.

- **`codex_transfer` fails closed when the auth probe is indeterminate** (#252). Its readiness gate
  rejected only a known-absent session, so a `codex login status` probe that timed out (reported as
  `None`) fell through and let the tool spawn `app-server` and issue a side-effecting import. The gate
  now requires a confirmed `True`, and the indeterminate case gets its own retryable
  `codex_auth_indeterminate` code rather than telling an already-authenticated caller to re-login.
  Backward-compatible addition; `fingerprint` `schema-33` → `schema-34`.

- **The test suite no longer corrupts the invoking repository when run with an inherited `GIT_DIR`**
  (#229). Under a pre-push hook launched from a linked worktree, an exported `GIT_DIR` made the
  fixtures' git calls operate on the real repo — staging every tracked file as deleted and rewriting
  its config. The shared test helpers now scrub the git-location env vars so every test git subprocess
  is anchored purely by `cwd`. Test-infrastructure only; no `fingerprint` bump.

- **An omitted `base`/`commit` on a `branch`/`commit` review no longer leaks the Python literal `None`
  into the error message** (#244). `invalid base ref: None` is now "base ref is required for a branch
  diff but was omitted", distinguished from a present-but-invalid ref (which keeps its `repr`). Message
  prose only; no `fingerprint` change.

- **Packaging and startup now declare POSIX-only and fail loudly on Windows** (#232). The
  `OS Independent` trove classifier was wrong — the async-job safety layer (`fcntl` locks, `os.killpg`,
  `SIGTERM` cancellation) is POSIX-only. The classifier is now MacOS + POSIX::Linux, the entrypoint
  refuses to start on a non-POSIX `os.name` (overridable via
  `CODEX_IN_CLAUDE_ALLOW_UNSUPPORTED_PLATFORM=1` for consult-only use), and the bare `import fcntl` in
  `_core/idempotency.py` is now guarded. WSL2 is unaffected. Not `fingerprint`-covered.

- **Exception-derived `internal_error` messages no longer leave a dangling `": "` separator** (#203).
  Empty or fully-redacted exception text now renders as just the exception class name. Message prose
  only; no `fingerprint` bump.

- **A transient read `OSError` on an idempotency record no longer classifies as permanently
  unavailable** (#202). `IdempotencyIndex._read` mapped any `OSError` to `"corrupt"` → a non-retryable
  `use_new_idempotency_key`, so a momentary I/O blip while reading a healthy record told the agent,
  permanently, to start a new paid run under a fresh key. It now distinguishes a transient `io_error`
  (a retryable `internal_error`) from genuine corruption (still fails closed), and `sweep()` bounds how
  long an unreadable entry can hold its key. The existing `internal_error` code is reused, so no
  `fingerprint` bump.

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

- **`codex_transfer` no longer blames the transcript for a drifted import request** (#256). Every
  non-`-32601` JSON-RPC error on the `externalAgentConfig/import` request mapped to `transfer_failed`,
  whose repair hint says to inspect the message and confirm the transcript is a complete Claude
  session — advice that can never succeed when the real cause is that the plugin's request params no
  longer match the CLI's schema. JSON-RPC 2.0 already partitions the space: codes in the reserved
  `-32768..-32000` range (invalid params/request, parse/internal error, and the server-defined
  `-32000..-32099` band), and errors carrying no integer `code` at all — absent, `null`, a string, a
  JSON float (`-32601.0 == -32601` in Python, so a float would otherwise be read as method-not-found
  and wrongly advise updating codex), or a JSON `true` (`bool` is a subclass of `int`) — are
  protocol/request-level faults and now surface as `cli_contract_changed` — restoring the fail-loud
  contract that a drifted request is the plugin's problem, not the user's. An application-range code
  remains a genuine import rejection (`transfer_failed`), and `-32601` still means the installed codex
  is too old (`transfer_unsupported`). No `FINGERPRINT` bump: both codes were already advertised on
  `codex_transfer` and the discovered surface is unchanged.

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
