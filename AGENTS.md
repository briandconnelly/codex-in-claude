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
- Local Git hooks are configured in `prek.toml` and run via [`prek`](https://prek.j178.dev) (a dev
  dependency). One-time setup: `uv run prek install --prepare-hooks`. Hooks mirror the CI gate —
  pre-commit runs file hygiene + `ruff`/`ty`/Actions-pinning/`uv lock --check`; pre-push runs
  `pytest`; commit-msg validates Conventional Commits via `scripts/check_commit_message.py` (its
  allowed types/scopes mirror the Git/PRs section — change both together). prek is a local
  convenience; CI (`test.yml`) remains the authoritative gate and does not run the builtin
  file-hygiene hooks.

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

## Versioning

- Semantic Versioning. **Pre-1.0:** a minor bump may change the agent-visible surface (a breaking
  change is a minor, not a major); a patch is a bug fix or internal change. Post-1.0, breaking
  changes are majors.
- A change to the agent-visible surface (tool names, params, error codes, value enums) bumps
  `FINGERPRINT` and is flagged as a breaking change (commit `!`/`BREAKING CHANGE:` footer, plus the
  `breaking-change` label on the PR).
- `CHANGELOG.md` follows Keep a Changelog: land every notable change under `## [Unreleased]`; cutting
  a release moves those entries into a new dated version section and leaves a fresh, empty
  `## [Unreleased]` on top. See Release coordination for the version-bump set.

## Release coordination

Bump together: `pyproject.toml` version, `.claude-plugin/plugin.json`, the `codex-in-claude==X.Y.Z`
PyPI pin in `.mcp.json`, `CHANGELOG.md`, and `FINGERPRINT` when the surface changed. (`README.md` carries no
pinned version literal — it uses a dynamic PyPI badge and marketplace install — so it needs no bump.)
See `docs/RELEASING.md` for the full release procedure and the one-time PyPI/GitHub setup.

**The lockstep version bump belongs only in the dedicated `chore: release X.Y.Z` PR — never in a
feature/fix PR.** Feature and fix PRs change `FINGERPRINT` (when the surface changed) and add their
entry under `## [Unreleased]`, but leave the three version literals — `pyproject.toml`,
`.claude-plugin/plugin.json`, and the `.mcp.json` pin — at the current released version. (`uv.lock`
is not a version source and still changes freely in feature PRs when dependencies move; its own
`codex-in-claude` `version` line is a derived mirror of `pyproject.toml` that `uv lock` refreshes as
part of the release PR.) The release PR is the *only* place those three literals move, and it is
merged immediately before the tag/publish. The reason is the `.mcp.json` pin (`codex-in-claude==X.Y.Z`):
the moment it lands on `main`, that version must already exist on PyPI, or a plugin install from
`main` hits an unresolvable pin. Bumping it in a feature PR opens that broken-pin window for the
entire gap until the release ships. So a release is two PRs: the work lands under `## [Unreleased]`
(no version-literal change), then a `chore: release` PR does the lockstep bump plus the
`## [Unreleased]` → `## [X.Y.Z] - YYYY-MM-DD` rollover.

## Python support

`requires-python>=3.11`, following SPEC 0 (support Python releases from roughly the last three
years). CI runs the gate on every supported minor. The supported set is defined by the Python trove
classifiers in `pyproject.toml`; a packaging test asserts the CI matrix in
`.github/workflows/test.yml` (the reusable gate called by both `ci.yml` and `publish.yml`) and the
`requires-python` floor stay in lockstep with those classifiers
(so this prose deliberately avoids naming specific versions). Changing the support set is
deliberate: update the classifiers, the CI matrix, and `requires-python` together, and note it in
`CHANGELOG.md`.

## Testing

- TDD: write the failing test first, then the minimal code to pass it.
- Test files mirror the module under test (`tests/test_<module>.py`); every bug fix lands with a
  regression test that fails before the fix.
- The 95% coverage floor is enforced in CI. Live tests that hit the real `codex` CLI are marked
  `integration` and excluded by default (`uv run pytest -m integration --no-cov`).

## Git / PRs

- **Conventional Commits** for every commit and PR title. Allowed types: `feat`, `fix`, `chore`,
  `docs`, `refactor`, `test`, `perf`, `ci`, `build`, `revert`. Optional scope from the codebase
  areas: `jobs`, `cli-contract`, `core`, `tools`, `schemas`, `worktree`, `packaging`, `config`
  (e.g. `feat(jobs): add async lifecycle`). Subject is imperative, lowercase, no trailing period.
  Mark breaking changes with `!` (`feat!:`) or a `BREAKING CHANGE:` footer (see Versioning).
- **Squash-merge only.** A PR becomes a single commit whose subject is the PR title, so **the PR
  title must itself be a valid Conventional Commit**. Keep PRs focused on one logical change.
- Branch names are `<type>/<slug>` matching the commit type (e.g. `feat/async-jobs`, `docs/conventions`).
- Branch for feature work; do not commit directly to the default branch. Link the issue in the PR
  body (`Closes #N`); label the PR with a type and (for issues) a priority.
- Preserve `Co-authored-by:` trailers (pairing, agent attribution) — they must survive the squash.
- **Agents never merge PRs; the maintainer merges.** An agent may merge only on an explicit,
  in-session instruction to merge that specific PR. Open the PR, get checks green, and stop.
- Don't add `pull_request_target` workflows or self-approve reviews. After pushing new commits to a
  PR that was already reviewed, request fresh review rather than relying on the stale approval.
- **Copilot reviews each PR on open and on every push** — the repo enables Copilot review-on-push
  (a `copilot_code_review` ruleset rule), and merging requires every review thread resolved
  (`required_review_thread_resolution`). Treat that feedback like any review: evaluate each comment on
  its merits — verify it against the code, don't blindly implement — fix what's valid, and reply to
  each comment noting the resolution. Because each push re-triggers a review, iterate until it reports
  no new actionable comments, then resolve the threads before merging. A comment you decline (e.g. a
  false positive) still gets a reply explaining why, and its thread still needs resolving.
