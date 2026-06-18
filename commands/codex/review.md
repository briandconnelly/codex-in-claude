---
description: Have Codex review your git changes and return structured findings
argument-hint: "[working_tree|branch <base>|commit <sha>]"
---

Use the `codex_review_changes` MCP tool from the codex-in-claude server to get an
independent code review from Codex.

Scope request: $ARGUMENTS

Map the request to the tool's parameters: default `scope=working_tree`; for a branch
review pass `scope=branch` and `base=<branch>`; for a single commit pass
`scope=commit` and `commit=<sha>`. Pass the absolute repo path as `workspace_root`.
Optionally call `codex_dry_run` first (free) to preview the scope and diff size.

When findings come back, verify each one against the actual code before presenting
it — note which you confirm and which you think are false positives.
