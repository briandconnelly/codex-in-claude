---
description: Ask Codex (a different model) for a read-only second opinion
argument-hint: "<question>"
---

Ask OpenAI Codex for an independent second opinion using the `codex_consult` MCP
tool from the codex-in-claude server.

Question: $ARGUMENTS

Pass the absolute repository path as `workspace_root` so Codex reasons about the
right project, and include any specific files or context the question needs as
`extra_context`. When the result comes back, treat Codex's findings as claims to
verify against the actual code — summarize what is worth acting on and flag
anything you disagree with.

For a high-reasoning-effort or broad repo-grounded consult that can exceed the synchronous
deadline (built-in default 300s), use `codex_consult_async` instead and poll for the
result — a sync call whose deadline expires loses the paid run.
