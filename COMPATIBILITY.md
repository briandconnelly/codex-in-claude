# Compatibility with the `codex` CLI

This plugin shells out to the OpenAI `codex` CLI. Every assumption it makes lives in
`src/codex_in_claude/cli_contract.py` so an upstream change is a one-file, greppable edit.
Design goal: **fail loudly and safely, never silently weaken a guarantee.**

Verified against `codex-cli 0.145.0`.

## Platform support

**macOS or Linux (POSIX) only.** The async-job safety layer — `fcntl` advisory locks
(pid-reuse / zombie-worker guards), process-group teardown (`os.killpg` /
`start_new_session`), and `SIGTERM`-driven graceful cancellation — is POSIX-only. On a
non-POSIX platform these guarantees quietly degrade to owned-children-only locking and
direct-PID kills that orphan `codex`'s child processes, so the server refuses to start
there instead of shipping a half-safe process model.

- [WSL2](https://learn.microsoft.com/en-us/windows/wsl/) reports `os.name == "posix"` and
  is unaffected (it *is* Linux).
- `CODEX_IN_CLAUDE_ALLOW_UNSUPPORTED_PLATFORM=1` downgrades the startup refusal to a
  stderr warning for operators who knowingly accept consult-only, unsupported use; do not
  use `codex_delegate`/`codex_review_changes` against untrusted work in that mode.

The `pyproject.toml` trove classifiers declare `Operating System :: MacOS` and
`Operating System :: POSIX :: Linux` (not `OS Independent`) so PyPI reflects this.

## What we invoke

- `codex exec --json --sandbox <mode> --cd <dir> --output-last-message <file> [--output-schema <file>]
  [--ephemeral] [--ignore-user-config] [--ignore-rules] [--skip-git-repo-check] [--add-dir <dir>]
  [--model <m>] -` — prompt delivered on **stdin** (the trailing `-`), keeping context out of argv.
- `codex app-server` — short-lived JSON-RPC sessions, driven by `codex_transfer` (session import)
  and by `codex_status`'s rate-limit read (`account/rateLimits/read`, no model spend — see #321).
  See "Session transfer" below.
- `codex --version`, `codex login status`, `codex exec --help` — free local probes.

Every paid call family — `codex_consult[_async]`, `codex_review_changes[_async]`, and
`codex_delegate[_async]` — runs its model work on `codex exec` alone. None uses the native
`codex review`/`codex exec review` subcommand, and none uses the `app-server`
JSON-RPC/broker protocol, which was the source of most of the upstream `codex-plugin-cc`
reliability issues. Reviews use `codex exec` with a diff we gather ourselves.

**Why not native review — re-verified at codex-cli 0.144.1 (issue #124).** `codex exec review`
now advertises `--output-schema`, `--output-last-message`, and `--json`, but `--output-schema` is
**accepted and silently ignored**. A clean-room run —

```sh
codex exec review --uncommitted --ignore-user-config --ignore-rules --strict-config \
  --ephemeral --output-schema <strict FINDINGS_OUTPUT_SCHEMA> --output-last-message <file> --json
```

— exits 0 yet writes free-form prose (not schema-conforming JSON) to the last-message file, and no
structured-findings payload appears anywhere in the `--json` event stream (only `command_execution`
items and one prose `agent_message`). Native review therefore can't back our strict result contract. Two lesser notes:
`codex exec review` has **no `--sandbox` flag** (config-based read-only control was not tested), and
the historical "output depends on the user's Codex MCP fleet" concern was **not re-tested** this
pass — it appears mitigable via `--disable remote_plugin`. Adopting native review remains blocked on
`--output-schema` alone; re-open the question only if a future Codex honors it.

Two flows reach the `app-server` surface: `codex_transfer` (transcript import) and `codex_status`'s
rate-limit read (`account/rateLimits/read`, added for #321 when codex 0.144 moved quota off the
`codex exec` stream). Both are quarantined the same way: the surface is experimental upstream, so
every assumption lives in `cli_contract.py` and `appserver.py`, neither call spends model tokens, and
no paid call depends on either. The rate-limit read verifies against **codex-cli 0.145.0** (probe:
drive `codex app-server` and confirm `account/rateLimits/read` returns a quota block; an integration
test does this live). See "Session transfer" below for the import flow.

## Sandbox modes

`--sandbox` is the capability boundary for a run (`cli_contract.py`): `read-only` for the
consult/review tiers, `workspace-write` for the propose tiers (`codex_delegate`,
`codex_delegate_async`); we never pass `danger-full-access` or `--dangerously-bypass-*` by default.

**`workspace-write` permits filesystem writes inside the workspace but blocks network egress.** This
is codex's own sandbox boundary and we pass it through deliberately. The practical consequence: a
propose/apply task **cannot perform network operations** — `git push`/`fetch`, `gh ...`, `curl`,
`npm publish`, dependency installs, etc. all fail inside the sandbox (typically with a
`Could not resolve host` / DNS error). Delegated tasks must therefore be self-contained; do any
network step yourself after reviewing and applying the returned diff. The tool docstrings and the
`codex_capabilities` `negative_scope` state this so a calling agent doesn't assume write access
implies internet access.

## Remote-plugin isolation (`remote_plugin`, #287)

Codex **0.143+** flipped the `remote_plugin` feature to **default-on**, which makes named
third-party connectors (GitHub, Gmail, Google Drive, Slack, Notion, …) available to the model on
every run. Those connectors are network side-effect / data-disclosure channels **outside** the
`--sandbox` filesystem boundary, so they are incompatible with this server's safe, read-only-by-default
posture. Crucially, `--ignore-user-config` does **not** neutralize them: plugins load from marketplace
snapshots (`~/.cache/codex-runtimes/`, `~/.codex/.tmp/bundled-marketplaces/`), not `config.toml`.

The server therefore sends **`--disable remote_plugin`** on **every model-bearing `codex exec` call**,
regardless of tier or isolation (`cli_contract.py`: `DISABLE_FEATURE_FLAG` + `REMOTE_PLUGIN_FEATURE`,
emitted in `codex.build_exec_command`). It is an **ALWAYS_SEND** guarantee-bearing flag:

- `--disable <FEATURE>` is documented as exactly `-c features.<FEATURE>=false`, and it wins over any
  `--enable`/`-c features.remote_plugin=true` **regardless of order**.
- An **unknown feature name fails loud** (`Error: Unknown feature flag`), so a future rename/removal of
  `remote_plugin` upstream surfaces as `cli_contract_changed` at arg-parse — zero spend — rather than a
  silent posture regression. Verify with a tool-surface probe on each Codex upgrade.

**Scope and boundary.** The guarantee covers model-bearing `codex exec` calls (consult/review/delegate);
it does not describe the separate `codex app-server` path used by `codex_transfer` (no model call). And
like the sandbox/approval `-c` denials below, it is bounded by the **`--profile` operator-trust
boundary** — an opaque profile this server cannot inspect could re-enable the feature, so only enable
that knob with profiles you control.

## Implicit Codex context (`AGENTS.md`, both skills roots, #300, #358)

`codex exec` **automatically loads** the resolved workspace's `AGENTS.md` into model context and
**auto-discovers** skills from two roots (per upstream docs: name/description metadata up front; a
skill's body loads when the skill is selected):

- the workspace's **`.agents/skills/`** — project-level, and
- **`$CODEX_HOME/skills/`** (default `~/.codex/skills/`) — **user-global, discovered from outside
  the workspace**, so no choice of workspace excludes it.

It needs no tool-directed read, and every model-bearing call in this plugin runs `codex exec`, so
that content can reach OpenAI even when the caller's prompt never mentions those files. Verified
empirically against codex-cli 0.145.0 (2026-07-21, issues #300 and #358) — including an A/B against
0.144.1, which behaved identically despite 0.145 shipping the new default-on `skill_search` feature,
so the user-global discovery is pre-existing rather than new. The behavior is invisible in
`codex exec --help` (no flag, no subcommand), so the mechanical help-drift check cannot catch
upstream changes to it. Upstream docs:
[AGENTS guidance](https://developers.openai.com/codex/concepts/customization#agents-guidance) and
[skills](https://developers.openai.com/codex/concepts/customization#skills).

**The isolation flags do not suppress it.** `--ignore-user-config` and `--ignore-rules` cover
specific `$CODEX_HOME` state — `config.toml` and execpolicy `.rules` respectively — and **not**
`AGENTS.md`, `.agents/skills/`, or `$CODEX_HOME/skills/`; no `isolation` value changes this. The
user-global case is the surprising one: `--ignore-user-config` reads as broad user-level isolation
but drops only `config.toml`, and a probe run *with* that flag still emitted a `$CODEX_HOME/skills/`
skill body. (The plugin's default `isolation=inherit` does not even send the flag — see
`config.isolation_flags`.) For the delegate tools the `AGENTS.md`/skills seeded into the throwaway
worktree (committed content plus replayed uncommitted tracked changes; untracked files are not
copied) auto-load there too, and the user-global skills load alongside them — neither tracked nor
seeded, so scrubbing the worktree does not exclude them.

### Re-verifying on a Codex upgrade

Marker probes are the only way to observe any of this. In a scratch git repo, plant a unique
codeword in `AGENTS.md`, a second in a marker skill under `.agents/skills/<name>/SKILL.md`, and a
third in a temporary global skill at `$CODEX_HOME/skills/<name>/SKILL.md`. Run a consult under the
plugin's flag set that never mentions those files, and ask it to list every available skill and
every codeword in context; then invoke the global marker skill by name and ask for its codeword.
The marker skill appearing in the listing confirms discovery, and its codeword coming back confirms
body egress. **Remove the temporary global skill afterwards.**

Observed under codex-cli 0.145.0 with the flag set above (observations, not guarantees — re-run the
probe rather than assuming they still hold):

| Question | Observed |
|---|---|
| `$CODEX_HOME/skills/` discovered despite `--ignore-user-config`? | **Yes**, body content reached the model |
| Project `.claude/skills/` discovered? | **No** |
| Parent-directory `AGENTS.md` above the git root loaded? | **No** (probed with cwd == git root; the cwd ≠ root case was not disambiguated) |
| `project_doc_max_bytes=0` fully disables loading? | **Not verified — do not assume** |

## Flag classes

- **ALWAYS_SEND_FLAGS** — guarantee-bearing (sandbox, cd, json, output-last-message, isolation,
  output-schema, …). Sent unconditionally and never gated on `--help`. If `codex` removes or
  renames one, it rejects the invocation at argument parsing — before any model call, zero spend —
  and the failure is reported as `cli_contract_changed` with repair guidance.
- **HELP_GATED_FLAGS** — depth/cosmetic only (e.g. `--model`). Feature-detected via
  `codex exec --help`; dropped gracefully if absent and noted in `meta.compat_warnings`.

## Reasoning-effort control (`model_reasoning_effort`, #309)

`codex exec` 0.145.0 has no dedicated reasoning-effort flag (verified against
`codex exec --help`, 2026-07-21 — byte-identical to 0.144.1's), so the per-call
`reasoning_effort` parameter and
`CODEX_IN_CLAUDE_REASONING_EFFORT` are sent as a **config override**:
`-c model_reasoning_effort="<value>"`, with the value **TOML-string-encoded** (JSON string syntax,
which is valid TOML). Codex TOML-parses the `-c` right-hand side and falls back to a string only
when that parse fails, so a raw interpolation would retype boolean/numeric/collection-shaped values
(codex 0.144.3 then rejects them locally as an invalid type) and silently unwrap quoted ones;
encoding makes the advertised open string round-trip exactly. A config key cannot be help-gated —
`--help` advertises flags,
not config keys — so a requested effort is sent unconditionally. Drift coverage is **narrower than
ALWAYS_SEND**: only removal of the `-c` flag itself fails loudly as `cli_contract_changed` with
zero spend. If a future `codex` renames or removes the **key**, the drift is **silent** — codex
tolerates unknown `-c` keys as junk it never reads (the same tolerance recorded for lookalike keys
below) — and the requested effort is quietly ignored; the re-verification probe in
`docs/UPGRADING-CODEX.md` is the guard for that case. (Verified 2026-07-13: a CLI `-c` override
survives `--ignore-user-config`, so an explicit effort stays effective under every isolation mode.)

The **semantic value set** is open and not allowlisted by this plugin. The plugin still enforces
transport-shape bounds (length and argv/JSON safety); values passing those bounds are sent
unchanged. The CLI accepts such a string silently, and the **backend** rejects an unsupported
model/effort combination at request time with a 400 whose message carries
`[ReasoningEffortParam] [reasoning.effort] [invalid_enum_value] …` (probed against codex-cli
0.144.3, 2026-07-13). That message also matches the generic `invalid value` drift pattern, so the
classifier checks `REASONING_EFFORT_REJECTION_MARKERS` (`reasoning.effort`,
`reasoningeffortparam` — deliberately **not** the config key name) first: when this run sent a
first-class effort override and **every marker appears in its bracketed `[…]` field form**, the
failure is the caller's argument (`invalid_reasoning_effort`), not contract drift. A marker as a
free substring does not match — an operator passthrough naming one (`--enable reasoning.effort`, a
profile so named) stays attributable to `extra_args_rejected`. A passthrough descriptor that
itself carries the full bracketed signature (a profile literally named
`[reasoning.effort][ReasoningEffortParam]`) is attributed to the passthrough *before* the backend
check, so it cannot impersonate the backend rejection either. A rejection naming only
`model_reasoning_effort` (the key) still fails loudly as `cli_contract_changed`. The accepted set
genuinely varies by model and account — the backend advertised
`none|minimal|low|medium|high|xhigh` for gpt-5.5 on ChatGPT, while the models cache advertises
`max`/`ultra` for other slugs — so discovery stays advisory (below) and no enum is pinned.

**Discovery** reads the same undocumented `models_cache.json` as the slug catalog: each entry's
`default_reasoning_level` (a string) and `supported_reasoning_levels` (a list of
`{effort, description, …}` objects, of which only the `effort` tokens are surfaced) map to
`ModelInfo.default_reasoning_effort` / `supported_reasoning_efforts`, defensively validated
(`REASONING_EFFORT_TOKEN_PATTERN`, `SUPPORTED_EFFORTS_MAX_ENTRIES`) and advisory only. The bundled
static fallback carries no effort data.

## Operator extra-args passthrough (`CODEX_IN_CLAUDE_EXTRA_ARGS`, #231)

An opt-in operator knob adds extra **global** `codex` options to every paid `exec` invocation
(consult/review/delegate). It is a small allowlist — `-c`/`--config KEY=VALUE`, `-p`/`--profile NAME`,
`--enable`/`--disable FEATURE` — appended after the plugin's own help-gated tokens and before the
stdin sentinel, so it can select a `model_provider`/`--profile` (its motivating use is doing so under
`ignore-config` isolation, which sends `--ignore-user-config` and drops `config.toml`, leaving `-c`
the only lever) **without** displacing the envelope-bearing flags. Anything outside the allowlist is
refused at parse time with `extra_args_rejected`, before any spend.

This passthrough is **user-owned surface, not part of the CLI contract**: the option names/config
keys/profile names an operator supplies are their responsibility, so when `codex` rejects one the
expected signature is `extra_args_rejected` (operator config to fix) — **not** `cli_contract_changed`.
Drift is attributed to the passthrough only when `codex`'s rejection names one of the (sanitized)
descriptors this server injected; a rejection of a plugin-owned guarantee flag still fails loudly as
`cli_contract_changed`. Two boundaries the allowlist cannot fully police, and why:

- **`-c` values are free-form** and can override any dotted config path. Keys under `sandbox`,
  `approval_policy`, or `shell_environment_policy` are refused because they would weaken a guarantee
  this server advertises (the sandbox capability boundary, the delegate no-network-egress promise, the
  approval posture, or the host-env isolation of commands `codex` runs). The key is normalized the way
  codex's own `-c` parser trims it before this check, so a leading/segment space cannot slip a denied
  key past. A `-c` value may hold a secret, so it is never echoed in `codex_status` or an error
  envelope.
- **`model` and `model_reasoning_effort` are reserved for the first-class controls** (#310, #309).
  `meta.model` (and `raw_response.model`) report the model the per-call `model` parameter or
  `CODEX_IN_CLAUDE_MODEL` requested, and `meta.reasoning_effort` reports the effort the per-call
  `reasoning_effort` parameter or `CODEX_IN_CLAUDE_REASONING_EFFORT` sent; a passthrough
  `-c model=…` / `-c model_reasoning_effort=…` would make the run use the operator's value while
  the envelope reports the per-call/server value (null in the common case), so both exact keys are
  refused at parse time. The parser also conservatively refuses case- and quote-varied lookalikes
  (`Model`, `"model_reasoning_effort"`) that codex-rs 0.144.3 treats as distinct junk keys, not
  aliases. Set the env var or the per-call parameter instead — those flow into
  `resolved_defaults` and the meta fields correctly. This is not a `model_*` root reservation:
  `model_provider`/`model_providers.*` (this knob's motivating use case) and other `model_*`
  keys still pass through. An opaque `--profile` can still set either key — the operator-trust
  boundary below, restated, not closed.
- **`remote_plugin` is wholly plugin-owned in the passthrough.** Both `--enable remote_plugin` and
  `--disable remote_plugin`, and any `-c features.remote_plugin=…` (either spelling, since
  `--enable X` == `-c features.X=true`), are refused — the server manages this feature as a documented
  security guarantee (#287, above). `--disable` is refused even though it agrees with the plugin, so a
  drift on the plugin's own guarantee flag can't be misattributed to the operator's passthrough. The
  refusal also covers the bare **`-c features=…`** parent key (a TOML inline table that could reach
  `remote_plugin`) and quoted key segments that resolve to the same path (`features."remote_plugin"`,
  `"features".remote_plugin`). Other features set by their own dotted key (`-c features.some_other=true`,
  `--disable some_other`) are still allowed.
- **`--profile` layers an opaque on-disk TOML** this server cannot inspect. A profile can therefore
  re-introduce configuration the denylist would otherwise refuse, so a profile is a documented
  **operator-trust boundary** — only enable this knob with profiles you control.

## Version policy

Advisory only. A version outside the tested set warns (`codex_status.version_warning`,
`StatusResult`) but never blocks — readiness depends only on the binary being found and
authenticated. Override the tested set with `CODEX_IN_CLAUDE_SUPPORTED_VERSIONS` (comma-separated
`major.minor`).

## Result extraction

The final answer is read from the `--output-last-message` file (stable). The `--json` JSONL event
stream is parsed **tolerantly** for optional metadata only (token usage, session id, error events),
so an event-schema change degrades metadata rather than breaking a run.

## Session transfer (`codex app-server`)

`codex_transfer` imports a Claude Code session transcript into a resumable Codex thread by driving
`codex app-server` — a newline-delimited JSON-RPC 2.0 stream over stdio (one JSON object per line, no
`Content-Length` framing). This whole surface is **experimental** upstream (`codex app-server` is
labeled `[experimental]` and the import method rides behind the `experimentalApi` capability), so
every assumption lives in `cli_contract.py` (the `APP_SERVER_*` / `IMPORT_*` constants) and
`appserver.py`. Verified against `codex-cli 0.145.0` via `codex app-server generate-json-schema --out <DIR>`.
The 0.144.1 → 0.145.0 schema diff is additive only for the consumed surface (an optional
`migrationSource`, a `MEMORY` item type, an optional `memory` details array, an optional
`subErrorType` on failures), so nothing this plugin sends or reads changed.

The flow: `initialize` (with `capabilities.experimentalApi=true`) → `initialized` notification → one
`externalAgentConfig/import` request carrying a single `SESSIONS` migration item → wait for the
matching `externalAgentConfig/import/completed` notification → terminate the child. The client is
deliberately single-request (no broker, no session reuse).

**Thread-id discovery.** Two sources, in order:

1. **The completed notification** (`itemTypeResults[SESSIONS].successes[].target`) — the imported
   thread id, present on a **fresh** import. This is part of the app-server's *emitted* JSON schema
   (`generate-json-schema`), so it is the primary, versioned surface.
2. **The import ledger** `$CODEX_HOME/external_agent_session_imports.json` (undocumented — same drift
   class as `models_cache.json`) — read tolerantly and bounded, only as a fallback. Codex deduplicates
   a byte-identical transcript to a silent no-op (empty `successes` **and** `failures`), so a
   re-import's thread id is recoverable only here, matched on `source_path` + `content_sha256`.

Because a live Claude session transcript grows on every turn, re-transferring it is **not** idempotent
— the changed bytes are a fresh import with a new thread; the ledger fallback only fires for a
genuinely unchanged (typically closed) transcript. An old CLI without the import method returns
JSON-RPC `-32601` (method-not-found) → `transfer_unsupported` (the hard backstop behind the advisory
version gate). A completed import with no `target` and no ledger record → `transfer_incomplete`, naming
the ledger it checked.

Any other error on the import *request* is classified by its JSON-RPC code, because the two cases have
opposite owners. A code in the reserved `-32768..-32000` range (invalid params/request, parse/internal
error, plus the server-defined `-32000..-32099` band) — or an error malformed enough to carry no integer
`code` — means **our request** drifted from the CLI's schema, so it fails loudly as
`cli_contract_changed`. An application-range code is Codex rejecting **this transcript**, so it surfaces
as `transfer_failed` carrying the app-server's message. Broken stream or handshake (EOF, a non-JSON
line, an `initialize` error, a missing `codexHome`) remains `cli_contract_changed`.

## Failure classification

A non-success `codex exec` run is classified from its stderr/stdout and JSONL `error` events against
the signature sets in `cli_contract.py`, checked in order so a more specific cause is never masked by
a generic one:

1. **auth** (`AUTH_FAILURE_PATTERNS`) → `codex_auth_required`.
2. **contract drift** (`CONTRACT_DRIFT_STDERR_PATTERNS`) → `cli_contract_changed`, **unless** the
   rejection names an operator `CODEX_IN_CLAUDE_EXTRA_ARGS` descriptor → `extra_args_rejected` instead
   (user-owned passthrough, not a plugin-contract drift; see the passthrough section above), **or**
   this run sent a first-class reasoning-effort override and the failure carries the backend's
   `REASONING_EFFORT_REJECTION_MARKERS` → `invalid_reasoning_effort` (a caller value to correct; see
   the reasoning-effort section above). Checked
   before rate-limit so a genuine contract change is never mistaken for a transient (retryable) failure.
3. **rate limit** (`RATE_LIMIT_PATTERNS`: `rate limit`, `too many requests`, `usage limit`, `quota`,
   `retry-after`, plus `429` matched with word boundaries so an incidental digit run can't fire it)
   → `codex_rate_limited`, `temporary=True` with `retry_after_ms` set from a parsed
   `Retry-After`/"retry after Ns" value **when it is seconds-valued** (a non-second unit or HTTP-date
   is ignored), else `RATE_LIMIT_DEFAULT_BACKOFF_MS` (60s). Lets a caller back off deterministically
   instead of retry-storming a transient limit.
4. everything else → `nonzero_exit`.

Signatures are confirmed against real `codex` output; this file is the source of truth for the
phrasings, so update `cli_contract.py` (one place) when upstream wording changes.

## Structured output

`--output-schema` uses OpenAI strict structured outputs: every property must appear in `required`
and every object must set `additionalProperties: false`. The findings schema in `schemas.py`
follows this (optional fields are nullable but still required).

## Canonical error envelope

Every `ok: false` response carries a uniform `error` object. The full schema is published at the
`codex://error-envelope` resource (fetch it once and cache by `fingerprint`); clients should
read that resource rather than hard-code the shape.

**Key contract points:**

- `temporary` (bool) signals whether retrying can succeed; `retry_after_ms` is always present
  (`null` unless `temporary` is true). Callers must read `temporary` — not `retry_after_ms`
  presence — as the retry signal.
- `repair{next_step,tool,arguments,alternative}` provides a stable SYMBOLIC `next_step` label
  (e.g. `poll_job_status`, `correct_arguments`) that callers branch on in code; `tool`/`arguments`
  name a recovery tool call; `alternative` is prose fallback. The `repair` field is omitted only
  when no corrective path exists.
- `details{field,fields,reason,allowed_values}` describes the offending input(s): `field` names a
  single input; `fields` (mutually exclusive with `field`; non-empty, unique) names inputs whose
  *combination* is invalid (e.g. a combined-size limit where no single input is at fault). The
  rejected `value` is deliberately never echoed — a parameter can accept arbitrary input that may be
  a secret. Neither carrier is required; whichever is present (`field`, `fields`, or neither) plus
  `reason`/`allowed_values` is sufficient to repair the call.
- Absent optional fields are **omitted** from the payload (no placeholder nulls), except
  `retry_after_ms` which is always present.

**Opaque wire branch:** tools that publish `outputSchema` include a compact opaque error branch
(a discriminated `ok: false` object) rather than the full error schema inline. Callers must branch
on `ok` first; the full envelope shape lives solely at `codex://error-envelope`. This keeps the
preloaded `tools/list` catalog compact.

**Stored job results across releases:** a persisted `result.json` is guaranteed readable only by
the release that wrote it. A backward-compatible *newer* release generally still reads it (added
fields are optional, and a compatible pre-upgrade payload is returned with `meta.fingerprint`
re-stamped to the current surface — pre-1.0, a breaking field removal/retype can end that).
**Cross-format replay is unsupported** (notably downgrade — the same code fires in the upgrade
direction after a breaking format change): each job record carries the writer's persisted-format
version (`RESULT_FORMAT`, stamped at spawn), and a stored result that fails validation under a
*different* recorded format is returned as `job_result_incompatible` — `temporary: false`, repair
`start_new_job` — because no retry can make this release able to read it (a reused
`idempotency_key` cannot succeed either; use a new one or none). A result that fails
validation under the *same*, a missing, or an unusable recorded format is corruption and stays
`internal_error` (message `"job result could not be returned: …"`).
(Records that have actually expired past their TTL still return `job_not_found`.)

## When `codex` changes

Follow the full procedure in [`docs/UPGRADING-CODEX.md`](docs/UPGRADING-CODEX.md): run the no-spend
drift check (`uv run python scripts/check_codex_contract.py`), do the manual semantic review the
script can't, then update `cli_contract.py` (and the lockstep files), run the test gate plus the live
integration tests, and bump `FINGERPRINT`/`CHANGELOG.md` only if the agent-visible surface changed.
