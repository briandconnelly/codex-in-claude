# Security

## Reporting a vulnerability

Please report security issues privately via GitHub's "Report a vulnerability" (Security advisories)
on this repository rather than opening a public issue.

## Security model

- **Read-only by default.** `codex_consult` and `codex_review_changes` run Codex with the
  `read-only` sandbox; Codex cannot modify files.
- **Your working tree is never edited.** `codex_delegate` runs Codex with `workspace-write` in a
  throwaway git worktree seeded from your current tracked state. The worktree does not bound
  Codex's writes: codex's workspace-write sandbox also lets commands write the OS temp roots
  (`/tmp` and `$TMPDIR`) by default, and those writes are neither captured in the returned diff
  nor cleaned up (see `COMPATIBILITY.md`). The plugin never modifies your working tree; it
  returns a diff for you to review and apply yourself.
- **No sandbox bypass.** The plugin never passes `--dangerously-bypass-approvals-and-sandbox` or
  `--dangerously-bypass-hook-trust`.
- **Prompt on stdin.** Prompts and gathered diffs are passed to `codex` over stdin, not argv, so
  they do not appear in local process listings.

## Secret redaction is best-effort

The plugin redacts secret-looking files and inline values from diffs it gathers
(`pontonier.core.redaction`).
This is **defense-in-depth, not a guarantee**:

- It only covers the diff text the server gathers. During any active call — consult, review, or
  delegate — Codex may read files **outside** the workspace, up to everything the OS user running
  codex can read: `--sandbox read-only` bounds writes, not reads, so no choice of workspace is a
  read boundary. Codex also pulls in context you never asked for: `AGENTS.md` is auto-loaded from the resolved workspace, from **every ancestor directory up to
  the repository root** when the workspace is in a repository, and from a **user-global
  `$CODEX_HOME/AGENTS.override.md`, else `$CODEX_HOME/AGENTS.md`** on every call whatever workspace
  you target; and skills under `.agents/skills/` **plus your
  user-global skills under `$CODEX_HOME/skills/`** (default `~/.codex/skills/`) are auto-discovered.
  The two arrive differently. `AGENTS.md` content is in the model's context before the turn starts.
  A skill contributes only its **name and description** up front — but that is enough for the model
  to select it, and selecting it makes the model read the **body**, which then reaches OpenAI too.
  A global skill's body has come back from a prompt that named neither the skill nor the file
  (observed on codex-cli 0.147.0 and again on 0.148.0; the probe, its dates, and the verified negatives live in
  [`COMPATIBILITY.md`](COMPATIBILITY.md) § "Implicit Codex context", the single home for all of it).
  Redaction covers none of it.
  `isolation=ignore-config`/`ignore-rules` helps only for the *specific* `$CODEX_HOME` state it
  names (`config.toml`, execpolicy `.rules`); it does **not** suppress `AGENTS.md` auto-loading or
  `.agents/skills/` skill discovery, and — despite the flag's name — it does **not** suppress
  `$CODEX_HOME/skills/` or the user-global `$CODEX_HOME` guidance file either (see
  `COMPATIBILITY.md`). Anything private in a user-global Codex
  skill **or in a user-global `$CODEX_HOME/AGENTS.md`** is eligible for egress on any active call,
  whatever workspace you target.
- **Keep secrets out of any tree you point Codex at — and note that is necessary, not
  sufficient.** Codex's reads are not bounded by that tree, so a prompt-injected repository can
  direct them at files elsewhere on the machine. Redaction is not a substitute for either.
- **Review what you delegate.** Read the task you send and the diff that comes back.

## Untrusted content

The question, task, diff, and any context sent to Codex are framed as untrusted data, with explicit
instructions not to follow embedded directives or exfiltrate secrets. This mitigates but does not
eliminate prompt-injection risk; treat Codex's output as claims to verify.
