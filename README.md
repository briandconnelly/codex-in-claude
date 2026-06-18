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
| `apply` | `workspace-write` (live tree) | live working tree, in place | explicit opt-in (later milestone) |

## Requirements

- The [`codex` CLI](https://developers.openai.com/codex/cli) on `PATH`, authenticated
  (`codex login` — ChatGPT or API key). Tested against `codex-cli 0.140`.
- Python 3.11+ available (the MCP server is launched via `uvx`).
- `git` (for review and delegate).

## Install

```sh
# Add the marketplace, then install the plugin:
/plugin marketplace add briandconnelly/codex-in-claude
/plugin install codex-in-claude
```

Then verify with `/codex:status` (free — no model call). The MCP server is launched on demand
via `uvx` from a pinned release tag, so updates are deliberate.

## Tools

**Active (call the model):**

- `codex_consult(question, …)` — read-only second opinion / answer.
- `codex_review_changes(scope, base, commit, paths, …)` — review `working_tree` / `branch` /
  `commit`; returns structured findings.
- `codex_delegate(task, …)` — implement a task in an isolated worktree; returns a reviewable
  `diff` that is **not** applied.

**Free (local only):**

- `codex_status` — readiness, version, auth, resolved defaults.
- `codex_dry_run(scope, …)` — preview a review's scope/diff size/redactions before spending.
- `codex_capabilities` — tool inventory + result fingerprint.

Slash commands wrap these: `/codex:status`, `/codex:consult`, `/codex:review`,
`/codex:delegate`, `/codex:dry-run`.

## Safety

- `consult` and `review` are strictly read-only.
- `delegate` lets Codex write, but only inside a throwaway git worktree seeded from your current
  tracked state; your working tree is never modified by the plugin. You review the returned diff
  and apply it yourself.
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
| `CODEX_IN_CLAUDE_GIT_TIMEOUT_SECONDS` | 60 | git command timeout |

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
