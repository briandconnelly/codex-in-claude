<!-- Keep PRs focused. See CONTRIBUTING.md and AGENTS.md for conventions. -->

## What & why

<!-- One or two sentences: what this changes and the motivation. -->

Closes #

## Checklist

- [ ] Conventional commit title (`feat:` / `fix:` / `chore:` …).
- [ ] `uv run ruff check . && uv run ruff format --check . && uv run ty check` passes.
- [ ] `uv run pytest` passes (≥95% coverage).
- [ ] If the agent-visible MCP surface changed (tool names, params, error codes, value enums),
      `FINGERPRINT` was bumped and `CHANGELOG.md` updated.
- [ ] If the CLI contract changed, `cli_contract.py` and `COMPATIBILITY.md` were updated.
- [ ] On a release: version bumped together across `pyproject.toml`, `.claude-plugin/plugin.json`,
      the `codex-in-claude==X.Y.Z` pin in `.mcp.json`, and `CHANGELOG.md`.

## Notes for reviewers

<!-- Anything non-obvious: tradeoffs, follow-ups, things to look at closely. -->
