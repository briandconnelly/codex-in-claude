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
3. **Protect release tags.** A push of any `v*.*.*` tag triggers a real PyPI publish, so restrict who
   can create them. Add a tag-protection ruleset (Settings → Rules → Rulesets, target tags `v*`) or
   classic protected-tag rule limiting creation to maintainers / release automation.

No PyPI API token is stored anywhere — publishing uses short-lived OIDC credentials.

## Cutting a release

1. On a branch, bump the version in lockstep across `pyproject.toml`, `.claude-plugin/plugin.json`,
   and `.mcp.json` (the `@vX.Y.Z` tag). The `release-lockstep` CI job verifies these three agree.
   Bump `FINGERPRINT` in `src/codex_in_claude/schemas.py` if the agent-visible surface changed.
2. Move the `## [Unreleased]` entries in `CHANGELOG.md` into a new dated section
   `## [X.Y.Z] - YYYY-MM-DD`, and leave a fresh empty `## [Unreleased]` on top.
3. Open a PR, get CI green, and merge to `main`.
4. Release one of two ways:
   - **Tag push:** `git tag -a vX.Y.Z -m "codex-in-claude vX.Y.Z" && git push origin vX.Y.Z`.
   - **Manual:** run the **Publish** workflow from `main` with the version as input; it creates the
     tag for you.
5. The workflow validates that all version references and the `## [X.Y.Z]` CHANGELOG section exist,
   then publishes and creates the release. If validation fails, nothing is published (zero spend).
