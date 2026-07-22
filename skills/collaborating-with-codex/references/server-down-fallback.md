# Server-down fallback

Use this fallback only after an MCP transport error shows the stdio server is unavailable.

First ask the user to reconnect or restart the `codex-in-claude` MCP server, then confirm recovery
with free `codex_status`. The plugin path is preferred because it supplies workspace-aware diff
gathering, bounded input, best-effort redaction, and structured results.

While the server remains down, a one-off read-only consult or review may use:

```sh
codex exec \
  --sandbox read-only \
  --ephemeral \
  --ignore-user-config \
  --ignore-rules \
  --disable remote_plugin \
  --cd "$WORKSPACE" \
  --skip-git-repo-check -
```

Send the prompt on stdin.

- Keep every flag; if `codex` rejects any of them, stop and surface the CLI drift — never drop a
  flag to make the command run. (Together they apply the plugin's guarantee-bearing flags at its
  strictest config isolation — no persisted session, no `$CODEX_HOME/config.toml`, no execpolicy
  rules, no remote-plugin connectors, an explicit working root instead of the ambient directory.
  `--ignore-user-config` drops that one config file, not user-level Codex state generally: skills
  under `$CODEX_HOME/skills/` still load. The plugin itself sends the two config-isolation flags
  only when the operator raises isolation above the default `inherit`.)
- Set `WORKSPACE` to a directory the user approved for disclosure.

Even with these flags, Codex auto-loads the resolved workspace's `AGENTS.md` and `.agents/skills/`
skills, discovers your user-global skills under `$CODEX_HOME/skills/`, and may read other files.
An empty scratch `WORKSPACE` removes the ambient *repository* context but not the user-global
skills — those are discovered from outside the workspace, so no `WORKSPACE` choice excludes them.
It is not a read boundary either: the read-only sandbox bounds writes, not reads, so Codex can
still read files at other absolute paths. If nothing beyond the sanitized stdin prompt may be
visible to Codex, do not use this fallback at all.

Before sending, gather, bound, and sanitize context yourself. This direct CLI route sends raw input,
has no plugin result envelope, and does not provide the plugin's diff gathering or redaction
protections. Treat the text response as an unverified claim.

Never construct a writable CLI fallback for delegation. Restore the MCP server for isolated
propose-tier work. Do not repeatedly retry either route while the transport or setup condition is
unchanged.
