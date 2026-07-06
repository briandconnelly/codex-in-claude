---
description: Hand off the current Claude Code session to a resumable Codex thread
---

Continue this Claude Code conversation inside Codex by importing its transcript into a
resumable Codex thread. This is free — no model call, no token spend — but it does
create a thread in `$CODEX_HOME`.

1. Locate the current session's transcript: the newest `*.jsonl` file under
   `~/.claude/projects/<slug>/`, where `<slug>` is the current working directory with
   `/` replaced by `-` (e.g. `/Users/me/proj` → `-Users-me-proj`). If several are
   plausible or you are unsure which is the active session, ask the user to confirm the
   path rather than guessing.
2. Call the `codex_transfer` MCP tool from the codex-in-claude server with
   `transcript_path` set to that absolute path.
3. On success, print the returned `resume_command` (`codex resume <thread_id>`) so the
   user can open the imported conversation in the Codex TUI or App.
4. On failure, branch on `error.code` and show `error.repair` — e.g. `transfer_unsupported`
   means the installed Codex is too old (update it); `transfer_incomplete` means Codex
   recorded no thread (retry, or fall back to `codex resume`'s interactive picker).

Note: transferring a still-active session creates a new thread each time you run it —
Codex only deduplicates a byte-identical transcript — so re-running mid-session is
expected to produce a fresh thread, not the same one.
