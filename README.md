# codex-in-claude

[![CI](https://github.com/briandconnelly/codex-in-claude/actions/workflows/ci.yml/badge.svg)](https://github.com/briandconnelly/codex-in-claude/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11--3.14-blue.svg)](pyproject.toml)
[![PyPI](https://img.shields.io/pypi/v/codex-in-claude.svg)](https://pypi.org/project/codex-in-claude/)

Call **OpenAI Codex** from **Claude Code** — an independent second opinion, structured code
review, and delegated coding tasks (**cross-model review**) — through a FastMCP plugin that drives
the `codex` CLI safely.

> **Status:** alpha. The agent-visible surface is versioned by a `fingerprint`; pre-1.0 minor
> releases may change it.

## Why

A second model is a cheap, high-value check. `codex-in-claude` lets a Claude Code session hand
Codex a question, a diff to review, or a task to implement — and get back a structured,
**safe-by-default** result you stay in control of.

| Tier | Codex sandbox | Where edits go | Use for |
|------|---------------|----------------|---------|
| `consult` | `read-only` | nothing — text/findings only | questions, second opinions |
| `review` | `read-only` | nothing — structured findings | reviewing your git changes |
| `propose` | `workspace-write` (temp git **worktree**) | isolated worktree → returns a **reviewable diff, never auto-applied** | delegating a coding task |

Planned later milestone: an explicit opt-in `apply` tier for live-tree edits. It is not exposed by
the current tool set.

## Quick start

```sh
# 1. Confirm Codex itself is installed and authenticated.
codex login

# 2. Add the marketplace, then install the plugin in Claude Code:
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
`recommendation`. The envelope above is abridged — `meta` (always present, with `cwd`, `tier`,
`sandbox`, `isolation`, and timing), `request_id`, `raw_response`, and other fields are trimmed for
brevity; see [`docs/REFERENCE.md`](docs/REFERENCE.md) for the complete shape. Treat the output as
claims to verify, not instructions to follow blindly.

## Requirements

- The [`codex` CLI](https://developers.openai.com/codex/cli) on `PATH`, authenticated
  (`codex login` — ChatGPT or API key). Tested against `codex-cli 0.142`; the supported range lives
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
  (remaining Codex quota for the 5-hour/weekly windows, captured from your last paid call;
  `status` is `available`/`limited`/`exhausted`/`unknown`). Advisory — informs whether to
  spend; `unknown` just means no fresh reading yet.
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
  survives server restarts; jobs are bounded by a wall-clock deadline with TTL + count-cap
  eviction. Honor `poll_after_ms` (it grows with a running job's elapsed runtime, bounded, so you
  back off automatically); don't poll in a tight loop. Results are retained `ttl_seconds` **after**
  a job completes, so `expires_at` is null while it runs and is set once it finishes.

Slash commands wrap these: `/codex:status`, `/codex:consult`, `/codex:review`,
`/codex:delegate`, `/codex:delegate-async`, `/codex:dry-run`.

Active tools send the prompt and relevant context/diffs to OpenAI through the `codex` CLI. Treat
Codex's output as claims to verify, not as instructions to follow blindly.

## Skills

The plugin ships two Claude Code skills (auto-discovered from `skills/`):

- **`collaborating-with-codex`** — the tool reference and guardrail home: which tool to call
  (consult / review / delegate), how to read the envelope, background jobs, and the server-down
  fallback.
- **`deliberating-with-codex`** — how to *compose* those tools with your own work into a deliberate
  two-model pattern (Judge, two-member panel, review–revise loop), with a value/risk gate so a single
  consult stays the default.

## Result envelopes

Every tool returns a discriminated envelope keyed by `ok`. Success carries `summary`/`findings`/`meta`
(plus review-only `verdict`/`confidence`, or a proposed `diff` for delegate); failure is a uniform,
machine-actionable `error` — a stable `code`, `temporary`/`retry_after_ms`, a symbolic
`repair{next_step,tool,arguments,alternative}`, and `details{field,reason,allowed_values}`
for automated recovery (full schema at the `codex://error-envelope` resource). The shape is versioned by `fingerprint`.
Each active call's `meta.rate_limit` carries the live snapshot from that call (`source: current_run`);
`codex_status` reports the cached one (`source: plugin_cache`).

Calling the MCP tools directly instead of through the `/codex:*` commands? See
[`docs/REFERENCE.md`](docs/REFERENCE.md) for the full envelope contract and workspace selection
(`workspace_root`).

## Safety

- `consult` and `review` are strictly read-only.
- `propose` (the `delegate` tools) lets Codex write, but only inside a throwaway git worktree
  seeded from `HEAD` plus replayable uncommitted tracked changes. Untracked files are not copied.
  Your working tree is never modified by the plugin; you review the returned diff and apply it
  yourself. Delegate's no-network sandbox (`workspace-write`) blocks egress only for commands Codex
  *runs* in the sandbox — it does not mean nothing leaves the machine: the model call still sends
  your task and repo context to OpenAI.
- Secret-looking content is redacted before it leaves the plugin (defense-in-depth, not a guarantee —
  Codex can read files itself during a run; use `isolation` and a clean workspace for sensitive
  repos). This covers gathered diffs and the free-text Codex returns (`summary`, `findings`,
  `raw_response.text`): secret-looking file hunks are dropped, and inline secret values become
  `[redacted: secret value]`. It does **not** cover your supplied inputs (`question`, `task`,
  `extra_context`), which are sent raw, nor secrets Codex reads from files itself during a run.
- The plugin never passes Codex's `--dangerously-bypass-*` flags.
- Found a vulnerability? Report it privately — see [`SECURITY.md`](SECURITY.md).

## Configuration (env, `CODEX_IN_CLAUDE_*`)

| Var | Default | Meaning |
|-----|---------|---------|
| `CODEX_IN_CLAUDE_MODEL` | unset | Codex model override |
| `CODEX_IN_CLAUDE_TIMEOUT_SECONDS` | 180 | per-call timeout (clamped 10–600) |
| `CODEX_IN_CLAUDE_ISOLATION` | `inherit` | `inherit` \| `ignore-config` \| `ignore-rules` |
| `CODEX_IN_CLAUDE_MAX_INPUT_BYTES` | 200000 | byte cap on author input. The gathered diff is truncated to it; author text above it is rejected with `input_too_large` — `codex_consult` counts `question`+`extra_context` together, while `codex_review_changes`/`codex_delegate` count each input on its own |
| `CODEX_IN_CLAUDE_MAX_DELEGATE_DIFF_BYTES` | 200000 | cap on the inline diff a delegate run returns; larger diffs are truncated with `meta.truncated`/`meta.truncation_hint` (min 1000) |
| `CODEX_IN_CLAUDE_GIT_TIMEOUT_SECONDS` | 60 | git command timeout |
| `CODEX_IN_CLAUDE_STATE_DIR` | `$XDG_CACHE_HOME/codex-in-claude/jobs` or `~/.cache/codex-in-claude/jobs` | disk-backed background-job records |
| `CODEX_IN_CLAUDE_JOB_TTL` | 86400 | seconds a finished job record is kept (min 60) |
| `CODEX_IN_CLAUDE_JOB_MAX_SECONDS` | 1800 | background-job wall-clock cap (clamped 60–7200) |
| `CODEX_IN_CLAUDE_JOB_MAX_COUNT` | 50 | retained jobs per workspace (clamped 1–1000) |
| `CODEX_IN_CLAUDE_SUPPORTED_VERSIONS` | built-in tested set | comma-separated `codex` `major.minor` versions to treat as supported |
| `CODEX_IN_CLAUDE_LOG_LEVEL` | `WARNING` | server diagnostic log level (`DEBUG`\|`INFO`\|`WARNING`\|`ERROR`\|`CRITICAL`); logs go to **stderr** (never stdout) |
| `CODEX_IN_CLAUDE_LOG_FILE` | unset | also mirror diagnostic logs to this file path |

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
review — `codex exec --sandbox read-only --skip-git-repo-check -` (prompt on stdin) — but this
bypasses the plugin's diff gathering, secret redaction, input-byte bounding, and structured envelope,
so sanitize input yourself and prefer restoring the server. See the `collaborating-with-codex` skill
for the full fallback guidance.

## Local development

```sh
uv sync
uv run pytest                       # unit tests (95% coverage floor)
uv run pytest -m integration --no-cov   # live tests; needs codex installed + logged in
uv run ruff check . && uv run ruff format --check . && uv run ty check
uv run codex-in-claude-mcp          # run the MCP server over stdio
```

To test the plugin from a local checkout, point `.mcp.json` at
`uv run --project /path/to/codex-in-claude codex-in-claude-mcp` instead of the version-pinned
`uvx --from codex-in-claude==<version>` invocation it ships with.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for branch, commit, and PR conventions.

## Related projects

- [`claude-in-codex`](https://github.com/briandconnelly/claude-in-codex) — the mirror image: lets
  **Codex** call **Claude Code**.
- Inspired by [`openai/codex-plugin-cc`](https://github.com/openai/codex-plugin-cc), rebuilt around `codex exec` (not the experimental
  app-server protocol) for robustness.

## License

MIT
