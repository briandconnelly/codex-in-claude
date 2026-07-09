# Compatibility with the `codex` CLI

This plugin shells out to the OpenAI `codex` CLI. Every assumption it makes lives in
`src/codex_in_claude/cli_contract.py` so an upstream change is a one-file, greppable edit.
Design goal: **fail loudly and safely, never silently weaken a guarantee.**

Verified against `codex-cli 0.142`.

## What we invoke

- `codex exec --json --sandbox <mode> --cd <dir> --output-last-message <file> [--output-schema <file>]
  [--ephemeral] [--ignore-user-config] [--ignore-rules] [--skip-git-repo-check] [--add-dir <dir>]
  [--model <m>] -` — prompt delivered on **stdin** (the trailing `-`), keeping context out of argv.
- `codex --version`, `codex login status`, `codex exec --help` — free local probes.

Notably we do **not** use the `app-server` JSON-RPC/broker protocol (the source of most of the
upstream `codex-plugin-cc` reliability issues) nor the native `codex review`/`codex exec review`
subcommand (its `--output-schema` is not honored for the final message, and its output depends on
the user's Codex MCP fleet). Reviews use `codex exec` with a diff we gather ourselves.

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

## Flag classes

- **ALWAYS_SEND_FLAGS** — guarantee-bearing (sandbox, cd, json, output-last-message, isolation,
  output-schema, …). Sent unconditionally and never gated on `--help`. If `codex` removes or
  renames one, it rejects the invocation at argument parsing — before any model call, zero spend —
  and the failure is reported as `cli_contract_changed` with repair guidance.
- **HELP_GATED_FLAGS** — depth/cosmetic only (e.g. `--model`). Feature-detected via
  `codex exec --help`; dropped gracefully if absent and noted in `meta.compat_warnings`.

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
`appserver.py`. Verified against `codex-cli 0.142.5` via `codex app-server generate-json-schema`.

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
   (user-owned passthrough, not a plugin-contract drift; see the passthrough section above). Checked
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

**Pre-upgrade job results:** a background-job *success* result written by a pre-upgrade server
instance is still returned (its `meta.fingerprint` is re-stamped to the current surface).
A stored *error* result whose shape predates this release no longer matches the schema-16 error
envelope; it is treated as corrupt and returned as an `internal_error` result (message
`"job result could not be returned: …"`, with guidance to start a new job), rather than the stale
shape.
Pre-upgrade *error* results are therefore effectively invalidated; compatible success results are
not.
(Records that have actually expired past their TTL still return `job_not_found`.)

## When `codex` changes

Follow the full procedure in [`docs/UPGRADING-CODEX.md`](docs/UPGRADING-CODEX.md): run the no-spend
drift check (`uv run python scripts/check_codex_contract.py`), do the manual semantic review the
script can't, then update `cli_contract.py` (and the lockstep files), run the test gate plus the live
integration tests, and bump `FINGERPRINT`/`CHANGELOG.md` only if the agent-visible surface changed.
