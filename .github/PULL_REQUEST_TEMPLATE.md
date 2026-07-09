<!-- Keep PRs focused. See CONTRIBUTING.md and AGENTS.md for conventions. -->

## What & why

<!-- One or two sentences: what this changes and the motivation. -->

Closes #

## Checklist

- [ ] Conventional commit title (`feat:` / `fix:` / `chore:` …).
- [ ] The gate passes — see [AGENTS.md → Tooling](../AGENTS.md#tooling). (If this PR touches
      `.github/workflows/`, the Actions-pinning check too.)
- [ ] If the agent-visible MCP surface changed — any category in `FINGERPRINT_COVERS`
      (`src/codex_in_claude/schemas.py`) — `FINGERPRINT` was bumped, the manifest snapshot
      regenerated, and `CHANGELOG.md` updated. Whether the change is *also* breaking is a separate
      call: see AGENTS.md → Versioning.
- [ ] If the CLI contract changed, `cli_contract.py` and `COMPATIBILITY.md` were updated.
- [ ] On a release: version bumped together across `pyproject.toml`, `.claude-plugin/plugin.json`,
      the `codex-in-claude==X.Y.Z` pin in `.mcp.json`, and `CHANGELOG.md`.

## Notes for reviewers

<!-- Anything non-obvious: tradeoffs, follow-ups, things to look at closely. -->
