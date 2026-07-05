# Releasing

Releases are automated by `.github/workflows/publish.yml`: pushing a `vX.Y.Z` tag (or running the
**Publish** workflow via `workflow_dispatch` from `main`) runs the test gate, builds the package,
publishes to PyPI via Trusted Publishing, and creates a GitHub Release whose body is the matching
`CHANGELOG.md` section.

## One-time setup (already done once per repo)

1. **PyPI Trusted Publisher.** On PyPI, add a trusted publisher for project `codex-in-claude`:
   owner `briandconnelly`, repository `codex-in-claude`, workflow `publish.yml`, environment `pypi`.
   For the very first release (before the project exists on PyPI), use PyPI's *pending publisher*
   flow with the same values.
2. **GitHub environment.** Create a repository environment named `pypi` (Settings → Environments).
   Optionally add required reviewers to gate the publish step behind a manual approval.
3. **Protect release tags.** A push of any `v*.*.*` tag triggers a real PyPI publish, so tag creation
   is restricted by the active ruleset **`release-tags-protected`** (Settings → Rules → Rulesets;
   target tags `v*`; blocks creation/update/deletion). Its `bypass_actors` is **empty** — while the
   agent shares the maintainer's account this ruleset is the load-bearing publish control, so nothing
   (not even the maintainer or `github-actions[bot]`) can push a `v*` tag without first relaxing it.
   A dedicated release identity becomes the documented bypass actor once a distinct agent identity
   exists (#99). Because bypass is empty, this ruleset also blocks the automated tag creation in the
   manual publish path — see *Cutting a release* below for the temporary-relax step.

No PyPI API token is stored anywhere — publishing uses short-lived OIDC credentials.

## Cutting a release

1. On a branch, bump the version in lockstep across `pyproject.toml`, `.claude-plugin/plugin.json`,
   and `.mcp.json` (the `codex-in-claude==X.Y.Z` PyPI pin). The `release-lockstep` CI job verifies
   these three agree. The pin references the release being cut, so it only resolves once that version
   is on PyPI (after the tag publishes) — same as the previous git-tag pin. Do **not** bump
   `FINGERPRINT` here — it moves in the feature/fix PRs that changed the agent-visible surface (see
   AGENTS.md → Versioning and Release coordination); a bump in the release PR would double-count.
   Just verify it already reflects everything shipping in this release (the manifest guard test
   enforces that).
2. Move the `## [Unreleased]` entries in `CHANGELOG.md` into a new dated section
   `## [X.Y.Z] - YYYY-MM-DD`, and leave a fresh empty `## [Unreleased]` on top.
3. Open a PR, get CI green, and merge to `main`.
4. **Temporarily relax tag protection.** Both release paths create a `v*` tag, which the
   `release-tags-protected` ruleset blocks while its bypass is empty. In Settings → Rules → Rulesets →
   **`release-tags-protected`**, set *Enforcement status* to **Disabled**, do the release (next step),
   then set it back to **Active** immediately after the tag exists. (Once a dedicated release identity
   is added as a bypass actor — see #99 — this manual toggle goes away.)
5. Release one of two ways:
   - **Tag push:** `git tag -a vX.Y.Z -m "codex-in-claude vX.Y.Z" && git push origin vX.Y.Z`.
   - **Manual:** run the **Publish** workflow from `main` with the version as input; its `create-tag`
     job pushes the tag as `github-actions[bot]` (also subject to the ruleset). If tag creation is
     blocked, the `publish` job is skipped and nothing ships — zero spend.
6. The workflow validates that all version references and the `## [X.Y.Z]` CHANGELOG section exist,
   then publishes and creates the release. If validation fails, nothing is published (zero spend).
