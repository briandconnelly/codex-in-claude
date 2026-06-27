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

## Version policy

Advisory only. A version outside the tested set warns (`codex_status.version_warning`,
`StatusResult`) but never blocks — readiness depends only on the binary being found and
authenticated. Override the tested set with `CODEX_IN_CLAUDE_SUPPORTED_VERSIONS` (comma-separated
`major.minor`).

## Result extraction

The final answer is read from the `--output-last-message` file (stable). The `--json` JSONL event
stream is parsed **tolerantly** for optional metadata only (token usage, session id, error events),
so an event-schema change degrades metadata rather than breaking a run.

## Failure classification

A non-success `codex exec` run is classified from its stderr/stdout and JSONL `error` events against
the signature sets in `cli_contract.py`, checked in order so a more specific cause is never masked by
a generic one:

1. **auth** (`AUTH_FAILURE_PATTERNS`) → `codex_auth_required`.
2. **contract drift** (`CONTRACT_DRIFT_STDERR_PATTERNS`) → `cli_contract_changed`. Checked before
   rate-limit so a genuine contract change is never mistaken for a transient (retryable) failure.
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
- `details{field,reason,allowed_values}` describes a single offending field. The rejected `value`
  is deliberately never echoed — a parameter can accept arbitrary input that may be a secret.
  `field` + `reason` + `allowed_values` are sufficient to repair the call.
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
