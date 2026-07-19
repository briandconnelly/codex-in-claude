---
description: Delegate a coding task to Codex; get back a reviewable diff (not applied)
argument-hint: "<task description>"
---

Delegate a coding task to OpenAI Codex using the `codex_delegate` MCP tool from the
codex-in-claude server.

Task: $ARGUMENTS

Pass the absolute repository path as `workspace_root`. Codex implements the task in
an isolated git worktree and returns a `diff` — it does NOT touch the working tree.

For a substantial or multi-file task that can exceed the synchronous deadline
(built-in default 300s), use `codex_delegate_async` instead and poll for the result —
a sync call whose deadline expires loses the paid run.

When the result returns:
1. Show the proposed `diff` and Codex's `summary`.
2. Review the diff for correctness yourself.
3. Apply it to the working tree (using your own edit tools) only if it is correct —
   and tell the user you are about to, or ask first if it is a significant change.
Do not apply a diff you have not reviewed.
