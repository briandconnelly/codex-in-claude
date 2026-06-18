# codex-in-claude

Call **OpenAI Codex** from **Claude Code** for delegation, code review, and second opinions —
a FastMCP plugin that drives the `codex` CLI safely.

> Mirror image of [`cc-plugin-codex`](https://github.com/briandconnelly/cc-plugin-codex)
> (which lets Codex call Claude Code).

## Status

Early development. See [the design plan](#) and `CHANGELOG.md`.

## What it does

A Claude Code session can hand Codex a task and get back results, with a **safe-by-default**
posture:

| Tier | Codex sandbox | Where edits go | Use for |
|------|---------------|----------------|---------|
| `consult` | `read-only` | nothing — text/findings only | questions, second opinions |
| `propose` | `workspace-write` (temp git worktree) | isolated worktree → returns a **reviewable diff, never auto-applied** | delegating a coding task |
| `apply` | `workspace-write` (live tree) | live working tree, in place | explicit opt-in (later milestone) |

Plus a native `codex review` path and disk-backed background jobs.

## Requirements

- The [`codex` CLI](https://developers.openai.com/codex/cli) on `PATH`, authenticated
  (`codex login`).
- Python 3.11+ (the MCP server is launched via `uvx`).

More documentation lands as the plugin matures.
