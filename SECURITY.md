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

The plugin redacts secret-looking files and inline values from diffs it gathers
(`pontonier.core.redaction`).
This is **defense-in-depth, not a guarantee**:

- It only covers the diff text the server gathers. During any active call — consult, review, or
  delegate — Codex may read files in the workspace itself, and it pulls in context you never asked
  for: the workspace's `AGENTS.md` is auto-loaded, and skills under `.agents/skills/` **plus your
  user-global skills under `$CODEX_HOME/skills/`** (default `~/.codex/skills/`) are auto-discovered.
  The two arrive differently. `AGENTS.md` content is in the model's context before the turn starts.
  A skill contributes only its **name and description** up front — but that is enough for the model
  to select it, and selecting it makes the model read the **body**, which then reaches OpenAI too.
  A global skill's body has been observed coming back from a prompt that named neither the skill nor
  the file. Redaction covers none of it.
- For workspaces that may contain live credentials, keep secrets out of the tree and review what
  you delegate. `isolation=ignore-config`/`ignore-rules` helps only for the *specific* `$CODEX_HOME`
  state it names (`config.toml`, execpolicy `.rules`); it does **not** suppress `AGENTS.md`
  auto-loading or `.agents/skills/` skill discovery, and — despite the flag's name — it does **not** suppress
  `$CODEX_HOME/skills/` either (see `COMPATIBILITY.md`). Anything private in a user-global Codex
  skill is eligible for egress on any active call, whatever workspace you target.

## Untrusted content

The question, task, diff, and any context sent to Codex are framed as untrusted data, with explicit
instructions not to follow embedded directives or exfiltrate secrets. This mitigates but does not
eliminate prompt-injection risk; treat Codex's output as claims to verify.
