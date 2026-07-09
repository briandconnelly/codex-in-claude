# Agent working conventions

Conventions for any agent (or human) working in this repository.

## What this is

A Claude Code plugin that calls the OpenAI Codex CLI via a FastMCP server. The Python package
is `codex_in_claude` under `src/`. Generic, CLI-agnostic machinery lives in
`codex_in_claude/_core/` and is designed for later extraction into a shared `agent-bridge`
package.

- **Rule:** `_core` must never import from its parent package (one-way dependency; this is what
  keeps it extractable).

## Tooling

- Use `uv` for everything: `uv sync`, `uv run pytest`, `uv run <cmd>`. Never pip/poetry.
- **The gate.** This is the repo's single definition of it; every other doc links here rather than
  restating it. A change is not done until it passes:

  ```sh
  uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest
  ```

  If you touched `.github/workflows/`, also run `uv run python scripts/check_github_actions_pinning.py`
  — CI runs it ahead of the four above, and nothing else surfaces an unpinned action.
  CI (`test.yml`) is the authoritative gate and runs all of this on every supported Python.
- Tests use `pytest`; the coverage floor and integration-test markers are defined in Testing below.
- Local Git hooks are configured in `prek.toml` and run via [`prek`](https://prek.j178.dev) (a dev
  dependency). One-time setup: `uv run prek install --prepare-hooks`. Hooks mirror the CI gate —
  pre-commit runs file hygiene + `ruff`/`ty`/Actions-pinning/`uv lock --check`; pre-push runs
  `pytest`; commit-msg validates Conventional Commits via `scripts/check_commit_message.py`. prek
  is a local convenience; CI (`test.yml`) remains the authoritative gate and does not run the
  builtin file-hygiene hooks.
- When changing the allowed commit types or scopes, update `scripts/check_commit_message.py` and
  the Git/PRs section below in the same change — they mirror each other.

## The CLI contract

Every assumption about the `codex` CLI lives in `src/codex_in_claude/cli_contract.py` — flags,
sandbox values, version, drift/auth signatures. Guarantee-bearing flags (`ALWAYS_SEND_FLAGS`) are
sent unconditionally and, if rejected, fail loudly as `cli_contract_changed` (zero spend).
Depth-only flags (`HELP_GATED_FLAGS`) are feature-detected and dropped gracefully. When Codex
changes, update that one file; see `COMPATIBILITY.md`.

## The result contract

All tools return the envelope in `src/codex_in_claude/schemas.py`. Bump `FINGERPRINT` whenever the
agent-visible surface changes — any externally observable change to a category in
`FINGERPRINT_COVERS` (same file; the Versioning section has the decision rules). A
committed manifest snapshot (`tests/fixtures/manifest_snapshot.json`, guarded by
`tests/test_manifest.py`) fails CI on any covered change, so the change can't land unreviewed: the
failure directs you to regenerate the fixture
(`uv run python -m codex_in_claude.manifest > tests/fixtures/manifest_snapshot.json`) and bump
`FINGERPRINT` in the same commit. The snapshot is an **acknowledgment guard** — it surfaces the
drift for review; it does not mechanically force the bump (the snapshot and `FINGERPRINT` are
independently editable, so bumping remains review policy). Record the change in `CHANGELOG.md`.

## Versioning

- Semantic Versioning. **Pre-1.0:** a minor bump may change the agent-visible surface (a breaking
  change is a minor, not a major); a patch is a bug fix or internal change. Post-1.0, breaking
  changes are majors.
