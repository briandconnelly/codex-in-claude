# Changelog

All notable changes to this project are documented here. Pre-1.0, minor versions may change the
agent-visible MCP surface; the result `fingerprint` changes when they do.

## [Unreleased]

### Added
- Initial release: a Claude Code plugin that calls the OpenAI Codex CLI via a FastMCP server.
- Tools: `codex_consult` (read-only second opinion), `codex_review_changes` (structured review of
  working_tree/branch/commit), `codex_delegate` (propose tier — implements a task in an isolated
  git worktree and returns a reviewable diff that is not applied), plus free `codex_status`,
  `codex_dry_run`, and `codex_capabilities`.
- Slash commands: `/codex:status`, `/codex:consult`, `/codex:review`, `/codex:delegate`,
  `/codex:dry-run`.
- `collaborating-with-codex` guidance skill.
- Driven by `codex exec` (not the experimental app-server protocol); centralized CLI contract,
  graceful flag gating, secret redaction, and an isolated-worktree delegation workflow.
- Result fingerprint: `codex-in-claude/0.1/schema-3`.
