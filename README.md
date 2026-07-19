# codex-in-claude

[![CI](https://github.com/briandconnelly/codex-in-claude/actions/workflows/ci.yml/badge.svg)](https://github.com/briandconnelly/codex-in-claude/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/pypi/pyversions/codex-in-claude.svg)](pyproject.toml)
[![PyPI](https://img.shields.io/pypi/v/codex-in-claude.svg)](https://pypi.org/project/codex-in-claude/)

Call **OpenAI Codex** from **Claude Code** — an independent second opinion, structured code
review, and delegated coding tasks (**cross-model review**) — through a FastMCP plugin that drives
the `codex` CLI safely.

**Contents:** [Why](#why) · [Quick start](#quick-start) · [Example](#example) ·
[Requirements](#requirements) · [Tools](#tools) · [Skills](#skills) ·
[Result envelopes](#result-envelopes) · [Safety](#safety) ·
[Configuration](#configuration-env-codex_in_claude_) · [Troubleshooting](#troubleshooting) ·
[Local development](#local-development)

## Why

A second model is a cheap, high-value check. `codex-in-claude` lets a Claude Code session hand
Codex a question, a diff to review, or a task to implement — and get back a structured,
**safe-by-default** result you stay in control of.

| Tier | Codex sandbox | Where edits go | Use for |
|------|---------------|----------------|---------|
| `consult` | `read-only` | nothing — text/findings only | questions, second opinions |
| `review` | `read-only` | nothing — structured findings | reviewing your git changes |
| `propose` (the `delegate` tools) | `workspace-write` (temp git **worktree**) | isolated worktree → returns a **reviewable diff, never auto-applied** | delegating a coding task |

Planned later milestone: an explicit opt-in `apply` tier for live-tree edits. It is not exposed by
the current tool set.

## Quick start

First, in a terminal, make sure the `codex` CLI is installed and log in (a no-op if you already are):

```sh
codex login
```

Then, inside a Claude Code session, add the marketplace and install the plugin:

```text
/plugin marketplace add briandconnelly/codex-in-claude
/plugin install codex-in-claude
```

Then run `/codex:status` in Claude Code. It is free (no model call) and checks that the `codex`
CLI is found, authenticated, and within the tested compatibility range.

For a first useful run:

- `/codex:consult is this approach sound?` for a read-only second opinion.
- `/codex:review` to review your current git changes.
- `/codex:delegate add focused tests for this behavior` to get a proposed diff in an isolated
  worktree.

The MCP server is launched on demand via `uvx` from a pinned PyPI release, so updates are deliberate.

## Example

Review your uncommitted changes from a Claude Code session:

> `/codex:review`

Codex inspects the diff **read-only** and returns a structured result envelope (abridged):

```json
{
  "ok": true,
  "tool": "codex_review_changes",
  "verdict": "concerns",
  "confidence": "high",
  "review_status": "completed",
  "coverage": { "status": "complete", "untracked_files_detected": 0, "untracked_files_omitted": 0, "omission_reasons": [] },
  "summary": "The retry path is correct, but the backoff delay leaks between calls and the new branch has no test coverage.",
  "findings": [
    {
      "severity": "high",
      "title": "Backoff delay is never reset after a success",
      "file": "src/app/retry.py",
      "line": 42,
      "evidence": "self._delay keeps its last value once a call succeeds",
      "risk": "A later transient failure starts from an inflated delay, adding latency.",
      "recommendation": "Reset self._delay to the base delay in the success branch."
    }
  ],
  "next_steps": ["Add a regression test asserting the delay resets after a success"],
  "meta": { "scope": "working_tree", "sandbox": "read-only", "elapsed_ms": 8137 }
}
```

`verdict` is one of `pass` / `concerns` / `fail` / `unknown`; `confidence` is `low` / `medium` /
`high`; every finding carries a `severity` (`critical` … `nit`) plus `evidence`, `risk`, and
`recommendation`. `verdict` is the *safe overall conclusion*: `review_status` tells you whether the
model actually ran (a tree with nothing reviewable returns `not_run`/`unknown`, never a false
`pass`), and `coverage` discloses anything the model was not shown — omitted untracked files
(governed by the `untracked` policy), a truncated diff, or a redacted file — downgrading a `pass`
over partial coverage to `unknown`. The envelope above is abridged — `meta` (always present, with `cwd`, `tier`,
`sandbox`, `isolation`, and timing), `request_id`, `raw_response`, and other fields are trimmed for
brevity; see [`docs/REFERENCE.md`](docs/REFERENCE.md) for the complete shape.

## Requirements

- **macOS or Linux (POSIX).** Windows is not supported natively — the async-job safety
  layer (file locks, process groups, signal-driven cancellation) is POSIX-only. Run it
  under [WSL2](https://learn.microsoft.com/en-us/windows/wsl/) on Windows. See
  [`COMPATIBILITY.md`](COMPATIBILITY.md) for the platform contract.
- The [`codex` CLI](https://developers.openai.com/codex/cli) on `PATH`, authenticated
  (`codex login` — ChatGPT or API key). Tested against `codex-cli 0.144`; the supported range lives
  in [`cli_contract.py`](src/codex_in_claude/cli_contract.py), `/codex:status` reports whether your
  version is in range, and
  [`COMPATIBILITY.md`](COMPATIBILITY.md) explains the policy.
- [`uv`](https://docs.astral.sh/uv/) on `PATH` (Claude Code launches the MCP server with `uvx`).
- Python 3.11+ available to `uvx`.
- `git` (for review and delegate).

## Tools

**Active (call the model and may spend tokens):**

- `codex_consult(question, …)` — read-only second opinion / answer.
- `codex_review_changes(scope, base, commit, paths, …)` — review `working_tree` / `branch` /
  `commit`; returns structured findings.
- `codex_delegate(task, …)` — implement a task in an isolated worktree; returns a reviewable
  `diff` that is **not** applied.
- `codex_consult_async(question, …)`, `codex_review_changes_async(scope, base, commit, paths, …)`,
  `codex_delegate_async(task, …)` — detached variants of the three active tools, taking the same
  arguments as their synchronous forms: each returns a `job_id` immediately. Starting a job commits
  to spend (it runs to completion or its deadline); poll with `codex_job_status` / `codex_job_result`.

**Free (local only):**

- `codex_status` — readiness, version, auth, resolved defaults, and a `rate_limit` block
  (remaining Codex quota for the shorter/rolling and longer windows, read **live** from the Codex
  app-server with no model spend; `status` is `available`/`limited`/`exhausted`/`unknown`/
  `unavailable`). Advisory — informs whether to spend; `unknown`/`unavailable` mean no usable
  reading, not a problem.
- `codex_transfer(transcript_path, …)` — hand off the current Claude Code session to a resumable
  Codex thread; returns `resume_command` (`codex resume <thread_id>`) to continue that exact
  conversation in Codex. No model call or token spend (a local file conversion via the experimental
  `codex app-server`), but it does create a thread in `$CODEX_HOME`. Not idempotent for a live
  session — Codex dedups only a byte-identical transcript, so re-running mid-session makes a new
  thread. Experimental.
- `codex_dry_run(scope, …)` — preview a review's scope/diff size/redactions before spending.
- `codex_delegate_dry_run(task, …)` — preview a delegate's seeded baseline (HEAD commit, plus
  tracked, uncommitted, and untracked counts and size) and prompt size before spending; no worktree
  is created.
- `codex_capabilities` — tool inventory + result fingerprint.
- `codex_models` — advisory catalog of valid `model` slugs, read from Codex's on-disk cache with a
  bundled static fallback; also browsable as the `codex://models` resource. Discovery only — `model`
  stays pass-through, so an unlisted slug still works and `codex exec` validates it.
- `codex_job_status(job_id, …)` / `codex_job_result` / `codex_job_consume_result` /
  `codex_job_cancel` / `codex_job_list` — background-job lifecycle. State is disk-backed and
  survives server restarts. Honor `poll_after_ms` rather than polling in a tight loop; deadlines,
  eviction, and result retention are covered in
  [`docs/REFERENCE.md`](docs/REFERENCE.md#background-jobs).

Slash commands wrap these: `/codex:status`, `/codex:transfer`, `/codex:consult`, `/codex:review`,
`/codex:delegate`, `/codex:delegate-async`, `/codex:dry-run`.

Active tools send the prompt and relevant context/diffs to OpenAI through the `codex` CLI. Treat
Codex's output as claims to verify, not as instructions to follow blindly.

## Skills

The plugin ships one Claude Code skill, auto-discovered from `skills/`:

- **`collaborating-with-codex`** — the router and shared safety contract for every Codex workflow.
  It selects ordinary consult, review, delegate, transfer, and async tools directly, and loads
  references on demand for independent-attempt or declared review–revise composition. A one-off
  critique or judgment remains an ordinary route rather than a separate deliberation mode.

## Result envelopes

Every result discriminates first on `ok`. On success, completed consult, review, and delegate calls
share their active-result fields; review alone adds `verdict`/`confidence`/`review_status`/`coverage`,
and delegate alone adds the proposed `diff`. Discovery, dry-run, transfer, async-start, and job-lifecycle tools have their
own success schemas. A fetched job result matches its originating consult, review, or delegate tool,
so branch on that result type before reading fields. Failure is a uniform, machine-actionable
`error` with a stable `code` and symbolic `repair` hint. The contract is versioned by `fingerprint`.

Calling the MCP tools directly instead of through the `/codex:*` commands? See
[`docs/REFERENCE.md`](docs/REFERENCE.md) for the full contract — every error field, rate-limit
reporting (`meta.rate_limit`), background-job semantics, and workspace selection (`workspace_root`).

## Safety

- `consult` and `review` are strictly read-only.
- `propose` (the `delegate` tools) lets Codex write, but only inside a throwaway git worktree
  seeded from `HEAD` plus replayable uncommitted tracked changes. Untracked files are not copied.
  Your working tree is never modified by the plugin; you review the returned diff and apply it
  yourself. Delegate's no-network sandbox (`workspace-write`) blocks egress only for commands Codex
  *runs* in the sandbox — it does not mean nothing leaves the machine: the model call still sends
  your task and repo context to OpenAI.
- Supplied prompts and context (`question`, `task`, `extra_context`, and similar author input) are
  sent raw. During every active call — including consult — Codex may read other files in the
  resolved workspace, and it also auto-loads context from that workspace: the project's `AGENTS.md`
  and any skills under `.agents/skills/` can be sent to OpenAI even if your prompt never mentions
  them (for delegate, the versions seeded into the throwaway worktree auto-load there). The
  plugin's isolation flags do not suppress this project-level context; details and the unverified
  edge cases are in [`COMPATIBILITY.md`](COMPATIBILITY.md). Best-effort redaction protects
  gathered diffs and returned free text
  (`summary`, `findings`, `raw_response.text`): secret-looking file hunks are dropped, and inline
  matches become `[redacted: secret value]`. It is output/diff defense-in-depth, not input
  protection or a guarantee; do not target a workspace containing secrets you cannot disclose.
- The plugin never passes Codex's `--dangerously-bypass-*` flags.
- Found a vulnerability? Report it privately — see [`SECURITY.md`](SECURITY.md).

## Configuration (env, `CODEX_IN_CLAUDE_*`)

| Var | Default | Meaning |
|-----|---------|---------|
| `CODEX_IN_CLAUDE_MODEL` | unset | Codex model override |
| `CODEX_IN_CLAUDE_REASONING_EFFORT` | unset | Codex reasoning-effort override, sent as a `model_reasoning_effort` config override on every paid call; an open per-model string the Codex backend validates — `codex_models` lists each model's advertised set (semantics and probes in `COMPATIBILITY.md`). The per-call `reasoning_effort` parameter overrides it; a backend-rejected value fails as `invalid_reasoning_effort` |
| `CODEX_IN_CLAUDE_TIMEOUT_SECONDS` | 300 | per-call timeout (clamped 10–600) |
| `CODEX_IN_CLAUDE_ISOLATION` | `inherit` | `inherit` \| `ignore-config` \| `ignore-rules` |
| `CODEX_IN_CLAUDE_EXTRA_ARGS` | unset | extra global `codex` options added to every **paid** exec call (consult/review/delegate), so you can select a `model_provider`/`--profile` even under `ignore-config` isolation (which drops `config.toml`, leaving `-c` the only lever). Allowlist only: `-c`/`--config KEY=VALUE`, `-p`/`--profile NAME`, `--enable`/`--disable FEATURE`. Anything else is refused with `extra_args_rejected` **before any spend** — including `-c` keys under `sandbox`/`approval_policy`/`shell_environment_policy` (guarantee-weakening) and the reserved `model`/`model_reasoning_effort` keys, plus their case/quote lookalikes (use `CODEX_IN_CLAUDE_MODEL`/`CODEX_IN_CLAUDE_REASONING_EFFORT` or the per-call `model`/`reasoning_effort` parameters instead; deny rules and rationale in `COMPATIBILITY.md`). `-c` values may hold secrets, so they are never echoed in `codex_status` or errors. A `--profile` layers an on-disk TOML this server cannot inspect — an operator-trust boundary (see `COMPATIBILITY.md`) |
| `CODEX_IN_CLAUDE_MAX_INPUT_BYTES` | 200000 | byte cap on author input: gathered diffs are truncated to it, author text above it is rejected with `input_too_large` (consult counts `question`+`extra_context` together; review/delegate cap each input separately) |
| `CODEX_IN_CLAUDE_MAX_DELEGATE_DIFF_BYTES` | 200000 | cap on the inline diff a delegate run returns; larger diffs are truncated with `meta.truncated`/`meta.truncation_hint` (min 1000) |
| `CODEX_IN_CLAUDE_MAX_OUTPUT_BYTES` | 10485760 | byte cap for captured stdout (head+tail window; run not killed); stderr is bounded to a separate ~1 MiB reserve |
| `CODEX_IN_CLAUDE_GIT_TIMEOUT_SECONDS` | 60 | git command timeout |
| `CODEX_IN_CLAUDE_STATE_DIR` | `$XDG_CACHE_HOME/codex-in-claude/jobs` or `~/.cache/codex-in-claude/jobs` | disk-backed background-job records |
| `CODEX_IN_CLAUDE_JOB_TTL` | 86400 | seconds a finished job record is kept (min 60) |
| `CODEX_IN_CLAUDE_JOB_MAX_SECONDS` | 1800 | background-job wall-clock cap (clamped 60–7200) |
| `CODEX_IN_CLAUDE_JOB_MAX_COUNT` | 50 | retained jobs per workspace (clamped 1–1000) |
| `CODEX_IN_CLAUDE_SUPPORTED_VERSIONS` | built-in tested set | comma-separated `codex` `major.minor` versions to treat as supported |
| `CODEX_IN_CLAUDE_LOG_LEVEL` | `WARNING` | server diagnostic log level (`DEBUG`\|`INFO`\|`WARNING`\|`ERROR`\|`CRITICAL`); logs go to **stderr** (never stdout) |
| `CODEX_IN_CLAUDE_LOG_FILE` | unset | also mirror diagnostic logs to this file path |
| `CODEX_IN_CLAUDE_ALLOW_UNSUPPORTED_PLATFORM` | unset | set to `1` to downgrade the non-POSIX startup refusal to a stderr warning for knowingly consult-only, unsupported use; the async-job safety layer cannot hold, so do not run delegate/review against untrusted work (see [Requirements](#requirements) / `COMPATIBILITY.md`) |

Two further variables, `CODEX_IN_CLAUDE_TIER_DEFAULT` and `CODEX_IN_CLAUDE_SANDBOX_DEFAULT`, exist
ahead of the planned `apply` tier. They only change the defaults `codex_status` reports — every
shipped tool pins its own tier and sandbox and ignores them.

## Troubleshooting

Run `/codex:status` first — it's free (no model call) and diagnoses most setup problems.

| Symptom | Cause | Fix |
|---------|-------|-----|
| `codex` not found | CLI not installed or not on `PATH` | Install the [`codex` CLI](https://developers.openai.com/codex/cli) and ensure it's on `PATH` |
| Not authenticated | No Codex login | `codex login` (ChatGPT or API key) |
| Unsupported-version warning | Your `codex` version is outside the tested range | Update `codex`, or set `CODEX_IN_CLAUDE_SUPPORTED_VERSIONS` once you've verified it works |
| `meta.workspace_warning` in results | Server fell back to its own launch directory | Run from the target repo, or pass `workspace_root` (see [`docs/REFERENCE.md`](docs/REFERENCE.md#workspace-selection)) |
| `codex_delegate` fails needing a commit | The temp worktree is seeded from `HEAD` | Make at least one commit first |
| `codex_rate_limited` error | Account hit a usage/rate limit | Back off for `retry_after_ms`, then retry |
| `Connection closed` / `No such tool available: mcp__codex-in-claude__*` | The stdio MCP server is down | Reconnect with the `/mcp` command (or restart the client), then confirm with `codex_status`; see the fallback note below |

A stdio MCP server can't be transparently auto-restarted (the client owns the pipe and the
`initialize` handshake), so recovery is a manual reconnect. On a fatal crash the server writes a
breadcrumb to **stderr** (server name, version, reason, and a `/mcp` reconnect hint) before exiting,
and logs clean disconnects (EOF / broken pipe / `SIGINT` / `SIGTERM`) as shutdown rather than crashes
— so the server logs tell you whether it died or was stopped.

If the MCP server is down, you can fall back to the `codex` CLI directly for a read-only consult or
review (prompt on stdin; set `WORKSPACE` to a directory you approve for disclosure):

```sh
WORKSPACE=/absolute/approved/path codex exec --sandbox read-only --ephemeral \
  --ignore-user-config --ignore-rules --disable remote_plugin \
  --cd "$WORKSPACE" --skip-git-repo-check -
```

Keep every flag — together they apply the plugin's guarantee-bearing flags at its strictest config
isolation — but this still bypasses the plugin's diff gathering, secret redaction, input-byte
bounding, and structured envelope, so sanitize input yourself and prefer restoring the server. See
the `collaborating-with-codex` skill for the full fallback guidance.

## Local development

```sh
uv sync
uv run pytest                       # unit tests (95% coverage floor)
uv run pytest -m integration --no-cov   # live tests; needs codex installed + logged in
uv run codex-in-claude-mcp          # run the MCP server over stdio
```

The full pre-PR gate — lint, format, types, tests — is defined once in
[`AGENTS.md` → Tooling](AGENTS.md#tooling).

To test the plugin from a local checkout, point `.mcp.json` at
`uv run --project /path/to/codex-in-claude codex-in-claude-mcp` instead of the version-pinned
`uvx --from codex-in-claude==<version>` invocation it ships with.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for branch, commit, and PR conventions.

## Related projects

- [`claude-in-codex`](https://github.com/briandconnelly/claude-in-codex) — the mirror image: lets
  **Codex** call **Claude Code**.
- Inspired by [`openai/codex-plugin-cc`](https://github.com/openai/codex-plugin-cc), rebuilt around
  `codex exec` for robustness: every paid call goes through it, and only `codex_transfer` touches
  the experimental app-server protocol.

## License

MIT
