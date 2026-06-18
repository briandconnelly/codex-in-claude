# Changelog

All notable changes to this project are documented here. Pre-1.0, minor versions may change the
agent-visible MCP surface; the result `fingerprint` changes when they do.

## [Unreleased]

### Fixed
- `codex_delegate`/`codex_delegate_async` no longer risk attributing the caller's pre-existing
  uncommitted changes to Codex. If the throwaway worktree's baseline commit cannot be finalized
  after the live patch applies (`git add`/`git commit` failure, or a non-clean tree afterward),
  the run now fails fast with a structured `worktree_error` **before** any Codex call (zero spend)
  and the partial worktree is cleaned up, instead of silently mixing live changes into the
  returned diff. The agent-visible surface is unchanged, so `FINGERPRINT` is not bumped. (#4)

### Added
- Initial release: a Claude Code plugin that calls the OpenAI Codex CLI via a FastMCP server.
- Tools: `codex_consult` (read-only second opinion), `codex_review_changes` (structured review of
  working_tree/branch/commit), `codex_delegate` (propose tier — implements a task in an isolated
  git worktree and returns a reviewable diff that is not applied), plus free `codex_status`,
  `codex_dry_run`, and `codex_capabilities`.
- Background jobs (M4): `codex_delegate_async` runs the propose tier detached and returns a
  `job_id` immediately, with free lifecycle tools `codex_job_status`, `codex_job_result`,
  `codex_job_consume_result`, `codex_job_cancel`, and `codex_job_list`. Job state is disk-backed
  under the state dir, survives MCP server restarts, reconciles dead workers via PID liveness, and
  is bounded by a wall-clock deadline plus TTL and per-workspace count-cap eviction.
- Config knobs: `CODEX_IN_CLAUDE_JOB_TTL`, `CODEX_IN_CLAUDE_JOB_MAX_SECONDS`,
  `CODEX_IN_CLAUDE_JOB_MAX_COUNT` (alongside the existing `CODEX_IN_CLAUDE_STATE_DIR`).
- Slash commands: `/codex:status`, `/codex:consult`, `/codex:review`, `/codex:delegate`,
  `/codex:delegate-async`, `/codex:dry-run`.
- `collaborating-with-codex` guidance skill.
- Driven by `codex exec` (not the experimental app-server protocol); centralized CLI contract,
  graceful flag gating, secret redaction, and an isolated-worktree delegation workflow.
- Result fingerprint: `codex-in-claude/0.1/schema-1`.
