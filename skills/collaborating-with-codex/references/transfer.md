# Session transfer

Use `codex_transfer` only when the user wants to continue the current Claude Code conversation in a
resumable Codex thread. Transfer is free of model spend, but it is not read-only with respect to
Codex state: it creates a persistent thread in `$CODEX_HOME`.

Pass the current Claude session transcript as `transcript_path`. Claude Code normally stores project
transcripts as `.jsonl` files beneath `~/.claude/projects/<cwd-slug>/`; if multiple candidates could
be current, ask the user instead of guessing.

On success, use the tool-specific `thread_id`, `resume_command`, `source_path`, and transfer `meta`.
It does not return the active consult/review/delegate result shape. Run the returned resume command
when the user is ready to switch clients.

A growing live transcript is not idempotent: re-running transfer after more messages normally
creates another thread because only byte-identical imports deduplicate. Transfer once at the handoff
point. It is not a deliberation pattern because it moves the conversation rather than composing two
model roles.
