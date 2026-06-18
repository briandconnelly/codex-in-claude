---
description: Preview what a Codex review would send — scope, diff size, redactions (free)
argument-hint: "[working_tree|branch <base>|commit <sha>]"
---

Call the `codex_dry_run` MCP tool from the codex-in-claude server (free — no model
call) to preview what a `codex_review_changes` call would send.

Scope request: $ARGUMENTS

Map it to `scope`/`base`/`commit` as for /codex:review, and pass the absolute repo
path as `workspace_root`. Report the context summary (files/lines changed), the
prompt size, whether the diff would be truncated, and any redacted secret paths.
