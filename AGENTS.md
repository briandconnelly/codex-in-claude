# Agent working conventions

Conventions for any agent (or human) working in this repository.

## What this is

A Claude Code plugin that calls the OpenAI Codex CLI via a FastMCP server. The Python package
is `codex_in_claude` under `src/`. Generic, CLI-agnostic machinery lives in
`codex_in_claude/_core/` and is designed for later extraction into a shared `agent-bridge`
package — **`_core` must never import from its parent package** (one-way dependency).

## Tooling

- Use `uv` for everything: `uv sync`, `uv run pytest`, `uv run <cmd>`. Never pip/poetry.
- Lint/format with `ruff`; type-check with `ty`. All three must pass before a change is done:
  `uv run ruff check . && uv run ruff format --check . && uv run ty check`.
- Tests use `pytest` with a **95% coverage floor**. Live tests that call the real `codex` CLI are
  marked `integration` and excluded by default; run them with `uv run pytest -m integration --no-cov`.

## The CLI contract

Every assumption about the `codex` CLI lives in `src/codex_in_claude/cli_contract.py` — flags,
sandbox values, version, drift/auth signatures. Guarantee-bearing flags (`ALWAYS_SEND_FLAGS`) are
sent unconditionally and, if rejected, fail loudly as `cli_contract_changed` (zero spend).
Depth-only flags (`HELP_GATED_FLAGS`) are feature-detected and dropped gracefully. When Codex
changes, update that one file; see `COMPATIBILITY.md`.

## The result contract

All tools return the envelope in `src/codex_in_claude/schemas.py`. Bump `FINGERPRINT` whenever the
agent-visible surface changes (tool names, params, error codes, value enums). Keep the change in
`CHANGELOG.md`.

## Release coordination

Bump together: `pyproject.toml` version, `.claude-plugin/plugin.json`, the `@vX.Y.Z` tag in
`.mcp.json`, `README.md`, `CHANGELOG.md`, and `FINGERPRINT` when the surface changed.

## Git / PRs

- Conventional commits (`feat:`, `fix:`, `chore:`, …).
- Branch for feature work; do not commit directly to the default branch.
- Don't add `pull_request_target` workflows or self-approve reviews.
