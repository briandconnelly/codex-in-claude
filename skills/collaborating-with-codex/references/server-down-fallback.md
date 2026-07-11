# Server-down fallback

Use this fallback only after an MCP transport error shows the stdio server is unavailable.

First ask the user to reconnect or restart the `codex-in-claude` MCP server, then confirm recovery
with free `codex_status`. The plugin path is preferred because it supplies workspace-aware diff
gathering, bounded input, best-effort redaction, and structured results.

While the server remains down, a one-off read-only consult or review may use:

```sh
codex exec --sandbox read-only --skip-git-repo-check -
```

Send the prompt on stdin. Before doing so, gather, bound, and sanitize context yourself. This direct
CLI route sends raw input, has no plugin result envelope, and does not provide the plugin's diff
gathering or redaction protections. Treat the text response as an unverified claim.

Never construct a writable CLI fallback for delegation. Restore the MCP server for isolated
propose-tier work. Do not repeatedly retry either route while the transport or setup condition is
unchanged.
