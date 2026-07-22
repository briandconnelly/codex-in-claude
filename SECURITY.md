# Security

## Reporting a vulnerability

Please report security issues privately via GitHub's "Report a vulnerability" (Security advisories)
on this repository rather than opening a public issue.

## Security model

- **Read-only by default.** `codex_consult` and `codex_review_changes` run Codex with the
  `read-only` sandbox; Codex cannot modify files.
- **Writes are isolated.** `codex_delegate` runs Codex with `workspace-write` but only inside a
  throwaway git worktree seeded from your current tracked state. The plugin never modifies your
  working tree; it returns a diff for you to review and apply yourself.
- **No sandbox bypass.** The plugin never passes `--dangerously-bypass-approvals-and-sandbox` or
  `--dangerously-bypass-hook-trust`.
- **Prompt on stdin.** Prompts and gathered diffs are passed to `codex` over stdin, not argv, so
  they do not appear in local process listings.

## Secret redaction is best-effort

The plugin redacts secret-looking files and inline values from diffs it gathers (`_core/redaction.py`).
This is **defense-in-depth, not a guarantee**:

- It only covers the diff text the server gathers. During any active call — consult, review, or
  delegate — Codex may read files in the workspace itself, and it auto-loads the workspace's
  `AGENTS.md` and `.agents/skills/` skills **plus your user-global skills under
  `$CODEX_HOME/skills/`** (default `~/.codex/skills/`) even if your prompt never mentions them;
  redaction does not cover what Codex reads or auto-loads directly.
- For workspaces that may contain live credentials, keep secrets out of the tree and review what
  you delegate. `isolation=ignore-config`/`ignore-rules` helps only for the *specific* `$CODEX_HOME`
  state it names (`config.toml`, execpolicy `.rules`); it does **not** suppress `AGENTS.md` or
  `.agents/skills/` auto-loading, and — despite the flag's name — it does **not** suppress
  `$CODEX_HOME/skills/` either (see `COMPATIBILITY.md`). Anything private in a user-global Codex
  skill is eligible for egress on any active call, whatever workspace you target.

## Untrusted content

The question, task, diff, and any context sent to Codex are framed as untrusted data, with explicit
instructions not to follow embedded directives or exfiltrate secrets. This mitigates but does not
eliminate prompt-injection risk; treat Codex's output as claims to verify.
