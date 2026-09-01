# Changelog

All notable changes to this project are documented here. Pre-1.0, minor versions may change the
agent-visible MCP surface; the result `fingerprint` changes when they do.

## [Unreleased]

### Added

- **Run envelopes now record which `codex` build served the run** (#519): `meta.codex_version`
  carries the display copy of `codex --version` observed for that run -- on consult, review and
  delegate results alike, sync and async, and on a run that failed as well as one that succeeded
  -- and it replays with a stored job result, so a persisted envelope can answer "which codex
  produced this?" after the fact. Previously `meta.server_version` named only the *plugin's* release and `codex_version`
  rode the `codex_status`/`codex_capabilities` envelopes alone, leaving the PATH-decided binary
  silently swappable between runs. The value is probed immediately before the run's exec, on the
  same executable token, working directory, and environment it is about to spawn, and never
  cached: every run -- sync included -- executes in a fresh worker process, so a cache would save
  nothing on the paid path while widening the window in which the reported version is not the one
  that served the run. It is **best-effort observation, not attestation**: it comes from a second
  process, so a binary replaced or a shim retargeted between probe and exec would go unreported --
  the `codex exec --json` stream carries no version of its own (re-verified at codex-cli 0.151.0).
  It is null or absent whenever nothing was recorded -- no run was launched (argument-validation
  and `codex_job_*` lifecycle errors, dry-run previews, async job-start handles), the probe failed
  or could not run, the spawn found no binary, a background worker crashed unexpectedly (its
  envelope is rebuilt from the job spec, which holds no observation of the run), or the payload
  predates this field -- and its absence is never evidence that no run happened. The resolved binary *path* is deliberately not reported: it is
  operator-controlled text this server withholds from the wire, and it is not reliably a path at
  all. The `fingerprint` moves `schema-89` -> `schema-90`, and `RESULT_FORMAT` moves 11 -> 12
  (a stored success envelope gains the key, which an older reader's closed schema would reject).
  Backward-compatible: a field addition, no input or guarantee changed.

### Changed

- **`codex-cli 0.152.0` is the supported version** (#586): `SUPPORTED_VERSIONS` moves
  `{0.151}` -> `{0.152}`, so `codex_status` no longer warns "outside the tested set" on a machine
  running 0.152.x. It is a **replacement**, not an addition -- the project tracks a single verified
  minor. No agent-visible surface changed, so the `fingerprint` does not move. The
  `docs/UPGRADING-CODEX.md` checks were run against the installed 0.152.0 with 0.151.0 retrieved
  side by side from npm (channel parity confirmed against a same-version npm copy): the mechanical
  drift check is clean (all 12 `ALWAYS_SEND` flags, the
  help-gated `--model`, and all three sandbox values present, no new unconsumed flag); every
  captured `--help` surface -- `codex`, `exec`, `review`, `exec review`, and `app-server` -- is
  byte-identical between the two binaries; `codex features list` gained four rows and lost none;
  `KNOWN_MODEL_SLUGS` still matches the live cache (the same eight slugs, reasoning-effort
  discovery fields unchanged in shape); the app-server schema diff added one v2 message this plugin
  does not consume (`AuthRecoveryNotification`) and left six of the seven consumed schemas
  byte-identical, with `GetAccountRateLimitsResponse` gaining two optional additive fields
  (`accountId`, `rateLimitUpsell`) the key-based reader ignores; the `--strict-config`,
  retired-setting, and invalid-value grammars all still parse; the two sandbox pins
  (`network_access`, `writable_roots`) hold against both the config file and a `--profile`, each
  with a live positive control; the reasoning-effort key is still applied (both bracketed rejection
  markers); `--sandbox read-only` still blocks writes and `--output-last-message` still receives
  the final message (each with a live positive control); the `SandboxWorkspaceWrite` field set is
  unchanged between the two binaries, so no new widening key arrived; the live app-server
  rate-limit read still returns a quota block (`test_live_rate_limits_read_roundtrip`); all four
  `remote_plugin` mechanism arms still hold, positive control included; structured output still
  conforms; and the implicit-context presence matrix is identical across both binaries on every row
  and every variant. The read-boundary probe was **not** re-run: it is prescribed only when a
  release touches the sandbox, `--sandbox` values, or filesystem access, and every one of those
  surfaces is byte-identical here. `docs/codex-help/0.152.0/` carries the captures from the binary
  actually verified, and the 19 live integration tests pass against it.

- **`sleep_tool` assessed and recorded; the posture is unchanged** (#586): 0.152.0 added a
  `stable`, default-on `sleep_tool` feature that can expose a `clock` tool namespace whose `sleep`
  function accepts a `duration_ms` of up to 43,200,000 (12 hours) -- longer than either of this
  server's deadlines. Captured wire requests (zero spend, with a positive control) show it is
  **not** exposed on the default `codex exec` path at 0.152.0: exposure is gated on the feature's
  `mode`, and under the default `model_driven` it requires the selected model to advertise `clock`
  in `experimental_supported_tools`, which is empty for all eight slugs in the model cache observed
  here. That cache is account-scoped, so this says nothing about other accounts or later catalog
  refreshes.
  Setting `mode = "always_on"` does expose it, and `--disable sleep_tool` removes it again. The
  plugin therefore sends no new flag; because the posture rests on backend model metadata that can
  change with no CLI upgrade, adopting the disable is tracked separately as a deliberate behavior
  change. `COMPATIBILITY.md` records the mechanism, the probe, and the re-check.

- **Recorded that 0.152.0 drops `update_plan` from the default tool set** (#586): restored with
  `-c tools.update_plan.enabled=true`. Nothing here references `update_plan` and JSONL parsing is
  tolerant, so no contract or parser dependency here changed -- though the model-facing tool
  catalog itself did, and that is an upstream behavior change this bridge simply has no stake in.
  It is documented because neither the `--help` surfaces (byte-identical) nor the `features list`
  diff (four added rows) can see it at all -- the model-facing request is a separate surface, and
  `COMPATIBILITY.md` now names the probe that observes it.

- **The sync-probe spawn shim is gone; `pontonier` pins to `0.8.0`** (#577): `probe.py` existed only
  because `pontonier` 0.7.0's `run_sync_capture` wrapped spawn and drain in one `try` and caught just
  `(FileNotFoundError, NotADirectoryError)`, so an unusable `codex` on `PATH` -- an executable
  directory, a file without the execute bit, `ENOEXEC` -- escaped a probe documented never to raise
  (#541). `pontonier` 0.8.0 isolates the `Popen` phase and returns `binary_missing` for every spawn
  failure, matching `run_async`, so the module and its errno/filename inference are deleted and
  `codex_version`, `login_status`, and `preflight._probe_help` call `runtime.run_sync_capture`
  directly. **The shim removal itself changes no behavior**: the end-to-end guard in
  `tests/test_sync_tool_guard.py` -- an executable directory named `codex` driven through
  `codex_status` -- still reports a readiness fact, and was confirmed to fail without the fix before
  this landed. The `fingerprint` does not move.
- **A `codex` run emitting invalid UTF-8 no longer loses its output, and cannot fabricate a session
  id** (#577, #578): separately from the shim removal above, `pontonier` 0.8.0 decodes captured
  output with `errors="replace"` rather than strictly, so a stray non-UTF-8 byte now yields text
  containing U+FFFD where 0.7.0 raised `UnicodeDecodeError` out of the sync probe and silently
  discarded the async capture. That is the better trade for diagnostic text -- U+FFFD is ordinary
  printable text (category `So`), so the control-character and redaction handling treat it as any
  other character -- but it made one new shape reachable: an invalid byte *inside* a JSONL
  `thread_id` now parses into a valid-looking identifier rather than destroying the capture.
  `session_id` is not prose, so `normalize` now rejects an identifier carrying U+FFFD and reports it
  as absent: a lossy id would send an agent to `codex resume` against a thread that never existed,
  with nothing in the envelope marking the substitution. A later intact id in the same stream still
  wins.
- **The served contract is written against MCP `2026-07-28`, on both protocol eras** (#571,
  ADR 0004 amendment): fastmcp 4 serves the legacy `initialize` handshake and the modern
  `server/discover` era side by side with no knob to restrict either, so this release makes the
  modern path conformant instead of merely tolerated. A `resources/read` of an unknown URI now
  returns the era-correct numeric — still `-32002` on a handshake-era connection (what Claude Code
  negotiates today: a live capture of 2.1.252 shows `initialize` at `2025-11-25`, roots advertised,
  and no `server/discover` on stdio), and `-32602` on a `2026-07-28` connection, whose spec forbids
  `-32002` (SEP-2164); the `error.data` envelope is identical on both, and
  `resource_error_carrier` states both numerics with their eras. The manifest snapshot gains a
  `discover` section (the modern era's supported versions, capabilities, instructions, and
  `ttlMs`/`cacheScope` hints, minus the release-variable server version) and a
  `modern_result_envelopes` section (the modern `resultType`/`ttlMs`/`cacheScope` wrapper on every
  list/read method the caching spec covers, and the `tools/call` wrapper from one free call; the
  items inside are pinned equal across eras),
  disclosed as the new `fingerprint_covers` tokens `discover_response` and
  `modern_result_envelopes` — the two eras already disagree on the wire (`listChanged`), so the
  legacy capture could not stand in for them. `roots_source`'s `probe_failed` description no
  longer promises that retrying may help: it does on a handshake-era connection and never on a
  `2026-07-28` one, where this server's push-style probe cannot run (the modern round-trip roots
  path is deferred). The roots capability gate now reads the SDK's era-neutral
  `session.client_capabilities` instead of the handshake-era `client_params`, so a `2026-07-28`
  request that declares roots without client info is no longer misreported as `not_negotiated`;
  a real modern-client test pins the emitted `probe_failed`. `protocol_revision` moves to
  `2026-07-28` and its SDK-drift guard is strict again (#570 had pinned the gap). Roots stay a
  handshake-era fallback (`probe_failed` on modern connections, unchanged); a dedicated modern-era
  `roots_source` value, cache-hint tuning, and the tasks extension are deferred and recorded in the
  ADR. The result `fingerprint` moves `schema-88` -> `schema-89`. **Breaking** for a client on a
  `2026-07-28` connection that matched resource-not-found on the numeric: `resource_error_carrier`
  documented `-32002` with no era qualifier while the server served both eras, so the modern
  value moving to `-32602` narrows a documented guarantee (AGENTS.md § Versioning; the safe
  direction, per #193). The handshake-era value is unchanged, and `error.data.code ==
  "resource_not_found"` is stable on both eras — classify on that, as the carrier now says.
  `protocol_revision` is documented as the target, not a per-session promise, and is not itself
  breaking.
- **Ported to fastmcp 4.0.0 / mcp 2.1.1** (#570): dependency caps move to `fastmcp>=4.0,<4.1`
  plus an explicitly declared `mcp>=2.1,<2.2` (imported directly; previously ridden as a
  transitive dependency — the class of gap that caused #572). The mechanical surface: the SDK's
  `McpError` -> `MCPError` rename and keyword constructor in the resource-error middleware; the
  #424 UI-extension filter reads the now-declared `ServerCapabilities.extensions` field instead
  of `model_extra` (and is tested on BOTH protocol eras fastmcp 4 serves); the roots probe moves
  from the removed `Context.list_roots()` to the SDK session (legacy-era only — a modern
  2026-07-28 connection has no back-channel, reports `roots_source: "probe_failed"`, and falls
  back exactly as a root-less client always did), now guarded by a live-path regression test
  driving the real server through a real client, since the doubles-only coverage is how the dead
  path stayed green; the manifest capture pins `Client(mode="legacy")` because fastmcp 4's
  default negotiates the modern era where `initialize_result` is `None`. The legacy `initialize`
  capture loses its empty `capabilities.experimental` map (an mcp-2 serialization change, not a
  capability change), so the result `fingerprint` moves `schema-87` -> `schema-88`; nothing is
  breaking. `protocol_revision` deliberately stays `2025-11-25` — the SDK's own target moved to
  `2026-07-28`, but declaring it before the era migration (error renumbering, cache hints, the
  discover-era manifest) would overstate conformance; the drift guard now pins that gap
  explicitly on both sides, and #571 owns closing it. Tests drop every camelCase SDK-field read
  and run with fastmcp's compat bridge OFF, so a camelCase read fails today instead of on the
  FastMCP 5 upgrade.

### Fixed

- **A run whose output capture failed now says so instead of reading as a slow model or a bare
  crash** (#579): `pontonier` 0.8.0's `CommandRun.capture_failed` is set when one of the plugin's
  own capture threads (stdout or stderr -- the flag does not say which) dies. Forced against the
  pinned release, that produces two shapes on the model-bearing path -- a clean exit 0 with an
  empty event stream and an intact `--output-last-message` file, or, when codex keeps writing, a
  child blocked on the undrained pipe until the deadline and a `timeout` that is really a bridge
  fault. `classify_failure` now names the lost capture in exactly those two places, hedged to what
  the flag proves: the `timeout` message says codex may have been blocked rather than slow, and
  its `repair.alternative` says to retry the same call once before treating it as an ordinary
  timeout (the table's alternative, which assumes a retry will time out again, is overridden for
  this case only); the generic `nonzero_exit` message notes that part of the output may have been lost and
  its diagnosis may be incomplete. No classification changes -- a recognizable auth, drift, or
  rate-limit signature still wins -- and the success path deliberately ignores the flag: the
  answer is the file, so a dead capture thread costs at most the stream-derived
  `usage`/`session_id`, which read as absent as they are documented to. These are
  `error.message`/`repair.alternative` prose changes outside the discovered surface, so the
  `fingerprint` does not move.

## [0.22.0] - 2026-08-31

An install-repair and hardening release. The headline is the dependency cap: fastmcp 4.0.0
published and every earlier release's uncapped `fastmcp>=3.4` floor now resolves it and fails at
import, so fresh installs — including the `.mcp.json` plugin path — are broken until this release
supersedes them. Alongside the cap, the three free diagnostics gain the same envelope guarantees
as the paid tools, and the `developer_instructions` docs stop overstating what the parameter and
its fingerprint prove. The result `fingerprint` moves `schema-85` → `schema-87` (both hops
additive or wording-only); nothing is breaking. The fastmcp 4 port itself lands separately
(#570).

### Fixed

- **Fresh installs no longer resolve the incompatible fastmcp 4** (#572): every released version
  declares `fastmcp>=3.4` with no upper bound, so once fastmcp 4.0.0 (on mcp 2.1.1) published, a
  fresh install resolved it and failed at import (`cannot import name 'McpError' from 'mcp'`) —
  including the `.mcp.json` plugin path. The floor is now capped at `<4`. Already-published
  versions cannot be repaired, only superseded by releasing this cap. The fastmcp 4 port itself is
  #570.

- **The three free diagnostics can no longer fail as a raw MCP protocol error** (#541):
  `codex_status`, `codex_capabilities` and `codex_models` are synchronous, so the `_guard`
  decorator — which wraps async tools — never covered them, and anything they raised escaped the
  handler instead of returning the documented envelope. That was worst for `codex_status`, whose
  entire job is to answer "is codex usable right now?": the tool an agent reaches for *because*
  something is already wrong was the one least able to report it. All three are now guarded and
  advertise `internal_error` in their per-tool `error_codes`; their output schemas already carried
  a success|error union, so no schema shape changed. The result `fingerprint` moves `schema-86` ->
  `schema-87`. Adding an error code is backward-compatible, so this is not breaking.
- **A `codex` on `PATH` that cannot be executed is a readiness fact, not a crash** (#541):
  `binpath.codex_bin()` deliberately falls back to the bare literal `"codex"` when resolution finds
  nothing, and `subprocess` then does its own `PATH` lookup *without* the `is_file()` + `X_OK`
  predicate `binresolve` applies to its own candidates. An executable directory named `codex` (or a
  file with the execute bit set that the kernel cannot exec) therefore raised `PermissionError` /
  `OSError(ENOEXEC)` straight out of the probes. Those spawn failures are now classified the way a
  missing binary already was, so `codex_status` reports `codex_found: false` instead of raising.
- **The three free tools now report their own pinned posture on an invalid-argument error**
  (#541): `README` documents that every shipped tool pins its own tier and sandbox and ignores
  `CODEX_IN_CLAUDE_TIER_DEFAULT` / `CODEX_IN_CLAUDE_SANDBOX_DEFAULT`. Because the three unguarded
  tools had no entry in the posture map, their `invalid_arguments` envelope fell back to
  `config.defaults()` and reported whatever an operator had configured — an operator running
  `TIER_DEFAULT=propose` saw `codex_capabilities` claim `propose`/`workspace-write`. Guarding them
  registers their real posture and restores the documented promise. `meta` values are not part of
  the discovered surface, so this moves no fingerprint of its own.

### Changed

- **`developer_instructions` now documents that compliance is best-effort** (#563): the only
  "instructed, not compelled" statement on the surface was scoped to *verdicts*, which reads as a
  promise that everything else applies. It does not — Codex may honor the caller text in full, in
  part, or not at all, and no result field attests which happened — `meta`'s fingerprint attests
  the request the server accepted and staged, never what the model did with it. Non-compliance may be silent,
  though the server's framing does instruct Codex to disclose a conflict with the rules above it,
  and `detail="full"` returns the raw model text. Stated once in the parameter contract (inline
  summary and `codex://params` full text), and echoed in `README.md`, `COMPATIBILITY.md`, and the
  bundled skill. The result `fingerprint` moves `schema-85` -> `schema-86`; documenting an
  existing limit weakens no guarantee, so this is not breaking.
- **The bundled skill no longer overstates what the fingerprint proves** (#564): the
  options-and-errors reference called it "how you tell a steered run from a default one". It
  separates a steered *request* — it attests what the server SENT, not what the model did — and
  its absence means only that the per-call parameter was omitted, not that the run was otherwise
  unsteered. The same reference now also states what validation does and does not cost: a value
  the server REFUSES costs nothing — unsafe text, the byte cap, framing markers, and the combined
  input budget are all rejected pre-spend — while a value it ACCEPTS goes straight into the paid
  run, so there is no free positive check. Because the checks stop at the first failure in
  unsafe -> byte cap -> marker order, a value that is both over-cap and marker-bearing reports
  only the cap.

## [0.21.0] - 2026-08-30

A caller-steering release. Consult and review gain an optional `developer_instructions`
parameter — caller stance for Codex's developer turn, always behind this server's framing, with
a `{sha256, bytes}` fingerprint reported in `meta` — and the operator passthrough loses the
ability to set the instruction-bearing config keys that could outrank that framing (the one
breaking change, operator surface only). The bundled skill teaches agents how to route content
into the new parameter and how to recover its refusals. The result `fingerprint` moves
`schema-84` → `schema-85` and the persisted `RESULT_FORMAT` 10 → 11 (both additive); the tracked
Codex version moves to `0.151` with no contract break.

### Added

- **`developer_instructions` on `codex_consult`, `codex_consult_async`, `codex_review_changes`,
  and `codex_review_changes_async`** (#556): optional caller stance/focus text for Codex's
  developer turn, composed into ONE `-c developer_instructions` value — this server's framing
  always leads, the caller text is delimited on both sides, and the closing marker outranks
  anything between the markers. codex places the value as the first developer-role message,
  ahead of its own developer messages (verified via `codex debug prompt-input` on `0.151.0`), so
  that placement is disclosed rather than reordered. Normalized once (stripped; blank =
  omitted); refused pre-spend over 4096 bytes, on control characters (C0 except tab/LF/CR, DEL,
  NUL) or lone surrogates, or when the text carries a framing-marker line (`invalid_arguments`
  with a reason naming `forged_framing_marker`); counted against
  `CODEX_IN_CLAUDE_MAX_INPUT_BYTES` with the call's other caller-authored inputs. Emitted only
  when text is present, so the common run's argv is byte-identical to `0.20.0`'s and never arms
  `--strict-config`; an instruction-carrying run does arm it, and a strict-config rejection of
  the plugin's own key is attributed to the plugin (`cli_contract_changed`), never to the user's
  config. `meta` gains a `developer_instructions` `{sha256, bytes}` fingerprint on sync results,
  the async launch handle, and fetched job results; the text itself is never echoed, though it
  does ride the codex command line and the on-disk background-job record — the tool description
  and skill disclose both carriers. Deliberately NOT on `codex_delegate`/`codex_delegate_async`.
  New `developer_instructions` parameter contract in `codex://params`. `fingerprint`
  `schema-84` → `schema-85`; persisted `RESULT_FORMAT` 10 → 11 (new `Meta` field). Non-breaking
  (additive).
- **The bundled skill teaches when and how to use `developer_instructions`** (#560): a
  content-routing rule (stance/persona/emphasis in `developer_instructions`; the target via the
  review scope or a consult's `question`; facts and quoted artifacts in `extra_context`; data
  never in the developer turn), refusal recovery for `forged_framing_marker` and the two byte
  limits, a dry-run caveat (`codex_dry_run` takes no `developer_instructions`), an independence
  hazard (a stance hinting at Claude's approach forfeits the two-member pattern), and framing
  stability across review–revise passes with the `meta.developer_instructions` fingerprint as
  the equality check. SKILL.md gains a Data-exposure bullet for the argv/job-record carriers and
  three binding rules (routing, exposure, independence) so the rules agents apply are as wide as
  the disclosures. Skill prose only — no wire text.

### Changed

- **BREAKING (operator surface): the extra-args passthrough can no longer set the
  instruction-bearing config keys** (#555). `CODEX_IN_CLAUDE_EXTRA_ARGS` now refuses
  `-c developer_instructions=…`, `model_instructions_file`, its deprecated alias
  `experimental_instructions_file`, the documented-as-reserved `instructions`, and
  `model_catalog_json` (a catalog entry can redefine a model's `base_instructions`) — plus the
  usual case/quote lookalikes — at parse time with `extra_args_rejected`, before any spend.
  Every framing string this server sends rides the user turn while `developer_instructions`
  lands as the *first* developer-role message, so a passthrough value outranked the server's own
  guardrails with no `meta` record; the per-call, meta-reported parameter (above) is the
  first-class replacement. `--profile` and, at `inherit` isolation, the user's `config.toml`
  remain the documented operator-trust boundary. Not itself a `fingerprint` move: the denylist
  is not part of the discovered surface.
- **pontonier 0.6.0 → 0.7.0**: the caller text crosses the backend adapter on the new
  first-class `RunRequest.instructions_append` field (pontonier#12) instead of riding
  `extra_args`, which the protocol reserves for operator descriptors;
  `CodexBackend.validate_request` mirrors the server boundary and `prepare()` fails closed for
  direct adapter callers (#558).
- **Tracked Codex version is now `0.151`.** `SUPPORTED_VERSIONS` tracks `(0, 151)`; a `0.149`
  or `0.150` CLI still runs and reports the advisory untracked-version warning in
  `codex_status`. Verified end to end against `codex-cli 0.151.0` and A/B'd against a
  side-by-side `0.149.1`. **No contract break and no agent-visible change.**
  - All 12 `ALWAYS_SEND` flags, the `--model` help-gated flag, and all three `--sandbox` values
    are present; the five relevant help screens are byte-identical to `0.149.1`.
  - Both security pins re-verified live with positive controls, each against the config file
    **and** an operator `--profile`: `sandbox_workspace_write.network_access=false` still blocks
    egress, and `sandbox_workspace_write.writable_roots=[]` still blocks writes outside the
    workspace; upstream's `SandboxWorkspaceWrite` struct still carries exactly the four fields
    `COMPATIBILITY.md` accounts for.
  - The `--strict-config` grammars still parse, `--ignore-user-config` still exempts the config
    file, and the app-server schema diff left all seven consumed schemas byte-identical.
  - `KNOWN_MODEL_SLUGS` is unchanged, and the implicit-context presence matrix (`AGENTS.md`
    sources and both skills roots) is identical to `0.149.1` under the read-forbidding probe.
    `0.151` adds a default-off `skip_host_skill_discovery` feature flag — inert here, but the
    flag to re-probe first when it stages.
  - New `0.151` surface is deliberately not adopted; `remote_plugin`, `view_image`, and
    `recommended_plugins` all hold their recorded stage and default.

## [0.20.0] - 2026-08-26

A compatibility and diagnosis release. Nothing on the agent-visible surface changed: the result
`fingerprint` stays at `codex-in-claude/0.1/schema-84` and `RESULT_FORMAT` at `10`, and there is
no breaking change.

The tracked Codex version moves to `0.149`, verified end to end and A/B'd against `0.148.0`.
Two more ways a user's own Codex config can refuse to start are now diagnosed instead of surfacing
as a bare `nonzero_exit`: a setting `0.149` retired, and a value of the wrong variant or type.
Each is attributed to whoever sent the key — the plugin's own pins only when this run actually
emitted them. On WSL2, `codex` is resolved to the WSL-native install instead of a Windows-side
shim that Windows-PATH interop can put first on `PATH`, and a new `CODEX_IN_CLAUDE_CODEX_BIN`
override names a binary outright.

### Added

- **`user_config_rejected` now covers two more config-parse failures** — a setting codex has
  RETIRED (#542; `0.149` retired the `untrusted` approval policy and refuses to start when the
  user's config still selects it) and an INVALID value, the wrong enum variant or the wrong TOML
  type (#550; a plain typo in `config.toml`). Both are distinct grammars from the `--strict-config`
  unknown-KEY one — the key exists and only its value is refused — and neither matched a drift
  signature, so each surfaced as a bare `nonzero_exit` with the diagnosis lost. Neither needs a
  `-c` pin or `--strict-config`, and both fire at the default `inherit` isolation, so they are the
  failures a user meets on the first run after an upgrade or an edit. Captured verbatim from
  `codex-cli 0.149.1`; each recognizer is anchored to the whole of stderr so a config-shaped pair
  quoted ahead of a genuine auth/drift/rate-limit diagnostic cannot steal the classification. The
  offending value is never echoed — it is free text the user typed into the wrong key, plausibly
  a secret no pattern-based redactor recognizes — while what codex *expected* (the allowed
  variants, or the type) is surfaced as the actionable content. Because these messages name no
  file, an error under an operator `--profile` discloses that the profile may be the source rather
  than asserting the user's own config, and the repair guidance is written for a refused value
  rather than inherited from the unknown-key prose. Attribution follows the `--strict-config`
  discipline, keyed on who sent the rejected key *on this run*: one of the plugin's own pins —
  emitted only on `workspace-write` runs and effort-carrying runs, and verified live to outrank a
  bad file value entirely, so the refused value can only have been ours — is `cli_contract_changed`;
  an operator passthrough key, or a dotted child of one (codex echoes a `-c t={k=v}` parent-table
  assignment as `t.k`), is `extra_args_rejected`; anything else is `user_config_rejected`. No new
  error code, so no `FINGERPRINT` change.
- **`CODEX_IN_CLAUDE_CODEX_BIN` names the `codex` binary to invoke** (#538). A non-empty value is
  used exactly as given, with no `PATH` re-resolution, and must be an executable file on disk: a
  missing path, a directory, or a file without the execute bit is reported by `codex_status` as
  `codex_found: false` with a `readiness_detail` naming the env var — never its value, which is
  operator-controlled and unbounded — and by every paid run as `codex_not_found`, zero spend,
  rather than as an `internal_error` inviting a retry. Documented in the README's Configuration
  table and `COMPATIBILITY.md`.

### Changed

- **Tracked Codex version is now `0.149`.** `SUPPORTED_VERSIONS` tracks `(0, 149)`; a `0.148` CLI
  now warns in `codex_status` (advisory only — it never blocks). Verified end to end against
  `codex-cli 0.149.1` and A/B'd against a side-by-side `0.148.0`. **No contract break:** all 12
  `ALWAYS_SEND` flags, `--model`, and all three sandbox values are present and unchanged.
  - Re-verified live, each with its own positive control: the `workspace-write` network-egress and
    writable-roots pins (both still outrank the config file *and* `--profile`); the read boundary on
    both tiers, with the write negative control proving the sandbox was in force; `--ignore-rules`;
    `model_reasoning_effort`; `--output-schema` conformance together with `--output-last-message`
    receiving exactly the final `agent_message`; and the `--strict-config` rejection grammar,
    unchanged in both forms.
  - The implicit-context marker probe (`AGENTS.md` sources, both skills roots, all four variants)
    produced a **presence matrix identical** to `0.148.0` — notable because `0.149` reworked skill
    selection and carried an upstream change titled "Enforce filesystem permissions when loading
    `AGENTS.md`".
  - `KNOWN_MODEL_SLUGS` gains `gpt-reserve`. A contemporaneous cache-free A/B had `0.148.0` fetch
    the identical set, so this is a backend catalog move, not a client change. Its
    `visibility: "hide"` is deliberately not filtered (#547).
  - The `0.148.0` → `0.149.1` app-server schema diff leaves six of the seven consumed schemas
    byte-identical; `GetAccountRateLimitsResponse` gains two `PlanType` enum values (`edu_plus`,
    `edu_pro`), absorbed because `planType` is read as a bounded free-form string.
  - New `0.149` surface is deliberately not adopted: the `agents` and `queue` subcommands and
    `exec --thread-source` are unused, and the retired `untrusted` approval value was never sent.
    `remote_plugin`, `view_image`, and `recommended_plugins` all hold their recorded postures.
- **The `remote_plugin` guarantee now says which half of it is verified** (#542, #548). The
  mechanism half is re-verified and pinned in the live integration suite: `--disable` wins over
  `--enable` in either order and over `-c features.remote_plugin=true`, and an unknown feature name
  still fails loud. The tool-surface half — proving no connector *tool* is exposed — needs a machine
  with a connector installed as a positive control, and none is; it is recorded as **not exercised**
  rather than as a pass, and `COMPATIBILITY.md` tells the next agent to check for that control
  first. Upstream `0.149.0`'s "Remove the workspace settings gate for apps and plugins" touches only
  the `app-server` and `chatgpt` crates, so it does not reach the `codex exec` path this covers.
- `collaborating-with-codex` skill: the spend step describes only the live `codex_status` quota
  read; the readiness gate, `blocked` refusal, cap declaration, async preference, and composition
  opt-in gate are now explicit Binding rules; the compound `Delegation` and `Retry` rules are
  split; the untrusted-workspace rationale moved to Data exposure; and the transfer reference notes
  that a resumed thread runs under Codex's own `AGENTS.md` auto-loading and skill discovery,
  outside the plugin's flags.

### Fixed

- **On WSL2, `codex` subprocesses resolve the WSL-native install instead of a Windows-side shim**
  (#537, #538). WSL2 — this project's documented way to run on Windows — forwards the Windows
  `PATH` into the WSL `PATH`, so the bare `"codex"` every spawn site used could resolve to a
  Windows npm-global shim and fail confusingly (`codex_status` reported `codex_found: false` next
  to a fully populated `flags_warning`). Every spawn site now goes through `binpath.codex_bin()`,
  resolved once per process: under WSL2 (`$WSL_DISTRO_NAME`, or `microsoft` in `/proc/version`) it
  probes `$HOME/.local/bin/codex`, `/usr/local/bin/codex`, then the npm global bin dir (from
  `npm prefix -g`; `npm bin -g` was removed in npm 9+) ahead of `shutil.which("codex")`; on any
  other host it goes straight to `shutil.which`, so a stale `~/.local/bin/codex` cannot shadow a
  newer install there. The probe order lives in `binresolve.py`, and `codex_status`'s "not found"
  detail no longer claims `PATH` is the only place checked. Thanks to @cbeaulieu-gt (#539).

## [0.19.0] - 2026-08-20

A sandbox-hardening and egress-disclosure release.

Three `workspace-write` guarantees that were advertised but not enforced now hold at every
isolation level: network egress and the writable-root set are pinned on the command line, where
the user's own `$CODEX_HOME/config.toml` can no longer widen them, and `codex exec
--strict-config` turns a pin codex silently ignores into a zero-spend startup failure instead of
a guarantee that quietly stopped holding.

The egress disclosures were corrected in the unsafe direction they were wrong in. Codex's reads
are not bounded by the workspace; `AGENTS.md` is auto-loaded from the repository root and from
`$CODEX_HOME` as well as from the resolved workspace; a discovered skill's *body* reaches OpenAI
through a read the model itself issues after selecting it on its description alone; a dry-run
preview does not bound what the paid call sends; and the propose tier's throwaway worktree does
not bound where Codex writes, because `workspace-write` grants the OS temp roots by default.
Every one of these was probed live on `codex-cli 0.148.0` with negative controls; none is a
behavior change, and each corrects a claim that never held.

Foreign text is no longer echoed raw. A rejected config key, codex's stderr, an unknown argument
name, a job id, and the `codex --version` string are now sanitized, bounded, or rejected before
they reach a result envelope, so a control character can neither spoof a terminal nor wedge
itself into a secret to defeat the redactor.

The tracked Codex version moves to `0.148`.

Three breaking changes: an unknown key in your own Codex config now fails pin-carrying runs; the
propose-tier surface retracts its "writes only inside a throwaway worktree" promise; and five
parameters reject control characters in their values. The agent-visible surface changed nine
times (result `fingerprint` `codex-in-claude/0.1/schema-75` → `schema-84`, and the persisted
`RESULT_FORMAT` `8` → `10`), so pre-1.0 this is a minor release; clients that cache by
`fingerprint` re-fetch the contract, and a reader older than `RESULT_FORMAT` `10` rejects newly
stored job records.

### Changed

- **BREAKING: `codex exec --strict-config` guards the guarantee-bearing `-c` key pins, and a new
  `user_config_rejected` error code reports an unknown key in your own Codex config.** codex
  silently tolerates an unknown config key, so an upstream rename of
  `sandbox_workspace_write.network_access` (#518), `sandbox_workspace_write.writable_roots`
  (#520), or `model_reasoning_effort` (#309) would leave the plugin sending a key codex no longer
  reads — reopening the guarantee with no signal. The flag turns that into a zero-spend startup
  failure, since config parsing precedes auth and any model call. It is emitted **only on runs
  that carry a `-c` override** — every `workspace-write` run, every effort-carrying run, and any
  run with an operator `-c` in `CODEX_IN_CLAUDE_EXTRA_ARGS` — because at the default `inherit`
  isolation the flag also hard-fails on an unknown key anywhere in the user's own config,
  including tables for profiles the run never selects; a plain read-only consult carries no pin,
  so sending it there would risk availability while guarding nothing.

  Breaking because a previously accepted operator environment — a junk or version-skewed key in
  `$CODEX_HOME/config.toml` — now hard-fails pin-carrying runs. Ownership keys on the rejected
  KEY, never on the shared `-c` descriptor codex's own message contains: a rejected plugin pin is
  `cli_contract_changed` (the fail-loud drift signal this exists to produce), an operator key or
  an operator `--profile`-selected file is `extra_args_rejected`, and any other config file is
  the new `user_config_rejected` — permanent, repairing with `correct_config`, naming the file
  and line to fix and offering `isolation="ignore-config"` only as an explicitly lossy fallback,
  since it drops your entire config. Neither `codex_status` nor a dry run parses Codex config, so
  neither can predict this failure. Strict validates key **names** only, so a bad
  `reasoning_effort` VALUE still takes the backend `invalid_reasoning_effort` path unchanged.
  (#524)

- **BREAKING: the propose-tier surface no longer promises "writes only inside a throwaway
  worktree", and the delegate tools now advertise `destructiveHint: true`.** codex's
  `workspace-write` sandbox grants the OS temp roots (`/tmp` and `$TMPDIR`) by default
  (`exclude_slash_tmp`/`exclude_tmpdir_env_var` default false), verified live at this plugin's
  exact argv, so the exclusivity claim never held. The `exclude_*` keys are deliberately **not**
  pinned closed — build tools, `uv`, `git`, and test runners need `$TMPDIR` — so every carrier
  discloses the grant instead: a canonical `cli_contract.WORKSPACE_WRITE_SCOPE_FACT` is carried
  verbatim by both delegate descriptions, their `codex_capabilities` `returns` entries, and a new
  `negative_scope` item, each alongside the persistence clause (temp-root writes are neither
  captured in the returned diff nor cleaned up); `Meta.tier` and `Meta.sandbox` carry a short
  form. The annotation preset splits: MCP defines `destructiveHint: false` as "performs only
  additive updates", and a delegated task can overwrite or delete pre-existing files under the
  temp roots, so `codex_delegate`/`codex_delegate_async` flip to `true` while consult and review
  keep `false` — the split is stated on `annotations_reading`.

  Breaking because the retired carriers made an exclusive promise ("writes **only** inside", "the
  worktree **bounds** what Codex may WRITE"), and correcting them weakens a documented guarantee
  a client could have relied on — even though runtime behavior is unchanged and the guarantee
  never held. What remains true and promised: the plugin never applies anything to your working
  tree, and the returned diff is gathered from the worktree only. (#523)

- **The egress disclosures no longer scope Codex's reads to the workspace or the repo.** Several
  descriptions said Codex reads "files ... from its resolved working dir", "other **repo** files",
  or "tracked files in the throwaway worktree". A reader takes that as a bound; it is not one.
  `--sandbox read-only` bounds **writes**: probed on `codex-cli 0.148.0` under this plugin's own
  flags with the workspace a bare non-repo directory, the model shelled out and read a file in
  `$HOME` on **both** tiers, while a write attempt in the same read-only run was refused — the
  negative control that makes the read informative. A canonical `cli_contract.READ_SCOPE_FACT`
  now states the truth at every carrier: the server instructions block, the `codex_status`
  caveat, `codex_capabilities`' `negative_scope`, all six active tools' `returns` and docstrings,
  `readonly_honesty_statement`, the `extra_context` parameter contract, and `workspace_root`'s
  own description, which now says it selects where Codex works, not what it can read. It is a
  **ceiling, not a warrant** — the probe shows reads far outside the workspace, not that every
  file the OS user can read is reachable, since a platform sandbox can still deny individual
  paths. Not breaking: no carrier ever promised reads were confined (every one said "may read"),
  so this widens a disclosure. The practical consequence for callers: narrowing `workspace_root`
  is no longer a mitigation for egress.

- **The egress disclosure names every `AGENTS.md` Codex auto-loads, not just the workspace's own.**
  Two further sources reach the model, both previously understated: inside a repository the load
  walks up from the resolved workspace to the repository root, so a caller who narrows
  `workspace_root` to a subdirectory precisely in order to bound egress still ships the repo-root
  `AGENTS.md` — a file it never named (#472); and a user-global `$CODEX_HOME/AGENTS.override.md`,
  else `$CODEX_HOME/AGENTS.md`, loads on every call from any workspace — the `AGENTS.md` twin of
  the `$CODEX_HOME/skills/` hole (#358). Neither `--ignore-user-config` nor
  `-c project_doc_max_bytes=0` suppresses the user-global file, though the latter does suppress
  the workspace and ancestor files. Outside a repository there is no walk at all.
  `cli_contract.SKILLS_DISCOVERY_FACT` and every carrier state all three sources. Not breaking.

- **Every disclosure now states HOW a skill's body reaches OpenAI, not just that skills are
  discovered.** The two halves of Codex's implicit context arrive differently: `AGENTS.md`
  content is auto-**loaded**, already in context before the turn, while a skill is
  auto-**discovered** as name and description only, and its **body** follows a read the model
  itself issues once it selects that skill (#480). The wire and the prose now agree on this, in
  one canonical sentence (`cli_contract.SKILL_BODY_FACT`) across the server instructions block,
  the `codex_status` caveat, `negative_scope`, all six active tools' `returns` and descriptions,
  `SECURITY.md`, `README.md`, `COMPATIBILITY.md`, the `collaborating-with-codex` skill and its
  fallback reference, and the upgrade docs. Four wire carriers gained disclosure rather than
  wording: `codex_consult_async` and `codex_review_changes_async` named the skills roots with no
  mechanism at all, as did both delegate `returns`. The warnings are **stronger**, not weaker —
  each site now says the metadata alone is enough for the model to select a skill, and that
  selecting it pulls the body to OpenAI even when the prompt names neither the skill nor the
  file. Not breaking. (#498, #501)

- **The dry-run previews no longer imply they bound what a paid call can send.** `codex_dry_run`
  led with "Preview what a `codex_review_changes` call would send", so a clean preview read as
  "nothing sensitive will be sent". It cannot establish that: a dry run never invokes Codex, so
  the model's own reads have not happened yet and none can be listed, and those reads are not
  bounded by the workspace. A canonical `cli_contract.PREVIEW_SCOPE_FACT` states the limit at
  both dry-run descriptions, their two `codex_capabilities` entries, a new `negative_scope` item,
  and the server instructions block. It is deliberately **scoped to the model-initiated channel**
  — a preview genuinely does bound the half this plugin assembles, enforcing `max_input_bytes`
  and reporting truncation — so an unscoped claim would have retracted a true guarantee. Both
  dry-run descriptions also now carry `READ_SCOPE_FACT`, which they never had. Not breaking: no
  carrier promised a preview was exhaustive, and the sibling redaction clause already disclaimed
  exactly this reading.

- **Tracked Codex version is now `0.148`.** `SUPPORTED_VERSIONS` tracks `(0, 148)`; a `0.147` CLI
  now warns as untracked (advisory only — it never blocks). `docs/UPGRADING-CODEX.md` was run end
  to end against `codex-cli 0.148.0` and A/B'd against a side-by-side `0.147.0`. **No contract
  drift:** all 11 `ALWAYS_SEND_FLAGS`, the `--model` help-gated flag, and the three `--sandbox`
  values are present; the guarantee semantics were re-probed live (`read-only` blocked a write,
  `workspace-write` allowed a workspace write while blocking network egress,
  `--output-last-message` received the final message, `--output-schema` output conformed); and
  the three `CONTRACT_DRIFT_STDERR_PATTERNS` still match verbatim. Alongside it:

  - `KNOWN_MODEL_SLUGS` drops `gpt-5.6-sol-wm`, refreshed from the `0.148.0`-written
    `models_cache.json`. What was observed is only that the slug is absent from the
    backend-served catalog cached by `0.148.0`; no contemporaneous `0.147` observation was taken,
    so this record does **not** establish that the removal is independent of the new client. The
    reasoning-effort discovery fields still hold their pinned shape.
  - New `0.148.0` surface is deliberately not adopted: the `migrate-rollouts` and `codex exec
    fork` subcommands and ten new feature flags, none of which this plugin sends or reads. `codex
    exec` gained no new flag. `remote_plugin` is still `stable`/default-on, so the unconditional
    `--disable remote_plugin` isolation stays load-bearing.
  - The `0.147.0` → `0.148.0` app-server schema diff leaves every consumed schema byte-identical
    after canonicalization; three unconsumed v2 messages were added and none removed.
  - The implicit-context probe was re-run against both binaries and the presence matrices are
    identical to each other and to the `0.147` record: a `$CODEX_HOME/skills/` skill is
    discovered despite `--ignore-user-config`, `.claude/skills/` is not, and a parent `AGENTS.md`
    above the git root is not loaded.

- **The posture on Codex's default-on `view_image` feature is recorded: left enabled,
  deliberately** (#479). It is a **model-invoked tool** taking a filesystem path, not implicit
  context — a zero-spend `codex debug prompt-input` A/B with a positive control found no image
  auto-attached and no difference under `--disable view_image`. Disabling it would buy no
  containment, since the read-only tiers already give the model a shell over the same filesystem;
  it would only stop the model interpreting pixels, and unlike `remote_plugin` it opens no
  channel outside the sandbox. The assumption the decision rests on — that the native handler
  enforces the same read boundary as shell execution — is verified against the `rust-v0.148.0`
  handler (#507). The published disclosures stay modality-neutral, so nothing agent-visible
  changes.

- **Documentation and internal conventions.** Safety-critical rules in the egress-caveat docs are
  no longer buried in narration — `README.md`'s Safety bullet is split with "do not target a
  workspace containing secrets you cannot disclose" promoted to a bolded lead, `SECURITY.md`'s
  compound bullet becomes one facts bullet plus an imperative per obligation, and the fallback
  reference's abort rule joins the rule list it belongs to; placement only, no warning weakens.
  The `COMPATIBILITY.md` implicit-context probe was rebuilt to **prove** the model did not read
  the markers itself rather than asking it to say so: it forbids shell commands and file reads,
  keeps every codeword and synthetic skill name out of the prompt, and is captured with `codex
  exec --json` so the operator can assert over the run's own event stream, with a failure treated
  as inconclusive rather than negative. A new **unprompted-selection** consult is what actually
  establishes the egress claim: with a global skill whose description matches an ordinary task, a
  prompt naming neither the skill nor the file nor the codeword still brought the body back.
  `docs/UPGRADING-CODEX.md`'s two-binary A/B loop uses the same instrument, so following the
  upgrade procedure can no longer reproduce the defect this fixed. `AGENTS.md` gains a **Package
  boundary** section — the single home for deciding whether a change belongs in this bridge or in
  upstream `pontonier` — and `CONTRIBUTING.md`'s partial restatement becomes a link to it.
  `COMPATIBILITY.md` no longer repeats the `recommended_plugins` paragraph, and a repo-wide guard
  now scans every Markdown file for a verbatim-repeated prose paragraph, since every existing doc
  guard is presence-based and a presence check passes at one occurrence and at ten.

### Fixed

- **The `workspace-write` guarantees can no longer be widened through the config-file channel.**
  At the default `inherit` isolation the plugin sends no `--ignore-user-config`, so codex reads
  the user's `$CODEX_HOME/config.toml` — where two settings that are perfectly reasonable for
  someone's own interactive codex use silently widened the plugin's advertised bounds.
  `[sandbox_workspace_write] network_access = true` re-granted the delegate tiers **full network
  egress** (#518), and `writable_roots = [...]` let a delegate **write outside its throwaway
  worktree** (#520). The `-c` denylist refused `sandbox_*` keys on the argv passthrough channel,
  but nothing inspected the config file. Both were verified live on `codex-cli 0.148.0` under the
  plugin's exact default flag set, with positive controls (`curl` reached example.com; writes
  landed at pre-absent targets outside the worktree). Every `workspace-write` run now pins
  `-c sandbox_workspace_write.network_access=false` and
  `-c sandbox_workspace_write.writable_roots=[]`, both verified to outrank the config file **and**
  an operator `--profile`, so the advertised promises hold at every isolation level and the
  `--profile` operator-trust carve-out is closed for these two keys. codex's own implicit grants
  (the worktree itself, and the temp roots disclosed above) are unaffected, so default-config runs
  behave as before. No fingerprint change — the pins alter only the built argv, and the
  agent-visible text already asserted the guarantees this makes true. `COMPATIBILITY.md`
  discloses both pins, and `docs/UPGRADING-CODEX.md` gains the semantic probes that guard them
  against silent upstream key drift, since codex ignores an unknown `-c` key rather than
  rejecting it.

- **BREAKING: foreign text is sanitized, bounded, or rejected before it reaches a result
  envelope.** Every path that quoted foreign text into an envelope echoed it raw: codex's stderr
  on the generic `nonzero_exit` branch, the `codex_transfer` diagnostics including
  `app_server_stderr_tail`, the review path's raw-output preview, stored job-result fragments,
  exception text, config keys and profile names, the `CODEX_IN_CLAUDE_EXTRA_ARGS` descriptors and
  refusal messages, the catalog strings `codex_models` reads from `$CODEX_HOME`, and the
  `codex --version` string (unbounded — a 5010-character version came back at full length). Two
  consequences: an escape sequence reaching a terminal can recolor, reposition, or erase — enough
  to spoof or hide surrounding output, and the text is attacker-influenceable, since a repository
  under review can make codex print chosen text — and a control character wedged into a secret
  defeats the redactor's patterns outright, so the value rides out as plaintext.

  The remedy splits by **who can fix the value**, because deleting control characters is the
  wrong answer for a machine field: it *repairs* a malformed value instead of letting it degrade.
  A `verdict` of `pa\x07ss` is not a valid verdict and must become `unknown`, but stripping turns
  it into an affirmative `pass` at `high` confidence; a `job_id` of `abc<BEL><ESC>[31mdef` came
  back as ``No job 'abc[31mdef'``, naming an id the caller never sent.

  - **Rendered prose is sanitized.** `summary`, `questions`, `assumptions`, `next_steps`, a
    finding's `title`/`evidence`/`risk`/`recommendation`, `error.message`, `repair.alternative`,
    and `app_server_stderr_tail` — on consult, review, and delegate alike, on fresh and replayed
    results. Single-token spans (a config key, a path, a rejected flag name) are bounded and
    single-line; multi-line diagnostics keep line breaks wherever that is provably as safe, since
    a rolling stderr tail exists to be read. Truncation is now **marked** (`…[truncated]`,
    reserved inside the budget) for every echoed span: a clipped config key that reads as a
    complete one sends the caller after a key they never wrote.
  - **Caller-correctable inputs are rejected** at the MCP boundary, at zero spend, reporting the
    static parameter NAME and never the value: `job_id`, `base`, `commit`, `model`, and
    `transcript_path` advertise `config.CONTROL_CHAR_FREE_PATTERN` in their own inputSchema,
    following `reasoning_effort`, which already worked this way. **This is the breaking half** —
    five parameters narrow their accepted value set.
  - **Identity-bearing values are preserved byte-exact**: `cwd`, `candidate_roots`,
    `Finding.file`, `session_id`, `source_path`, `verdict`, `severity`, and the `--version` string
    as `codex.codex_version()` returns it to `config.parse_version`. POSIX permits a control
    character in a filename, so such a value may be entirely correct, and a
    `repair.arguments.workspace_root` naming such a repository still round-trips. Sanitizing at
    capture would repair identity text — deleting the control character out of `0.<BEL>148.0`
    yields a plausible `codex-cli 0.148.0`, turning a version that does not parse into one that
    does — so `codex_version` and `version_warning` can legitimately disagree, and
    `codex_version`'s description now says so. `version_warning` is now **static**: it never
    quoted anything `codex_version` did not already carry, so interpolating it only copied
    untrusted text into a second sink.
  - **An unknown argument NAME is withheld**, the one site no schema can cover, since it is
    rejected precisely because it is unknown: `details.field` and `invalid_arguments[].field`
    carry a fixed marker plus a new `field_withheld: true`. The flag is the machine
    discriminator — the marker is not a reserved word, so a caller may genuinely send an argument
    named `<withheld>`. Detection runs on the raw location components, before the length bound
    truncates, so a control character past the bound cannot be cut away and the remainder
    reported clean.

  Two carriers no client sees as prose are deliberately left alone: `raw_response`, `diff`, and
  `meta`. `codex_models`' `slug` is left alone too — it is the identifier, and it is
  pattern-validated. `error.resource_uri` needed nothing: `ReadResourceRequestParams.uri` is a
  pydantic `AnyUrl`, which percent-encodes any raw `Cc` before the field is populated.

  The persisted `RESULT_FORMAT` moves to `10`: `InvalidArgument` and `ErrorDetail` are closed
  schemas (`extra="forbid"`), so an older reader would reject a stored envelope carrying
  `field_withheld`. The text-sanitizing mechanism is upstream (`pontonier` 0.6.0), since it is
  CLI-agnostic; which spans are echoed, each one's bound and truncation direction, and which
  variant a span gets are bridge policy and stay here. One related leak is fixed at the source
  rather than the sink: a control character in a printed worktree path defeated the alias
  matching that suppresses dead absolute paths, so the path survived — a second, independent leak
  of the #420 guarantee that no amount of stripping the OUTPUT could fix. The
  argument-validation LOG still echoes a rejected value, tracked as #532. (#528, #529, #531)

## [0.18.0] - 2026-08-18

### Added

- `cli_contract.PONTONIER_CONTRACT` — the declarative CLI contract in the shared
  `BackendContract` shape, derivation-pinned against the legacy constants.
- Surface-honesty gates via `pontonier.testing`: `FORBIDDEN_SURFACE_PHRASES`
  (cross-bridge contamination canaries and refused-mechanism claims) enforced
  against the built manifest.
- `backend.CodexBackend` — this bridge's adapter on the frozen pontonier
  `AgentBackend` protocol (`contract_api_version = 1`), validated by an argv
  differential against the production command builder.

### Changed

- Every model-bearing run now goes through the pontonier `AgentBackend` adapter:
  `codex.run_codex_exec` stages via `CodexBackend.prepare()` (temp artifacts,
  argv from the shared builder, prompt over stdin, help-gate drops surfaced on
  `PreparedRun.dropped_flags`) and keeps only the execution step — timeouts,
  output byte caps, and event streaming stay this bridge's. The consult-only
  `--skip-git-repo-check` moved from an inline call-site flag to backend policy
  derived from the canonical `kind`, pinned by the argv differential test (which
  now compares against true production argv — it previously validated a variant
  without the flag). Wire snapshots are byte-identical; argv is unchanged for
  every tier.

- Generic core machinery (`jobs`, `worktree`, `gitdiff`, `redaction`, `runtime`,
  `gitproc`, `streamcap`, `idempotency`, `workspace`, `jsoncache`) now comes from
  the shared [pontonier](https://github.com/briandconnelly/pontonier) library
  instead of the vendored `_core/` package. This bridge's worktree knobs
  (`cic-worktree-` prefix, `codex-in-claude@local` baseline identity) are pinned
  in `config.WORKTREE_CONFIG`, so git-visible behavior is unchanged; all wire
  snapshots are byte-identical.

- **Docs and code comments retire the deleted `_core/` package.** `CONTRIBUTING.md`'s one-way
  import rule named a directory that no longer exists, and `SECURITY.md` / `COMPATIBILITY.md`
  cited `_core/redaction.py` / `_core/workspace.py` as the authority for security-relevant
  behavior; all now point at `pontonier.core`. Eight source and test comments were updated the
  same way, and `tests/test_backend.py` no longer calls the protocol "provisional" while every
  other doc calls it frozen. No agent-visible surface changed — the built manifest is
  byte-identical, so no `FINGERPRINT` bump.
- **Runtime dependency bumps.** `fastmcp` 3.4.5 → 3.4.7. The built manifest is byte-identical
  under the new version, so the agent-visible surface is unmoved and no `FINGERPRINT` bump follows.
- **Tracked Codex version is now `0.147`.** `SUPPORTED_VERSIONS` tracks `(0, 147)`; a `0.146` CLI
  still runs and only draws the advisory `codex_status` warning. The `docs/UPGRADING-CODEX.md`
  procedure was run end to end against `codex-cli 0.147.0`, A/B'd against a side-by-side
  `0.146.0` from npm (the retrieved binary authenticated clean against the committed `0.146.0`
  help snapshots, and an npm-vs-Homebrew diff of the new version retired the cross-channel
  concern). Nothing this plugin sends or reads changed: all 11 `ALWAYS_SEND_FLAGS`, `--model`, and
  the three sandbox values are present, and the guarantee semantics were re-probed live —
  `read-only` blocked a write, `workspace-write` allowed the workspace write and blocked network
  egress, `--output-last-message` received the final message, and an `--output-schema` run
  validated against `FINDINGS_OUTPUT_SCHEMA`. The `model_reasoning_effort` config key is still
  applied (the backend rejection carried both `[ReasoningEffortParam]` and `[reasoning.effort]`),
  and `codex exec` still has no dedicated effort flag. Contract-drift stderr signatures still
  match. `docs/codex-help/0.147.0/` carries fresh snapshots and the live `integration` suite
  passes against the new binary.
- **`KNOWN_MODEL_SLUGS` gains `gpt-5.6-sol-wm`.** The bundled advisory fallback is refreshed from
  the `0.147.0`-written `models_cache.json`. The catalog is backend-served rather than shipped in
  the binary, so this slug had already appeared under `0.146.0` — it is catalog drift the upgrade's
  slug diff caught, not a `0.147` change. The reasoning-effort discovery fields still hold their
  pinned shape.
- **`--approve-for-me` (new in `0.147.0`) is deliberately not adopted**, and `cli_contract.py` now
  records why alongside the other never-sent flags: it routes approval requests through an
  automatic review under the `workspace-write` sandbox, which would let a read-only-tier run
  acquire write capability without the caller electing a write tier.
- App-server: the `0.146.0` → `0.147.0` generated-schema diff is additive only on the consumed
  surface — an optional `extensions` map on `InitializeParams` (not sent), an optional `title` on
  the import progress/completed per-item results (read tolerantly, ignored), and two new
  `PlanType` values, `self_serve_business_prolite` and `enterprise_cbp_automation`, already read as
  a free-form capped string. Ten unconsumed v2 `ThreadSection*` messages were added; none removed.

### Fixed

- Multi-line private-key blocks (PEM/PKCS8/OpenSSH/PGP) in gathered diffs and
  returned prose are now redacted statefully (via pontonier): the BEGIN/END
  markers stay visible, every body line between them is replaced with a
  `[redacted: secret value]` marker, and an unterminated block fails closed. Previously only the
  BEGIN marker was masked while the entire base64 body was sent.
- **Bare provider tokens are now redacted.** The shared redactor adds patterns for `github_pat_`,
  `glpat-`, `sk-ant-`, `npm_`, and `pypi-` tokens. The vendored redactor already caught these in a
  labelled assignment (`token = "…"`), because its value-pattern matcher keys on the label; what
  changed is the *unlabelled* case — a bare token in prose, or a diff line with no `key =` in front
  of it, which that redactor passed through verbatim. Measured old-vs-new over both spellings: five
  improvements, and no payload where the new redactor leaks something the old one caught.
- Redaction preserves the diff's trailing newline, so delegate diffs are
  `git apply`-able again (ports moonbridge's fix).
- **`COMPATIBILITY.md` corrects the 2026-08-02 "parent `AGENTS.md` above the git root is loaded"
  observation.** That mechanism does not reproduce on `0.146.0` or `0.147.0`: the parent codeword
  was absent from both binaries with a project `AGENTS.md` present, with it removed, and with
  `--cd` set to a repository subdirectory. The corrected behavior is that codex walks *upward* from
  the resolved workspace directory and stops at the **git root**. What is retracted is the
  mechanism, not the concern — because `resolve_workspace` returns an explicit `workspace_root`
  unchanged, the resolved workspace can be a *subdirectory*, and the walk then crosses above it
  (from `repo/sub`, codex loaded `repo/AGENTS.md`). The published "the resolved workspace's
  `AGENTS.md`" caveat therefore still understates egress and issue #472's conclusion stands.
  Correcting that published wording is a `FINGERPRINT`-bumping change tracked in #472, so this
  entry changes no agent-visible text and carries no bump.
- **`config.py`'s extra-args comment no longer states a plugin-side narrowing as a codex fact.**
  It claimed an attached `-cKEY=VAL` is "rejected"; codex accepts it (clap attached short-option
  value) on both `0.146.0` and `0.147.0`. It is *this parser* that refuses it, because the
  attached-form split fires only on long `--flag=value`. Behavior is unchanged and
  safe-direction — the plugin passes through strictly less than codex would take.
- **Shared-core diagnostics stay inside the server's log configuration.** Moving the core out of
  `codex_in_claude._core` made its loggers siblings of the server namespace rather than children,
  so `pontonier.core.*` records inherited none of the configured handlers and propagated to the
  stdlib root logger instead — the exact escape `propagate = False` exists to prevent, since an
  embedding host may have wired root to stdout (the stdio JSON-RPC channel). `obs.configure()` now
  configures the `pontonier` namespace alongside `codex_in_claude`, so library diagnostics honor
  `CODEX_IN_CLAUDE_LOG_LEVEL`/`CODEX_IN_CLAUDE_LOG_FILE` and reach the same stderr and file
  handlers.

## [0.17.0] - 2026-08-02

A secret-redaction release. The inline redactor was rebuilt around merged candidate spans so no
matcher can strand part of a secret another one covers whole; its labelled-secret pattern now
reaches quoted-JSON and bracket-subscripted keys without scrubbing ordinary source out of reviewed
diffs; two quadratic patterns were bounded; and a replacement that may be incomplete now says so
instead of advertising a completeness it doesn't have. Alongside that, `codex_review_changes` and
`codex_dry_run` disclose *how* a diff was redacted — files withheld whole versus files sent with
values masked inline — `codex_capabilities` declares the targeted MCP protocol revision and its
`readOnlyHint` reading, and the dry-run tools warn when the call they preview may exceed the
synchronous deadline. The tracked Codex version moves to `0.146`. One breaking change: a blank
`question` or `task` is now rejected before any spend. The agent-visible surface changed nine times
(result `fingerprint`
`codex-in-claude/0.1/schema-66` → `schema-75`, and the persisted `RESULT_FORMAT` `7` → `8`), so
pre-1.0 this is a minor release; clients that cache by `fingerprint` re-fetch the contract.

### Added

- **`coverage.redaction` distinguishes withheld files from inline masks** on
  `codex_review_changes` and `codex_dry_run` (#433). The `redacted` omission reason previously
  conflated two different things a secret-looking diff can trigger: a whole file dropped (its path
  itself looked secret-bearing, e.g. `.env`) versus a file sent with one or more values masked
  inline. The new optional `RedactionSummary` — `withheld_paths`, `masked_paths`, and
  `inline_masks` (markers actually emitted, after span merging, not raw pattern candidates) —
  splits them, counting only content that lands in the retained, byte-capped diff text.
  `meta.redacted_paths` and `DryRunResult.redacted_paths`/`redacted_paths_count` are unchanged:
  still the single backward-compatible union of both path lists, and still `codex_delegate`'s only
  redaction disclosure. The field is optional (so a `Coverage` persisted before it existed still
  replays) and the `redacted` reason can fire without it — a legacy-shaped producer, or a
  disclosure dropped entirely by byte-cap truncation — but the field is never populated without
  the reason. `FINGERPRINT` `schema-74` → `schema-75`; `RESULT_FORMAT` `7` → `8` for the persisted
  `Coverage`. Additive, not breaking.
- **`codex_capabilities` declares the targeted MCP protocol revision** via a new
  `protocol_revision` field (`"2025-11-25"`), previously derivable only by inspecting the
  `initialize` wire response (#423). Its description points at the new
  `docs/adr/0004-mcp-2026-07-28-migration.md`, which records why this server stays on 2025-11-25
  (verified FastMCP/`mcp` SDK support, not any spec-granted support window) and the per-feature
  migration plan for the deprecated pieces already in use. `FINGERPRINT` `schema-70` →
  `schema-71`; additive, not breaking.
- **`codex_capabilities` documents the server's `readOnlyHint` reading** via a new
  `annotations_reading` field, previously stated only in source comments (#426). The hint tracks
  whether a call changes observable state that outlives the response (a job record, committed
  spend) rather than file I/O — which is why `codex_consult`, `codex_review_changes`, and
  `codex_delegate` are `readOnlyHint: false` even though consult and review never write files,
  while the two dry-run tools, which create no job record, stay `true`. Consistency guards assert
  the claim against the live tool annotations. `FINGERPRINT` `schema-71` → `schema-72`; additive,
  not breaking.
- **`codex_dry_run` and `codex_delegate_dry_run` advise when the previewed call may exceed the
  synchronous deadline** via a new `deadline_advisory` field, described identically on both
  (#342). Non-null exactly when the previewed call would run the model *and* either its prompt
  exceeds 100,000 bytes or its resolved `reasoning_effort` is `high`/`xhigh` (an exact match — a
  near-miss like `"Xhigh"` is deliberately not treated as high); null whenever the call would run
  no model at all. It is a hint, not a refusal, and it names the previewed **paid** tool's own
  `_async` counterpart, never the dry-run tool's own name, which has none. `FINGERPRINT`
  `schema-73` → `schema-74`; additive, not breaking.

### Changed

- **Tracked Codex version is now `0.146`.** `SUPPORTED_VERSIONS` tracks `(0, 146)`; a `0.145` CLI
  still runs and now warns as untracked (the gate is advisory and never blocks). The upgrade
  procedure was run end to end against `codex-cli 0.146.0`, A/B'd against a side-by-side
  `0.145.0`, and required no code change: help text for `codex`/`exec`/`review`/`exec review` is
  byte-identical, every guarantee-bearing flag and sandbox value is present, the drift signatures
  still match observed output, and the model catalog is unchanged. Semantics were re-probed rather
  than assumed — `read-only` still blocks writes, `workspace-write` still writes in-workspace while
  blocking network egress, `--output-last-message` still delivers, `-c model_reasoning_effort` is
  still applied, and structured output still conforms. Upstream additions were reviewed and none is
  consumed: a new `app-server --help` flag, two new app-server messages, an optional `providerId`
  and an `ent26` `PlanType` value on the consumed schemas (the plan type is read as a free-form
  capped string, not an enum), and four new feature flags — including `mcp_2026_07_28`, still
  under development, which corroborates this release's decision to target MCP 2025-11-25.
  `remote_plugin` remains default-on, so the `--disable remote_plugin` isolation guarantee still
  applies and still fails closed.
- **The skills-discovery egress caveat now reads identically everywhere it appears** (#427).
  Fifteen prose sites — the server instructions, the `codex_status` caveat, six
  `ToolCapability.returns` clauses, `codex_capabilities`' `negative_scope`, and six tool
  docstrings — stated the same fact (Codex auto-loads the resolved workspace's `AGENTS.md` and
  discovers skills in that workspace's `.agents/skills/` and in the user-global
  `$CODEX_HOME/skills/`, and the plugin's isolation flags do not suppress any of it) in
  independently drifted wording. All now share one canonical sentence pair defined in
  `cli_contract.py`, already the single source of truth for every Codex CLI assumption. This is a
  wording convergence, not a byte-identical refactor — no carrier shared one sentence before — and
  no guarantee is removed or weakened; the three `_async` docstrings keep their deliberately
  lighter subset. `tools/list` grew 157 bytes, inside its existing budget. `FINGERPRINT`
  `schema-72` → `schema-73`; not breaking.
- **`Meta.timeout_seconds` is now described** — it was the only semantically-loaded `Meta` member
  published without a description (#413). It states which deadline the value is, by envelope: a
  synchronous paid call reports that call's own resolved deadline, post-clamp (10–600s); a
  background job's start handle, or a later `codex_job_result` fetch of that job's originating
  run, reports the job-lifecycle ceiling instead (`job_max_seconds`, default 1800, clamped
  60–7200); and `invalid_arguments` or `internal_error`, tied to no run, report the server's
  configured sync deadline. Wording only: `FINGERPRINT` `schema-67` → `schema-68`,
  `RESULT_FORMAT` unmoved, not breaking.
- **The three sync tools' Progress & recovery paragraphs now describe job-record recovery
  correctly** (#414). They named only `codex_job_list` → `codex_job_result`, omitting
  `codex_job_status` as the polling step in between, and framed recovery as a fallback for a
  dropped connection — when some MCP clients background a long tool call well before the server's
  own deadline. Wording only: `FINGERPRINT` `schema-68` → `schema-69`, not breaking.

### Fixed


- **BREAKING (agent surface): a blank `question` or `task` is now rejected before any spend**
  (#411). An empty or whitespace-only value passed validation and bought a real Codex run that
  could produce nothing — the prompt builders strip it, so the model received framing scaffolding
  and no ask (a reported `codex_consult(question="   ")` burned 23,120 tokens and returned
  `ok: true`). `codex_consult`, `codex_consult_async`, `codex_delegate`, `codex_delegate_async`,
  and `codex_delegate_dry_run` now return `invalid_arguments` naming the offending argument, with
  a `correct_arguments` repair. Flagged breaking because it narrows an accepted value set: input
  that previously returned `ok: true` now returns an error. Blankness is Python's `str.strip()`,
  so U+00A0 is blank while U+200B counts as content, and the check sits after the byte-limit
  guard, so an oversize whitespace-only value still reports `input_too_large`. `FINGERPRINT`
  `schema-66` → `schema-67`; `RESULT_FORMAT` unchanged.

- **Redaction no longer strands part of a secret behind a marker that looks complete** (#443,
  #445). The inline patterns ran as sequential `re.sub` passes, each over the previous pass's
  output, and `sub` never revisits consumed text — so a matcher with a narrower value class could
  eat a prefix of a value a later matcher would have covered entirely: `token=ghp_<20 chars>-tail`
  came out as `token=[redacted: secret value]-tail`. The worse harm is not the disclosure but that
  the output advertised itself as fully redacted, suppressing the reaction a bare secret would
  provoke. Every pattern is now run with `finditer` against the **original** line, each candidate
  contributes the span it would replace, the spans are merged on strict overlap, and the line is
  rebuilt with one marker per merged interval — so the order of the pattern list no longer affects
  output at all. Touching (non-overlapping) candidates deliberately stay separate, and a preserved
  prefix (a labelled key, an `Authorization:` header, a connection string's `://user:`) survives
  because it lies outside every merged span rather than because a replacement handed it back. The
  span projection fails closed: a candidate whose group 1 is not a leading, participating prefix
  of its match has its whole match redacted rather than the computed slice. One behavioral delta
  comes with this, in the fail-closed direction — the code-reference exemption is now judged
  against the original line, so a line whose evidence an earlier marker had erased is redacted
  instead of exempted.
- **A redaction marker no longer claims completeness it doesn't have** (#446). A credential
  carrying a character the connection-string userinfo matchers exclude (`/`, or `?`/`#` on the
  arms that stop there) can never be *spanned* by them, so span merging cannot help and an earlier
  matcher firing on its prefix still ships the remainder beside a marker:
  `redis://u:token=[redacted: secret value]%2Fmore/tail@host` looked complete while `/tail` went
  out in the clear. The rebuild site now decides per merged interval whether the replacement may
  be partial and emits `[redacted: possibly partial secret value]` instead. Two checks, either
  sufficient: the character right after the interval is not a safe terminator for that interval's
  trailing candidate type (a narrower set for labelled and Bearer candidates, whose value alphabet
  is not a specific credential's own grammar), or the interval's earliest whole-match candidate is
  preceded by a value-continuation character. Neither is a completeness proof — the plain marker
  means no affirmative sign of truncation was found, not that completeness was verified, which
  this best-effort mechanism cannot do.
- **The labelled-secret pattern now reaches quoted and bracket-subscripted keys** (#432, #434). It
  required its `:`/`=` separator immediately after the label, so JSON's closing quote
  (`"api_key": "…"`) and a subscript's `"]` (`cfg["password"]["key"] = …`) each defeated it
  entirely — silently exempting the carrier this pattern exists for: a credential with no
  recognizable vendor shape (`AWS_SECRET_ACCESS_KEY`, `CI_JOB_TOKEN`, an internal HMAC secret), in
  exactly the carriers that hold those — `.json` config, fixtures, captured API responses, and the
  `raw_response.text` this redactor is applied to. A value carrying a vendor prefix was caught
  anyway by its own pattern, which masked the gap. The label group now accepts an optional closing
  quote and a bracket subscript. Both widenings fail closed by construction: reaching either
  requires consuming a quote, and a match that consumed a key quote is never eligible for the
  code-reference exemption below — pinned by an invariant test, so moving the bracket out of the
  quote group fails CI even if every behavioral test still passes. The accepted cost is the mirror
  of that guarantee: ordinary source assigning to a `key`/`token`-ish subscript is masked out of a
  reviewed diff, including innocent suffixes such as `obj["monkey"]`. Measured rather than
  assumed — an A/B over 3,124 real source files found no line that stopped being redacted and
  three newly redacted, only one of them a genuinely new class.
- **A labelled match no longer swallows a later sensitive label's secret** (#434, #436). The
  pattern's value run is greedy and `re.sub` never revisits consumed text, so an earlier weak
  label's value could absorb a later sensitive label whole and ship the real secret behind it in
  the clear: `cfg "key": aaaa…password = realsecret1234567890` redacted the filler and left the
  secret untouched. Two guards now run in sequence, because no single formulation covered both
  shapes without reopening the quadratic risk below: the original guard for bracketed candidates,
  unbounded at any distance, and a new unconditional one covering non-bracket swallows — a shape
  never protected before, at any distance — bounded at 1024 value characters to stay linear on
  repeated-anchor input. Both share one refinement: a guard refuses only when the inner label's
  own value would itself clear the redaction threshold, so `token = aaaa…key=short` is not turned
  into a total miss. The accepted trade is that a refused candidate's own value run survives on
  the page and the engine advances to redact the inner label instead — a narrower span, not a
  leak. A non-bracket swallow chain whose gap exceeds the peek is exactly as unprotected as it
  always was, tracked as #465.
- **Secret redaction no longer scrubs ordinary source out of reviewed diffs** (#421). The
  labelled-secret pattern matches any 16-or-more-character identifier run after a `key`/`token`-ish
  label, so plain code tripped it: `token = _PLACEHOLDER_PREFIX + _placeholder_seed(text)` reached
  Codex masked. Two harms compounded — the reviewer could not see the code it was asked to review,
  and because any inline mask makes `coverage.status` partial, the never-false-pass rule turned a
  model `pass` into `unknown` citing `redacted`, which reads as "this file had secrets" when one
  innocuous assignment tripped a heuristic. A labelled match inside a diff body line is now left
  intact when it is provably a code reference: the separator must be followed by whitespace (so
  `api_key=value` in config, env, shell, or a query string is never exempt), the label must not be
  from the password family — read across the whole logical label, so `config.password.key = …` is
  judged whole — the value must be an unquoted bare dotted identifier path, and it must be
  followed by a call, an operand, or, only with a `:` separator, an annotation default. The
  exemption applies only within a recognized source extension, because every one of those
  conditions is a claim about *code* syntax: in YAML the identical text is a plain scalar, and
  YAML nests the sensitive label on a *preceding* line, out of reach of any same-line test.
  Redaction is not weakened elsewhere — the exemption never applies to `redact_text`'s arbitrary
  prose, and every vendor/JWT/PEM/connection-string pattern still runs on an exempted line.
- **A connection-string credential is redacted in every userinfo position it can occupy** (#440).
  The matcher required a non-empty username, so `redis://:pass@host` went out verbatim — not an
  edge case but the *canonical* Redis URL, since Redis had no usernames before ACLs in 6.0 — and a
  token stored as the username with an empty password (`https://<token>:@host`, the
  token-as-username idiom) was never redacted either. Both are now matched, the latter at 16+
  characters, the credential threshold this module already uses. The password side stays required:
  an empty password holds no secret, and matching it would emit a marker claiming to have hidden a
  blank value. The bare `://token@host` form is deliberately left alone at any threshold, because
  length cannot establish credential semantics in that position — a 16+ rule masks
  `git+ssh://deployment-automation@git.example.com/repo` and `docker://prometheus-operator@sha256:…`,
  identities rather than secrets, and raising the threshold only changes which identities get
  destroyed.
- **An ordinary URL whose query or fragment carries an `@` is no longer masked as userinfo**
  (#442). Per RFC 3986, `?` and `#` terminate the authority component, so nothing after either can
  be userinfo — but the named-username matcher admitted both, on the password side and on the
  username side: `https://host.example:8443?email=user@example.com` (an email in the query, no
  userinfo anywhere) masked its port and query and partly hid its host, and
  `https://host.example?foo:bar12345678@x.example` (an ordinary query string carrying a colon)
  masked both slots. All three connection-string userinfo runs now derive their terminators from
  one shared exclusion set instead of three independently hand-spelled character classes. The
  accepted trade, pinned by a characterization test on each side: a password or username
  containing a literal `?`/`#` — already invalid userinfo per RFC 3986 without percent-encoding —
  is no longer redacted. An old-versus-new sweep over 1,849,015 lines of third-party source found
  zero behavior changes in either direction, in both code-exemption modes.
- **Redaction is no longer quadratic on long or repeated-anchor text** (#438, #439). Two patterns
  opened with an unbounded greedy run ahead of a literal that never arrives, so every start
  position scanned to the end of the run and then backtracked one character at a time: the
  connection string's scheme run (`[a-zA-Z][\w+.-]*://`) took ~15s on 100 KB of text, and the JWT
  first segment 5.7s on 160 KB of `eyJ`. This is a liveness concern rather than a disclosure —
  `redact_text` runs over untrusted model output, so input size and shape are attacker-influenced,
  and a synchronous call that blows its deadline is terminated with its paid work lost. The
  connection-string scan now simply starts at the `://`, which costs no coverage (the scheme was
  never part of the *replaced* span, so output is byte-identical wherever the old pattern matched)
  and additionally recognizes userinfo whose `://` no letter-led run reaches — closing a real leak
  on 0.16.0, where a preceding marker ate the scheme letters and `key=<eaten>://user:pw@host`
  shipped its password intact. The JWT first segment is bounded at 512 post-anchor characters,
  about 8x a realistic JOSE header; segments 2 and 3 stay unbounded, since capping the first also
  caps how many anchors ever reach them. Both are roughly linear after the fix and pinned by
  timing budgets.
- **`codex_delegate`'s prose and error envelopes no longer point into the deleted worktree**
  (#412, #420). Codex runs with its working directory set to the throwaway worktree, and that
  worktree is torn down before the caller reads the result, so every absolute path Codex wrote was
  dead on arrival — a reported run returned ``Created
  [HELLO_SMOKE_TEST.md](/private/tmp/cic-worktree-8t22rdm3/tree/HELLO_SMOKE_TEST.md)``, an
  invitation to open a file that no longer existed anywhere. Worktree-absolute paths — plus the
  `file://` spelling and the symlinked-ancestor spelling (macOS resolves `/tmp` to `/private/tmp`)
  — are now rewritten to repository-relative form in `summary` and `raw_response.text`, and the
  delegate prompt asks for relative paths so the rewrite is a backstop rather than the only
  mechanism. Paths stay *relative* rather than re-rooted at the live repository on purpose: the
  diff is not applied, so a live absolute path would be equally dead for a new file and would
  point at differing content for an existing one. Error envelopes are covered too:
  `classify_failure`'s `nonzero_exit` branch and the `WorktreeError` messages built from raw git
  argv/stderr now route through `worktree.sanitize_prose`, the one composition that relativizes
  and redacts in a single pass — the two operations reordered are each independently unsafe.
  `diff` was never affected; it already carried correct `a/`/`b/` paths.
- **`invalid_arguments` envelopes are now conformant wherever they are produced** (#416, #418,
  #419). `docs/REFERENCE.md` promises an envelope with this code carries a per-argument list of
  `{field, reason, allowed_values}`, with `details` mirroring the first entry; `codex_transfer`'s
  rejection of an invalid `transcript_path` and the `untracked`-policy rejection reached by a
  direct call both omitted it. The rule now lives in the one constructor every producer goes
  through, so a listless envelope is a loud programming error rather than a silently
  non-conformant result, and `details` is always derived from the first entry. The `gitdiff`
  branch's human-readable `message` also stopped echoing the rejected value: its machine fields
  already honored `InvalidArgument`'s promise that a rejected value — which may be a secret — is
  never copied, while the prose beside them printed it verbatim. And the background worker now
  normalizes a nonconformant `invalid_arguments` envelope to `internal_error` immediately before
  the atomic write, so replay cannot return a never-conformant record stamped with the current
  fingerprint. Replay of records written before these rules is deliberately untouched.
- **`initialize` no longer advertises the `io.modelcontextprotocol/ui` extension (MCP Apps)**
  (#424). FastMCP injects it unconditionally, so a host probing for MCP Apps support found the
  capability advertised with nothing behind it — this server implements no UI/Apps code. The
  existing `get_capabilities` seam now filters that one id, preserves any other entry a future
  FastMCP version might legitimately add, and omits the `extensions` key entirely only when
  nothing is left. Removing a falsely-advertised capability is a correction, not a removal of
  implemented behavior. `FINGERPRINT` `schema-69` → `schema-70`; not breaking.
- **A transfer or rate-limit read's `stderr_tail` no longer races the drain thread that fills it**
  (#449). `_StderrDrain` reads a child's stderr on its own daemon thread and kept no reference to
  it, so on a loaded runner an outcome could be assembled before the last, most diagnostic line
  ever reached the capture — and a quiesce placed in the `finally` could not fix it, since Python
  evaluates a `return` expression before `finally` runs. Every post-spawn exit in both functions
  now runs a shared settle step before constructing its outcome: terminate the child, join the
  stdout reader, give the newly-tracked drain thread a bounded (~2s) quiesce, then snapshot. A
  stream that never reaches EOF still yields a bounded, possibly incomplete tail — that boundary
  is deliberate and unchanged, and is now pinned by tests rather than left implicit.

- **`COMPATIBILITY.md` understated what auto-loads into Codex's context.** Its implicit-context
  table recorded that an `AGENTS.md` *above* the git root is not loaded. It is — and re-probing the
  previous `0.145.0` binary shows the same, so this corrects a wrong earlier observation rather
  than a behavior change, in the unsafe direction. A negative control rules out model
  confabulation: removing the parent `AGENTS.md` makes its codeword disappear from the answer while
  the project one remains. Documentation only — the published egress caveat still says "the
  resolved workspace's `AGENTS.md`" and so remains narrower than observed behavior; widening that
  text carries its own `FINGERPRINT` bump and is tracked separately.

## [0.16.0] - 2026-07-28

A discovery-metadata and envelope-slimming release. Every tool now states its cost, its title, and
its stability tier in `tools/list` itself; `codex_capabilities` gains a `summary` default and a
`contracts` mode, so a client can fetch a schema or recheck a `fingerprint` without re-reading the
whole tool inventory; `codex_job_list` gains `limit`/`status` filters; and each `codex://` resource
carries triage metadata a client can read before spending context on the body. Delivered success
envelopes stop sending explicit null `meta` members, and `meta.roots_source` now reports what the
MCP-roots probe actually saw on every envelope that reports it. Several capability and
documentation contracts that described something other than what was being sent are corrected. The
agent-visible surface changed ten times (result `fingerprint` `codex-in-claude/0.1/schema-56` →
`schema-66`, and the persisted `RESULT_FORMAT` `6` → `7`), so pre-1.0 this is a minor release;
clients that cache by `fingerprint` re-fetch the contract. Every change is backward-compatible — no
tool, field, or error code was removed or retyped.

### Added

- `codex_capabilities` accepts `detail="contracts"`, which omits `tool_details` and returns
  everything else unchanged. The `include_schemas` fallback for resource-blind clients previously
  re-sent the whole tool inventory on every schema fetch; pairing it with `contracts` drops a
  schema fetch from 30,404 to 23,082 bytes, and `detail="contracts"` alone is a 3,787-byte
  `fingerprint` recheck for cache revalidation (was 11,109). `tool_details` is the only field
  removed, and it is already optional in the published schemas, so a `contracts` response still
  validates against both the tool's `outputSchema` and `codex://capabilities-result` — no schema
  change was needed. The new token lives on a separate `CapabilitiesDetail` Literal used only by
  this tool; the shared `Detail` stays two-valued, so the five other tools taking `detail` are
  unaffected. Backward-compatible: `summary` remains the default and both existing modes are
  unchanged.

- Every tool now states its cost in its own description — five tools that previously relied
  on `codex_capabilities` alone (`codex_consult`, `codex_review_changes`, `codex_delegate`,
  `codex_job_result`, `codex_job_consume_result`) now say `PAID` or `Free` explicitly, and two
  more (`codex_dry_run`, `codex_delegate_dry_run`), which already stated `NO model call and no
  spend` in prose, were normalized onto the same `Free` token — so a client reading only
  `tools/list` gets one consistent cost marker across every tool.
- Every cost marker now uses one canonical token — `PAID —` for active tools, `Free — no
  model call` for free ones — instead of near-miss variants (`FREE —`, a line-wrapped
  `Free —`, or bare mentions of "spend"). `codex_transfer`, `codex_capabilities`,
  `codex_consult_async`, `codex_review_changes_async`, and `codex_delegate_async` gain the
  literal marker; the three async `PAID —` blocks each point at the correct preview tool
  (`codex_status` only for `codex_consult_async`, `codex_dry_run` for
  `codex_review_changes_async`, `codex_delegate_dry_run` for `codex_delegate_async`), and all
  six active tools now say "every new call" rather than "every call" so the marker doesn't
  contradict `idempotency_key`'s no-new-spend replay semantics.
- Every tool now carries a `title` for human-facing pickers and a namespaced
  `_meta` stability tier, so a client reading only `tools/list` can see which tools are
  experimental.
- `codex_job_list` gained optional `limit` (1-1000) and `status` filters to narrow the
  returned jobs. Both are purely opt-in: omitting `limit` (the default) still returns every
  job the store retains, so the tool every error's repair hint names as *the* way to recover
  a lost `job_id` never hides a row the server deliberately kept — the job store's own
  retention policy is the only server-side limiter, and a soft one, since running jobs are
  exempt from eviction. Only an explicit
  `limit` can truncate; when more jobs match, the response sets `truncated: true` with a
  `truncation_hint` pointing back at omitting `limit` (or narrowing with `status`) — the
  extra rows are dropped, not paged, so there is no cursor. Running jobs are never evicted,
  so a busy workspace can retain more rows than `limit`'s 1000 ceiling can ask for, which is
  why omitting it is the only complete listing. The tool's `codex_capabilities` record
  advertises both params and the cap-not-a-page semantics, so a client treating the
  `detail="full"` inventory as authoritative sees them too (audit F5, #396, #395).
- `meta.roots_source` (and the matching field on `codex_dry_run`/`codex_delegate_dry_run`)
  reports which of three states the MCP-roots probe saw: `client` (the client advertised the
  roots capability and the probe returned, possibly an empty list), `not_negotiated` (this client
  never advertised the roots capability — pass `workspace_root` instead), or `probe_failed`
  (roots were advertised but the call errored this turn — retrying may help). It reports the
  probe, not where the workspace came from — `workspace_source` answers that. Previously all
  three collapsed into a silent empty list and a fallback to the server's own cwd; `roots` stays
  advisory either way, and `workspace_root` remains the durable path (audit F8). Which run the
  value describes now depends on the envelope, and `codex://result-meta` states the rule: when
  present, a DELIVERED consult/review/delegate result reports the ORIGINATING run (like
  `meta.tier`) whether returned synchronously or fetched later, while a `*_async` handle, a
  dry-run preview, and an error a `codex_job_*` call generates instead of delivering a stored
  result each report the CURRENT call — so a replay handle and the result later fetched for the
  same job may legitimately differ. Absence implies none of that: it means only that the value
  was not reported (an unexpected server-side `internal_error`, for instance, is built without
  one), so never infer a run's age or identity from a missing key. Bumps the persisted result-format (`RESULT_FORMAT` `6` → `7`)
  for the new `Meta` field; not breaking.
- Each `codex://` resource now carries a namespaced `_meta["dev.bconnelly.codex-in-claude/triage"]`
  so an agent can decide whether a body is worth the context before reading it: the five static
  schema resources declare `size_bytes` (computed from the payload at registration, so it cannot
  drift from the body); `codex://models`, whose body is a refreshed cache, declares `volatile:
  true` with a `freshness_via` pointer to the payload's `fetched_at` field instead of a size that
  would go stale (audit F4). `size_bytes` now counts the UTF-8-encoded bytes rather than
  `len()` of the JSON string (Copilot review of #385): today's payloads are pure ASCII under
  `json.dumps`'s default `ensure_ascii=True`, so the count is unchanged for all five resources,
  but the name is now true by construction instead of by coincidence of that default.

### Changed

- Delivered `codex_consult`/`codex_review_changes`/`codex_delegate` success envelopes now omit
  `meta` members whose value is null, instead of sending a run of explicit null keys on every
  call — between 5 and 17 of them across the representative envelopes in
  `tests/fixtures/wire_shape_snapshot.json`, though a real call's count varies with which
  members apply to it.
  A key's absence means exactly what the null meant — not applicable, or not reported for this
  run — so read `meta` with a null-safe accessor rather than by testing key presence. Measured
  against those same representative envelopes: ~6–37% smaller at `detail="summary"`, and the
  same ~6–37% at `full`; the saving scales with how many members the run leaves
  inapplicable, so the sparsest envelope gains most. The six required `meta` members are
  always present and empty arrays stay empty arrays; everything outside `meta` is delivered
  verbatim, so top-level fields (including `codex_delegate`'s `diff`, which is null when a run
  proposes no changes) and all of `raw_response` keep their keys. The `*_async` job handle and
  `codex_job_status` are unaffected. Trimming happens on delivery, so the stored `result.json`
  is byte-for-byte unchanged (`RESULT_FORMAT` stays `7` and already-stored job results stay
  readable) and a replayed result still matches a fresh synchronous one. Not breaking: the
  published output schemas and `codex://result-meta` already accept absence for every affected
  field. Bumps `FINGERPRINT` (`schema-60` → `schema-61`); the rule is published on
  `codex://result-meta` (#334).
- `codex_capabilities` now defaults to `detail="summary"`, returning only the per-tool facts
  `tools/list` does not already carry (`name`, `cost`, `stability`, `error_codes`, and, for the
  `*_async` tools only, `async_lifecycle`). Pass `detail="full"` for the previous payload. The
  `extra_context` parameter contract moved its full text to `codex://params` and
  `idempotency_key`'s inline summary was compressed. The durable size win is `codex_capabilities`'
  own response — 21,763 → 11,109 bytes (−49%) — but that is paid only by clients that call the
  tool, not by every client the way `tools/list` is.
- Net effect on the preloaded discovery surface: this release's other additions (per-tool cost
  markers, titles, stability tiers, `codex_job_list` filters, `roots_source` provenance,
  resource triage metadata) outgrew the compression above for every client, not just
  `codex_capabilities` callers. `tools/list` went from 79,242 to 83,895 bytes (+5.9%). That is a
  deliberate trade: a larger preloaded surface in exchange for cost/stability/next-step metadata
  that was previously missing or unreachable.

### Fixed

- **`codex_capabilities` delivered the per-tool `stability` key in one detail mode but not the
  other** (#399). `detail="summary"` force-added `stability: null` for the eight default-tier tools,
  while `detail="full"` had it stripped by `exclude_none` — so a client moving from the default mode
  to the richer one *lost* a key, contradicting the docstring's framing of `full` as additive. The
  key is now forced once, before either mode branches, so `full` can no longer drop a key
  `summary` carries and a single code path is what puts `stability` on both;
  `detail="contracts"` carries no inventory and is unaffected. Null keeps its published meaning — the tool inherits the server-wide `stability` — and
  the field stays optional in both published schemas, so this only adds key presence:
  `FINGERPRINT` moves (`schema-65` → `schema-66`), `RESULT_FORMAT` does not, and it is not breaking.
  The subset regression no longer carves `stability` out and now also pins that both modes describe
  the same tools and agree on every shared value.

- **The documented contract misdescribed `detail` rejection and `roots_source`** (#397). Three
  corrections, none of which changes behavior. (1) `docs/REFERENCE.md` told direct MCP callers that
  an unrecognized `detail` value is rejected with `unsupported_detail`; it is not reachable that way
  — `detail` is a closed enum in each tool's input schema, so the call boundary rejects the value as
  `invalid_arguments` before the handler runs, and `unsupported_detail` stays deliberately
  unadvertised as an in-handler guard for direct Python callers. A parametrized regression now pins
  the boundary behavior across the five tools sharing the result `Detail` enum, so the corrected
  sentence cannot go stale unnoticed (`codex_capabilities` has its own three-valued
  `CapabilitiesDetail`; pinning that one is #398's item 3). The same published description no longer
  offers an unsupported `detail` as an example of a lifecycle-generated error, since over MCP that
  rejection never reaches the handler. (2) `roots_source` was absent from `REFERENCE.md` entirely,
  including the workspace-selection section that owns workspace resolution; it is now documented
  there with the probe-versus-selection distinction and the correct placement note that the two
  dry-run tools expose it top-level on a *successful* preview, while their error envelopes carry it
  under `meta` like every other tool. (3) The published `codex://result-meta` description
  said `client` means roots "were used", which conflates the roots probe with workspace selection —
  `client` only reports that the probe returned, and it coexists normally with
  `workspace_source: "param"` (an explicit `workspace_root` wins) or `"cwd"` (the probe returned no
  usable root). Wording only, so it bumps `FINGERPRINT` (`schema-64` → `schema-65`) without moving
  `RESULT_FORMAT`; not breaking.

- **`meta.roots_source` never reached a delivered paid success envelope** — the one surface it
  was added for. A synchronous `codex_consult`/`codex_review_changes`/`codex_delegate` success is
  delivered from the worker-written `result.json`, not from the `meta` the handler prepared, and
  the job spec never carried `roots_source`, so the worker stored `null` and the null-omission
  rule then dropped the key entirely. The three job specs now carry it and the worker reads it
  back, so a paid success — and a paid failure, whose stored envelope is built the same way —
  reports the roots state its run actually saw. The same value now also reaches the
  lifecycle-generated error envelopes (`job_not_found`, a running/corrupt/incompatible job, and
  `codex_job_cancel` — plus the in-handler `unsupported_detail` guard, which only a direct Python
  caller can reach, since over MCP a bad `detail` is rejected as `invalid_arguments` before the
  handler runs), which probed roots and then discarded the answer;
  those report the CURRENT lookup, not the inspected job's originating run. `roots_source` is
  provenance rather than call identity, so it is excluded from the idempotency argument hash: a
  keyed call that reconnects with a different roots state still replays instead of failing
  `idempotency_conflict`, and pre-existing dedup entries keep matching unchanged. Results stored
  before this change carry no value and keep omitting the key. `RESULT_FORMAT` stays `7` (the
  field is already known at that format, so no stored payload became unreadable); bumps
  `FINGERPRINT` (`schema-63` → `schema-64`) for the published `codex://result-meta` description
  that now states which run the value describes. Not breaking (#393).
- **`capabilities-result` schema's `required` list contradicted the default response.**
  `ToolCapability.use_when`/`.returns` were marked required in the published schema (reachable via
  `codex_capabilities(include_schemas=["capabilities-result"])`), but `detail="summary"` — the
  default — strips both from every `tool_details` entry, so a strict client validating the
  default response against its own published schema failed. Both fields are now optional in the
  schema, modeling both detail modes; the response bytes are unchanged in either mode. Non-breaking:
  the schema is corrected to describe what was already being sent.
- `CapabilitiesDetailParam`'s description and the `codex_capabilities` docstring described
  `async_lifecycle` as part of every summary entry; it is only present for the `*_async` tools
  (3 of 17), which both now say explicitly.
- A `resources/read` failure's `error.data` now carries `resource_uri` (the URI that was
  requested) and `request_id`, matching the correlation fields the tool-error carrier already
  has via `meta.request_id`. Both are optional and populated only on the JSON-RPC (resource)
  carrier — the tool carrier is unaffected, since duplicating `request_id` there would be two
  homes for one fact (audit F6, #185). `resource_error_carrier` in `codex_capabilities` is
  updated to name both fields and to disclose, as a deliberate divergence, that this server
  keeps `code`/`message` rather than the `machine_code`/`human_message` spelling some §6
  profiles use.
- The `*_async` tools' `JobStarted` result now carries a `follow_up` object
  (`{next_step, tool, arguments, alternative}`, the same shape as `error.repair`) naming
  `codex_job_status` with literally callable arguments (audit F7).

## [0.15.0] - 2026-07-22

A status-signal and diff-gather-hardening release. `codex_status` learns to report an
administrative spend block, `codex_job_status`/`codex_job_list` expose a finished job's
success/failure without a fetch, the diff-gather paths gain consistency and bounded-memory
guarantees across every review scope, and several capability-discovery contracts are corrected to
match what they advertise. Tracked Codex is bumped to 0.145. The agent-visible surface changed
(result `fingerprint` `codex-in-claude/0.1/schema-49` → `schema-56`, and the persisted
`RESULT_FORMAT` `4` → `6`), so pre-1.0 this is a minor release; clients that cache by `fingerprint`
re-fetch the contract. Every change is backward-compatible — no tool, field, or error code was
removed or retyped.

### Added

- **`codex_status` reports an administrative spend block** (#359). codex 0.145 added
  `spendControlReached` to the app-server's rate-limit snapshot — a *spend* control the backend
  enforces, distinct from a quota window: no reset clears it. `rate_limit` now carries
  `spend_control_reached` (`true` | `false` | `null`) and `rate_limit.status` gains the value
  `blocked`, which outranks every window-derived verdict because its remedy is not waiting; it
  reports `limiting_window: null` and a `note` saying a reset will not clear it. The tri-state is
  load-bearing — `null` is not `false`, and a null never downgrades a healthy window reading to
  `unknown` (which would reintroduce the permanent-`unknown` failure #321 removed). The bundled
  `collaborating-with-codex` skill gains the matching rule: `blocked` is the one non-advisory
  rate-limit state — refuse the paid call rather than deferring it. Bumps the persisted
  result-format (`RESULT_FORMAT` `5` → `6`) for the new status value; not breaking.

- **`result_ok` on job status and list entries** (#335). `codex_job_status` and each
  `codex_job_list` entry now carry `result_ok` — a finished job's producer-declared outcome
  (`true` = success, `false` = a stored error envelope, `null` = running, unclassifiable, or a
  record finalized before this field) — so a stored failure can be triaged without a per-job fetch.
  It reports the outcome recorded when the result was written and does **not** guarantee the payload
  is still readable across releases. `codex_capabilities` gains an
  `async_lifecycle.result_ok_field` entry for structural discovery. Backward-compatible output
  addition; not breaking.

### Changed

- **Diff-gather git reads are now counted in bounded memory** (#331, #350, #351, #353). The
  untracked-file listing, the tracked-diff `--numstat` summary, and the untracked-count stderr read
  — all reached on the default `codex_review_changes` and free `codex_dry_run` paths — previously
  materialized whole git outputs unbounded in the workspace's changed-file count. They now stream
  through a single shared bounded runner, so a pathological workspace can neither exhaust server
  memory nor stall a review past its deadline, and a latent stderr deadlock is removed. The same
  work fixed a real corruption bug: a carriage return in a git-produced filename was rewritten by
  Python's universal-newline translation before the NUL-splitter saw it, silently omitting one file
  while still counting it as included — a quiet coverage-contract violation (#353). Reported counts
  and error vocabulary are unchanged, so **no `fingerprint` change**.

- **Every egress caveat now discloses that user-global Codex skills auto-load** (#358). Skills under
  `$CODEX_HOME/skills/` (default `~/.codex/skills/`) are discovered from **outside** the workspace
  and their bodies can reach OpenAI on any active call, despite `--ignore-user-config` — pre-existing
  behavior (verified against `codex-cli 0.144.1` and `0.145.0`), not new. The disclosure is
  corrected across the server instructions, all six tool descriptions and their capability entries,
  `codex_capabilities`' negative scope, `README.md`, `SECURITY.md`, `COMPATIBILITY.md`,
  `cli_contract.py`, and the `collaborating-with-codex` skill. A reword of covered descriptions, so
  it **bumps the `fingerprint`**; not breaking — the contract only ever promised `ignore-config`
  drops `$CODEX_HOME/config.toml`, never that all `$CODEX_HOME` content stays local.

- **Tracked Codex version bumped to `0.145`** (#361). `SUPPORTED_VERSIONS` now tracks `(0, 145)`;
  the CLI contract, help snapshots, and `KNOWN_MODEL_SLUGS` fallback are verified against
  `codex-cli 0.145.0`, which required no code change — help text, sandbox values, drift/auth
  signatures, and reasoning-effort handling are all intact, and the app-server schema diff is
  additive-only for the surface this plugin consumes. Advisory only: an untracked version warns in
  `codex_status` but never blocks, and the `CODEX_IN_CLAUDE_SUPPORTED_VERSIONS` override still
  applies. No agent-visible surface change, so no `fingerprint` bump.

### Fixed

- **Diff gathers are now consistency-checked and atomic across every review scope** (#336, #355). A
  gather runs its context summary and the transmitted diff as separate git invocations, so a
  concurrent edit or ref move between them could make the summary and the reviewed patch describe
  different content while `coverage.status` still reported `"complete"`. `working_tree` reviews now
  bracket that window with a best-effort porcelain state token; on a mismatch they set a new
  `tree_changed_during_gather` value on `coverage.omission_reasons`, degrading coverage to `partial`
  and (via the #319 rules) a `pass` verdict to `unknown`. It is a consistency caveat, not a claim
  that specific content was omitted, and it does not trip on a content-only re-edit — so `complete`
  is documented as no longer proof the tree held still. `branch` and `commit` reviews now resolve
  their refs to immutable commit object IDs once, up front, so the summary and diff cannot split
  under a mid-gather commit/reset/checkout; an unborn HEAD fails closed, a concurrent HEAD move is
  disclosed via the same token, and a `commit=<annotated-tag>` review peels the tag to its commit.
  Widens `CoverageOmissionReason` and bumps the persisted result-format (`RESULT_FORMAT` `4` → `5`,
  since an older closed-schema reader could reject the new enum value); not breaking.

- **Capability-discovery contracts corrected to match what they advertise** (#337, #370, #372, #373).
  Several `codex_capabilities` and error-envelope contracts were narrower or staler than their
  promise. `fingerprint_covers` said any change in a covered category bumps the `fingerprint` but
  silently excluded release-identity fields (`serverInfo.version`, the capabilities `version`); it
  now carries a description disclosing the carve-out in both directions, so a caching client can
  trust that an unchanged `fingerprint` means an unchanged contract. The `codex_capabilities` tool
  **description** advertised only two of the five `include_schemas` tokens (the parameter schema
  already listed all five), hiding the resource-blind route to `parameter-contracts`. And
  `invalid_arguments` omitted `allowed_values` for a rejected `list[Literal]` element — the one
  parameter shape the domain resolver did not read — on the very field whose repair hint says to use
  those values. The `include_schemas` token set is additionally drift-proofed against its runtime
  payload by an exact-equality test. Wording and coverage corrections that widen what is documented
  and weaken no guarantee — the `fingerprint` bump is for the `fingerprint_covers` and tool-
  description changes (the `allowed_values` fix is per-call envelope data, no discovered surface);
  not breaking.

- **`invalid_reasoning_effort` gives the local pre-spend guard a provenance-specific repair** (#332).
  The code is emitted from two paths: the Codex backend rejecting a sent effort (table repair
  `correct_arguments` + `codex_models`, correct there) and the local pre-spend guard refusing a
  hostile *resolved* value before any subprocess (zero spend). The guard previously inherited the
  backend repair, misdirecting an agent that branches on `error.repair` to a useless `codex_models`
  call. It now emits a provenance-specific repair with **no** tool: `correct_config` when the
  invalid value is the resolved `CODEX_IN_CLAUDE_REASONING_EFFORT` default, `correct_arguments` when
  it is an explicit per-call argument. Bumps the `fingerprint` for the corrected parameter contract;
  not breaking (`correct_config` is already a published `RepairStep`).

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
