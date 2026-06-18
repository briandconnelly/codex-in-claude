# codex-in-claude

Call **OpenAI Codex** from **Claude Code** — for an independent second opinion, structured
code review, and delegated coding tasks — through a FastMCP plugin that drives the `codex` CLI
safely.

> The mirror image of [`cc-plugin-codex`](https://github.com/briandconnelly/cc-plugin-codex)
> (which lets Codex call Claude Code). Inspired by `openai/codex-plugin-cc`, rebuilt around
> `codex exec` (not the experimental app-server protocol) for robustness.

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

## Requirements

- The [`codex` CLI](https://developers.openai.com/codex/cli) on `PATH`, authenticated
  (`codex login` — ChatGPT or API key). Tested against `codex-cli 0.140`.
- [`uv`](https://docs.astral.sh/uv/) on `PATH` (Claude Code launches the MCP server with `uvx`).
- Python 3.11+ available to `uvx`.
- `git` (for review and delegate).

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

The MCP server is launched on demand via `uvx` from a pinned release tag, so updates are deliberate.

## Tools

**Active (call the model and may spend tokens):**

- `codex_consult(question, …)` — read-only second opinion / answer.
- `codex_review_changes(scope, base, commit, paths, …)` — review `working_tree` / `branch` /
  `commit`; returns structured findings.
- `codex_delegate(task, …)` — implement a task in an isolated worktree; returns a reviewable
  `diff` that is **not** applied.
- `codex_delegate_async(task, …)` — same as `codex_delegate` but detached: returns a `job_id`
  immediately. Starting a job commits to spend (it runs to completion or its deadline).

**Free (local only):**

- `codex_status` — readiness, version, auth, resolved defaults.
- `codex_dry_run(scope, …)` — preview a review's scope/diff size/redactions before spending.
- `codex_capabilities` — tool inventory + result fingerprint.
- `codex_job_status(job_id, …)` / `codex_job_result` / `codex_job_consume_result` /
  `codex_job_cancel` / `codex_job_list` — background-job lifecycle. State is disk-backed and
  survives server restarts; jobs are bounded by a wall-clock deadline with TTL + count-cap
  eviction. Honor `poll_after_ms`; don't poll in a tight loop.

Slash commands wrap these: `/codex:status`, `/codex:consult`, `/codex:review`,
`/codex:delegate`, `/codex:delegate-async`, `/codex:dry-run`.

Active tools send the prompt and relevant context/diffs to OpenAI through the `codex` CLI. Treat
Codex's output as claims to verify, not as instructions to follow blindly.

## Workspace selection

When calling the MCP tools directly, pass `workspace_root` as an absolute path to the repository you
want Codex to inspect or edit. Claude Code usually supplies the current repo as an MCP root for slash
commands; if neither an MCP root nor `workspace_root` is available, the server may fall back to its
own launch directory and return `meta.workspace_warning`.

Review and delegate operations need a git repository. `codex_delegate` also requires at least one
commit so it can create the temporary worktree.

## Safety

- `consult` and `review` are strictly read-only.
- `propose` (the `delegate` tools) lets Codex write, but only inside a throwaway git worktree
  seeded from `HEAD` plus replayable uncommitted tracked changes. Untracked files are not copied.
  Your working tree is never modified by the plugin; you review the returned diff and apply it
  yourself.
- Secret-looking content in gathered diffs is redacted (defense-in-depth, not a guarantee — Codex
  can read files itself during a run; use `isolation` and a clean workspace for sensitive repos).
- The plugin never passes Codex's `--dangerously-bypass-*` flags.

## Configuration (env, `CODEX_IN_CLAUDE_*`)

| Var | Default | Meaning |
|-----|---------|---------|
| `CODEX_IN_CLAUDE_MODEL` | unset | Codex model override |
| `CODEX_IN_CLAUDE_TIMEOUT_SECONDS` | 180 | per-call timeout (clamped 10–600) |
| `CODEX_IN_CLAUDE_ISOLATION` | `inherit` | `inherit` \| `ignore-config` \| `ignore-rules` |
| `CODEX_IN_CLAUDE_MAX_INPUT_BYTES` | 200000 | cap on prompt/diff bytes |
| `CODEX_IN_CLAUDE_MAX_DELEGATE_DIFF_BYTES` | 200000 | cap on the inline diff a delegate run returns; larger diffs are truncated with `meta.truncated`/`meta.truncation_hint` (min 1000) |
| `CODEX_IN_CLAUDE_GIT_TIMEOUT_SECONDS` | 60 | git command timeout |
| `CODEX_IN_CLAUDE_STATE_DIR` | `$XDG_CACHE_HOME/codex-in-claude/jobs` or `~/.cache/codex-in-claude/jobs` | disk-backed background-job records |
| `CODEX_IN_CLAUDE_JOB_TTL` | 86400 | seconds a finished job record is kept (min 60) |
| `CODEX_IN_CLAUDE_JOB_MAX_SECONDS` | 1800 | background-job wall-clock cap (clamped 60–7200) |
| `CODEX_IN_CLAUDE_JOB_MAX_COUNT` | 50 | retained jobs per workspace (clamped 1–1000) |
| `CODEX_IN_CLAUDE_SUPPORTED_VERSIONS` | built-in tested set | comma-separated `codex` `major.minor` versions to treat as supported |

## Local development

```sh
uv sync
uv run pytest                       # unit tests (95% coverage floor)
uv run pytest -m integration --no-cov   # live tests; needs codex installed + logged in
uv run ruff check . && uv run ruff format --check . && uv run ty check
uv run codex-in-claude-mcp          # run the MCP server over stdio
```

To test the plugin from a local checkout, point `.mcp.json` at
`uv run --project /path/to/codex-in-claude codex-in-claude-mcp` instead of the pinned `uvx` tag.

## License

MIT