- Every change is judged on **two independent questions**:
  - **Bumps `FINGERPRINT`?** Yes for any *externally observable* change to a category in
    `FINGERPRINT_COVERS` (`src/codex_in_claude/schemas.py`) — the discovered value, shape, or
    documented meaning of anything in that tuple. Reference the tuple by name rather than
    re-listing its categories in prose — in this document or **any other doc, template, or comment
    in the repo** (a re-listing drifting out of sync with the code is the exact bug this rule
    exists to prevent, and it has happened: #227 removed one such copy here, while copies in
    `CONTRIBUTING.md`, `docs/UPGRADING-CODEX.md`, and the PR template survived and went stale).
    A refactor that leaves the discovered surface byte-identical does not bump it.
  - **Breaking?** Flag it breaking (commit `!`/`BREAKING CHANGE:` footer + the `breaking-change` PR
    label) only when the change is *backward-incompatible* for a client: removing or renaming a
    field/tool/resource/prompt, retyping a field, adding a required input, narrowing an accepted
    value set or enum, changing an output field's meaning under a closed schema, or weakening a
    documented guarantee (an annotation or a promised semantic). Backward-compatible additions and
    wording-only rewords are not breaking.
  - Every breaking change is also a `FINGERPRINT` bump; not every bump is breaking (so #198, a
    wording-only reword, correctly bumped `FINGERPRINT` with no `!`, and #193's `!` was over-flagging
    — the safe direction). Quick reference:

    | Change | Bumps `FINGERPRINT` | Breaking |
    |---|---|---|
    | Add a backward-compatible tool, param, resource, prompt, field, error code, or enum value | Yes | No |
    | Remove/rename/retype a field/tool/resource/prompt, add a required input, or narrow a value set | Yes | Yes |
    | Reword a description/instruction, no guarantee change | Yes | No |
    | Reword text that weakens a documented guarantee | Yes | Yes |
    | Change a `_REPAIR_BY_CODE` machine field (`next_step`'s `RepairStep`, `repair.tool`, `temporary`) | Yes | Per the rules above |
    | Change human-readable `_REPAIR_BY_CODE`/`error.message` prose only | No | No |
    | Internal refactor, discovered surface unchanged | No | No |

    The last two rows are why #197 (repair-hint prose only) bumped neither: that prose ships fresh in
    each error envelope and is absent from the manifest snapshot, so no cached discovery surface
    changed. The exemption is *only* for the human-readable message/prose text — the co-located
    machine-readable repair fields remain part of the discovered surface.
- `CHANGELOG.md` follows Keep a Changelog: land every notable change under `## [Unreleased]`; cutting
  a release moves those entries into a new dated version section and leaves a fresh, empty
  `## [Unreleased]` on top. See Release coordination for the version-bump set.

## Release coordination

The release PR bumps three version literals in lockstep — `pyproject.toml` version,
`.claude-plugin/plugin.json`, and the `codex-in-claude==X.Y.Z` PyPI pin in `.mcp.json` — and rolls
`CHANGELOG.md`'s `## [Unreleased]` into a dated section. `FINGERPRINT` is **not** part of the release
bump: it moves in the feature/fix PRs that change the agent-visible surface (see Versioning), and the
release PR only verifies it already reflects everything shipping. (`README.md` carries no pinned
version literal — it uses a dynamic PyPI badge and marketplace install — so it needs no bump.) See
`docs/RELEASING.md` for the full release procedure and the one-time PyPI/GitHub setup.

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
- Test files mirror the module under test (`tests/test_<module>.py`).
- Every bug fix lands with a regression test that fails before the fix.
- The **95% coverage floor** is enforced in CI. Live tests that hit the real `codex` CLI are marked
  `integration` and excluded by default (`uv run pytest -m integration --no-cov`).

## Git / PRs

- **Conventional Commits** for every commit and PR title. Allowed types: `feat`, `fix`, `chore`,
  `docs`, `refactor`, `test`, `perf`, `ci`, `build`, `revert`. Optional scope from the codebase
  areas: `jobs`, `cli-contract`, `core`, `tools`, `schemas`, `worktree`, `packaging`, `config`
  (e.g. `feat(jobs): add async lifecycle`). Subject is imperative, lowercase, no trailing period.
  Mark breaking changes with `!` (`feat!:`) or a `BREAKING CHANGE:` footer (see Versioning).
- **Squash-merge only.** A PR becomes a single commit whose subject is the PR title, so **the PR
  title must itself be a valid Conventional Commit**. Keep each PR to one logical change — if the
  title needs an "and", split the PR.
- Branch names are `<type>/<slug>` matching the commit type (e.g. `feat/async-jobs`, `docs/conventions`).
- **Claim an issue before working it, and never work one assigned to someone else.** Only work an
  issue that has no assignees or where you are the sole assignee. Before starting, check the
  assignees (`gh issue view ISSUE_NUMBER --json assignees,title`); if anyone else is assigned, stop — do not
  start work. To claim an unassigned issue, self-assign (`gh issue edit ISSUE_NUMBER --add-assignee @me`),
  then re-check (`gh issue view ISSUE_NUMBER --json assignees`) and begin only if you are the sole assignee —
  `--add-assignee` is additive and will not fail if someone claimed the issue first, so the re-check
  is what closes that race.
- Branch for feature work; do not commit directly to the default branch. Link the issue in the PR
  body (`Closes #N`); label the PR with a type and (for issues) a priority.
- Preserve `Co-authored-by:` trailers (pairing, agent attribution) — they must survive the squash.
- **Agents never merge PRs; the maintainer merges.** An agent may merge only on an explicit,
  in-session instruction to merge that specific PR. Open the PR, get checks green, and stop.
- Don't add `pull_request_target` workflows.
- Don't self-approve reviews.
- After pushing new commits to a PR that was already reviewed, request fresh review rather than
  relying on the stale approval.
- **Whether Copilot reviews a PR depends on who authored it**, but merging always requires every
  review thread resolved (`required_review_thread_resolution`).
  - **Human-authored PRs** get an automatic Copilot review on open and on every push (the
    `copilot_code_review` ruleset rule).
  - **Bot-authored PRs** — including every PR opened under the `briandconnelly-agent[bot]` identity
    — get **no automatic review**: the ruleset rule skips authors that hold no Copilot seat. The
    workflow that exists to close that gap, `.github/workflows/copilot-review-bot-prs.yml`, does
    not currently work (it silently no-ops; see #236) and would not fire on a push even if it did.
    So until #236 lands, ask the maintainer to request Copilot review on a bot PR, and ask again
    after each push you want re-reviewed.

  Treat Copilot's feedback like any review:
  - Evaluate each comment on its merits — verify it against the code, don't blindly implement.
  - Fix what's valid, and reply to each comment noting the resolution.
  - A comment you decline (e.g. a false positive) still gets a reply explaining why, and its
    thread still needs resolving.
  - Iterate until the review reports no new actionable comments, then resolve every thread before
    merging.
