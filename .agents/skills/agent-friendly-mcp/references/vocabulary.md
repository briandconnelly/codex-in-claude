# Vocabulary

Shared terms used across `SKILL.md`, the contract checklist, the workflows, and the examples.

- **Discovery surface**: the union of definitions, summaries, and discovery primitives an agent can see before deciding which capability to invoke.
- **Concise vs detailed result**: a single structured default response, with an opt-in detail mode for richer content.
  Not a free `response_format` toggle.
- **Resource index**: a lightweight catalog of available resources with metadata sufficient to decide whether to read the body, distinct from the bodies themselves.
- **Prompt scaffold**: a reusable task starter that points at tools and resources, with explicit prerequisites and expected follow-on actions.
- **Task-completing tool vs composable primitive**: the granularity decision: one tool that completes a user task vs. several narrower tools the agent must chain.
- **Operational prerequisites**: auth scopes, workspace/project context, prior setup, or implicit state that affects capability availability, result shape, permissions, or repair.
- **Negotiated capability**: an optional MCP feature that both sides advertised during initialization; agents must not assume it exists from schema prose alone.
- **Root**: a client-declared filesystem boundary exposed through `roots/list`; useful for workspace-scoped servers, but guidance rather than access control.
- **Capability fingerprint**: a versioned identity for the server's surface, so clients can detect breaking changes.
- **Code-execution client**: an agent that writes code against the MCP server's surface (per "Code Execution with MCP") rather than calling tools directly.
- **Repair signal**: error fields that tell the agent specifically how to retry: stable code, offending field, allowed values, retryability, suggested next call.
- **State handle**: an opaque reference to server-side state, such as a job, cursor, or session, with declared lifetime and expiry behavior.
- **Long-running operation**: work that may need progress, cancellation, status polling, or result retrieval after the original request.
- **Task-capable tool**: a tool that supports the task-augmented request pattern, declared via `execution.taskSupport: "optional" | "required" | "forbidden"`, so clients can recover status and results after the original call returns.
