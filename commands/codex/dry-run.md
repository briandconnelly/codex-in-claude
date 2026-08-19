---
description: Preview the input a Codex review would assemble — scope, diff size, redactions (free)
argument-hint: "[working_tree|branch <base>|commit <sha>]"
---

Call the `codex_dry_run` MCP tool from the codex-in-claude server (free — no model
call) to preview what a `codex_review_changes` call would send.

Scope request: $ARGUMENTS

Map it to `scope`/`base`/`commit` as for /codex:review, and pass the absolute repo
path as `workspace_root`. Report the context summary (files/lines changed), the
prompt size, whether the diff would be truncated, and any redacted secret paths.

Say what the preview does not cover. It reports metadata about the input the plugin
would assemble; it does not invoke Codex, so it neither enumerates nor bounds the
files the model itself reads and sends during the paid run. Do not present a clean
preview as evidence that nothing sensitive will be sent.
