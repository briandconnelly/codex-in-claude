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
   The maintainer (`briandconnelly`) is configured as a **required reviewer**, so the publish step
   pauses for a manual approval before anything ships to PyPI. Self-review is left enabled, so the
   maintainer can approve their own release deployment.
3. **Protect release tags.** A push of any `v*.*.*` tag triggers a real PyPI publish, so tag creation
   is restricted by the active ruleset **`release-tags-protected`** (Settings → Rules → Rulesets;
   target tags `v*`; blocks creation/update/deletion). Its `bypass_actors` grants the **Repository
   admin** role an `always` bypass (#99), so the maintainer can push a `v*` tag directly — no manual
   toggle needed. The distinct agent identity (`briandconnelly-agent[bot]`) is **not** a bypass actor
   and holds no admin role, so an agent cannot create a release tag: releases stay human-initiated.
   `github-actions[bot]` is likewise not a bypass actor, so the workflow-dispatch path's automated
   tag creation is still blocked by this ruleset (see *Cutting a release*).

No PyPI API token is stored anywhere — publishing uses short-lived OIDC credentials.

## Cutting a release

1. On a branch, bump the version in lockstep across `pyproject.toml`, `.claude-plugin/plugin.json`,
   and `.mcp.json` (the `codex-in-claude==X.Y.Z` PyPI pin). The `release-lockstep` CI job verifies
   these three agree. The pin references the release being cut, so it only resolves once that version
   is on PyPI (after the tag publishes) — same as the previous git-tag pin. Do **not** bump
   `FINGERPRINT` here — it moves in the feature/fix PRs that changed the agent-visible surface (see
   AGENTS.md → Versioning and Release coordination); a bump in the release PR would double-count.
   Just verify it already reflects everything shipping in this release; the committed manifest
   snapshot surfaces any un-acknowledged surface drift for review (the bump itself stays review
   policy — it is an acknowledgment guard, not a mechanical one).
2. Move the `## [Unreleased]` entries in `CHANGELOG.md` into a new dated section
   `## [X.Y.Z] - YYYY-MM-DD`, and leave a fresh empty `## [Unreleased]` on top.
3. Open a PR, get CI green, and merge to `main`.
4. Release by pushing the tag as the maintainer — the `always` bypass for the **Repository admin**
   role on `release-tags-protected` (#99) lets it through with no toggle:
   `git tag -a vX.Y.Z -m "codex-in-claude vX.Y.Z" && git push origin vX.Y.Z`.
   (The **Publish** workflow's `workflow_dispatch` path instead creates the tag as
   `github-actions[bot]`, which is *not* a bypass actor, so that path is still blocked by the ruleset
   — either temporarily set the ruleset's *Enforcement status* to **Disabled** for that path only, or
   just use the tag push above. If tag creation is blocked, the `publish` job is skipped and nothing
   ships — zero spend.)
5. The workflow validates that all version references and the `## [X.Y.Z]` CHANGELOG section exist,
   then publishes and creates the release. If validation fails, nothing is published (zero spend).
