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
  — CI runs it ahead of the four above, and the `prek` pre-commit hook runs it too once installed
  (see below). CI (`.github/workflows/test.yml`) is the authoritative gate and runs all of this on
  every supported Python version.
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

## Auditing changes with the bundled skills

Maintenance skills live under `.agents/skills/` (mirrored to `.claude/skills/` for Claude Code
discovery). When a change touches the surface below, audit it with the matching skill before landing
— these are quality lenses, not part of [the gate](#tooling):

- **MCP tools, resources, prompts** (schemas, descriptions, the server instructions block) →
  `agent-friendly-mcp`.
- **Instruction-style text** (this file, `skills/` bodies, tool/server descriptions, `commands/`
  slash-command prompts) → `separating-context-from-constraints`.
- **Documentation** (`README`, `docs/`, `CONTRIBUTING`, per-directory context files) →
  `agent-friendly-docs`.

Each skill's own description owns *when* it applies — consult it rather than re-deriving triggers
here. A Claude Code session surfaces these automatically; naming them keeps the expectation explicit
and reachable by any harness that reads this file.

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
- **A new parameter is new API surface, not just new behavior.** Test the documented invariants
  across the parameter's whole domain — the boundary values and the invalid ones — not only the
  values the current callers pass. Red-green covers the behavior you intended; the input domain
  needs its own pass. This matters most in `_core/`, which is written for callers who do not exist
  yet. (#273 added `BoundedCapture(head_bytes=...)` tested only at `None` and `0`, the two values
  its callers used; `head_bytes > max_bytes` then retained ~15x the byte cap while reporting
  `truncated=False`, silently breaking the guarantee stated in that class's own docstring.)
- The **95% coverage floor** is enforced in CI. Live tests that hit the real `codex` CLI are marked
  `integration` and excluded by default (`uv run pytest -m integration --no-cov`).

## Agent identity

Agent sessions in this repo run under dedicated bot identities — GitHub App actors distinct from
the maintainer's personal account, never the maintainer's own login: an agent's commits, pushes,
`gh` calls, and PRs attribute to its bot identity, while the maintainer's own git operations on the
same machine keep the personal account. The accounts the claim protocol recognizes are the
`$agent_ids` allowlist in the Git / PRs query below — that list is the roster's only home, and this
prose deliberately names no agent. Setup and mechanism live in the `agent-bot-identity` skill; what
matters here is what an agent identity does and does not buy.

- **An agent identity buys attribution, not containment.** An agent running on the maintainer's
  machine executes as the maintainer's OS user and can reach the personal GitHub credentials there,
  so it holds *both* identities. The ruleset's required review binds the **bot token**, not the
  agent. Everything in Git / PRs below — never merging, never self-approving — is a convention
  agents uphold, not a boundary that stops them. The only hard boundaries are each identity's own
  permission grants (for a GitHub App, its installation list) and this repo's server-side rulesets.
- **No agent identity holds the Workflows permission — an enrollment requirement for any identity
  added here — so GitHub rejects agent pushes that touch `.github/workflows/`.** This is enforced
  server-side, not a convention: a change under that path has to come from the maintainer (that is
  why #295's workflow removal could not be pushed by the agent working it). CI logic *outside* that
  path — scripts the workflows invoke, composite actions — is still writable by agents, which is
  part of why the human review gate matters.
- **A GitHub App bot actor cannot be an issue assignee.** `gh issue edit --add-assignee` fails for
  bot actors, and `Issues: write` is already the widest grant, so no permission fixes it. The
  label-based claim protocol in Git / PRs exists to work around this.

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
- **Claim an issue before working it, and never work one someone else has taken.** Before starting,
  check the assignees (`gh issue view ISSUE_NUMBER --json assignees,title`) and run the active-claim
  query below. Stop if either is taken: the issue is assigned to anyone other than the maintainer
  directing your session, or it carries an active claim that is not yours.
- **The claim is a comment, and its identity is that comment's id.** Sessions cannot be told apart
  by actor: many sessions post as the same bot account, and more than one recognized account may
  participate, so neither the comment author nor the `agent:in-progress` label identifies a session
  — the comment id is the only unique key, and every rule below turns on it. Claim by commenting first, with `<!-- agent-claim -->` as the first line; **record the `id` the
  API returns** — that is your claim for the rest of the issue's life. The label is shared state with
  no owner: an index for humans and search, written only by the agent that wins the race below.
- **An active claim is a claim comment whose id no release names — and only comments from
  recognized agent accounts are protocol data.** A release comment's first line is exactly
  `<!-- agent-release:CLAIM_ID -->`, naming the one claim it releases. The query below keys both
  markers on the recognized accounts' immutable account ids — the `$agent_ids` allowlist
  (rename-proof, unlike logins). That allowlist is the roster's only home, and it changes only by a
  reviewed edit to this file, only for an identity the maintainer operates and directs to follow
  this protocol. Racing agents compute the same winner only when they run the same list, and a
  feature branch or worktree can carry a stale roster, so two rules keep the list synchronized: run
  the claim query as it stands on the default branch's tip (`git fetch origin && git show
  origin/main:AGENTS.md`), not from your checkout; and a newly enrolled identity posts its first
  claim only after its enrollment has merged to the default branch. De-enrollment runs in reverse:
  the identity stops claiming, every active claim it holds is released, and only then is its id
  removed — removing an id erases its comments from protocol state, so an unreleased claim would
  silently vanish and the issue would read as free. A claim or release posted by any unlisted
  account can neither take nor free an issue. The query fetches **every** comments page (`--paginate`), so a claim or release past page
  one still counts. It prints the winning active claim, or nothing if the issue is free. A non-zero
  exit means a page fetch or parse failed — discard any output and re-run; never treat a failed run
  as "free":

  ```sh
  set -o pipefail
  gh api repos/briandconnelly/codex-in-claude/issues/ISSUE_NUMBER/comments --paginate | jq -s '
    [
      292553156     # briandconnelly-agent[bot]
    ] as $agent_ids                   # recognized agent accounts — the roster
    | add
    | map(select(.user.id as $u | $agent_ids | index($u) != null))
    | [ .[] | .body
          | capture("^<!-- agent-release:(?<id>[0-9]+) -->(\r?\n|$)").id | tonumber ] as $released
    | [ .[] | select(.body | test("^<!-- agent-claim -->(\r?\n|$)")) ]
    | map(select(.id as $i | ($released | index($i)) | not)) | min_by(.id) // empty'
  ```

  Both markers must be the *entire* first line — trailing text on the marker line makes it inert.

- **Resolve a race by lowest claim id, then take the label.** After commenting, re-run that query.
  The active claim with the lowest `id` wins: REST ids are unique ascending integers, so they never
  tie and every racing agent computes the same winner. (`gh issue view --json comments` returns
  opaque GraphQL node ids — `IC_kwDO…` — which carry no order and cannot decide this; use REST.) If
  the winner is your claim id, take the label (`gh issue edit ISSUE_NUMBER --add-label
  agent:in-progress`). If it is not, release your own claim, **leave the label alone** — it belongs
  to the winner — and stop.
- **Release your own claim whenever you stop working the issue** — you lost the race, the work
  landed, or you abandoned it. Post `<!-- agent-release:CLAIM_ID -->` naming the id of *your* claim,
  and remove the label (`gh issue edit ISSUE_NUMBER --remove-label agent:in-progress`) only if you
  held the winning claim. **Never release a claim id that is not yours** — that hands the issue to
  the next agent while its owner is still working. A stale claim blocks the next agent. (Bot
  actors cannot self-assign — see Agent identity.)
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
  - **Bot/agent-authored PRs** — any non-human author — get **no automatic Copilot review**: the
    ruleset rule skips authors that hold no Copilot seat. This is **deliberately not automated** — requesting the Copilot
    reviewer through the API needs a full user identity that CI/automation tokens don't have (a
    fine-grained PAT is refused `403`, and a broad classic PAT was declined on security grounds; see
    #294 / #236). So the maintainer requests Copilot on a bot PR with the web-UI **"Request review"**
    button. If you authored the PR, ask them to — and again after each push you want re-reviewed.

  Treat Copilot's feedback like any review:
  - Evaluate each comment on its merits — verify it against the code, don't blindly implement.
  - Fix what's valid, and reply to each comment noting the resolution.
  - A comment you decline (e.g. a false positive) still gets a reply explaining why, and its
    thread still needs resolving.
  - Iterate until the review reports no new actionable comments, then resolve every thread before
    merging.
