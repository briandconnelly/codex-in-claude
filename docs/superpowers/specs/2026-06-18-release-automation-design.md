# Release automation: GitHub Release + PyPI publish on version tags

**Date:** 2026-06-18
**Status:** Approved design
**Scope:** Add CI/CD so that pushing a `vX.Y.Z` tag builds, publishes to PyPI, and creates a
GitHub Release — modeled on the sibling repo `cc-plugin-codex`, adapted to this repo's specifics.

## Goal

When a new semantic version is released, automatically:

1. Run the full test gate (lint, format, types, pytest with the 95% coverage floor).
2. Build the sdist + wheel.
3. Publish to PyPI via **Trusted Publishing** (OIDC; no stored API token).
4. Create a GitHub Release whose body is the matching `CHANGELOG.md` section, with the built
   distributions attached.

Keep the process agent-friendly: explicit, fail-loud version-consistency guards; least-privilege
tokens; pinned action SHAs; one familiar release shape shared with `cc-plugin-codex`.

## Repo-specific facts that shape the design

- CI today is a **single monolithic `ci.yml`** with one `gate` job (matrix py3.11–3.14:
  `uv sync --frozen`, `ruff check`, `ruff format --check`, `ty check`, `uv run pytest`). There is no
  reusable test workflow yet.
- The version literal lives in exactly **three** files:
  - `pyproject.toml` → `version = "X.Y.Z"`
  - `.claude-plugin/plugin.json` → `"version": "X.Y.Z"` (note: `.claude-plugin/`, not the
    reference's `.codex-plugin/`)
  - `.mcp.json` → `git+https://…@vX.Y.Z`
- `README.md` carries **no** pinned version literal: it uses a dynamic PyPI badge
  (`shields.io/pypi/v`) and a marketplace install (`/plugin marketplace add …`). Nothing to grep
  there. (This differs from `cc-plugin-codex`, whose README pins `cc-plugin-codex==X.Y.Z`.)
- `CHANGELOG.md` uses **Keep a Changelog** bracket format (`## [X.Y.Z] - DATE`, with a leading
  `## [Unreleased]`), not the reference's `## X.Y.Z -` format.
- Build backend is `hatchling`; the console script is `codex-in-claude-mcp`; package is
  `codex_in_claude` under `src/`.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Pre-build test gate | **Extract a reusable `test.yml`** (`on: workflow_call`); both `ci.yml` and `publish.yml` call it | DRY; the release gate means exactly what PR CI means — no quiet drift of the matrix/coverage floor. Confirmed by a Codex second opinion. |
| Release notes body | **Auto-extract the `## [X.Y.Z]` section** from `CHANGELOG.md` | Richer releases; reuses the changelog already maintained. |
| Version-consistency check | **Add a `release-lockstep` job to CI** (every PR/push) | Catches version drift early instead of only at release time. |
| PyPI auth | **Trusted Publishing (OIDC)** | No long-lived token to store/rotate; `id-token: write` scoped to the publish job only. |
| Trigger | `push` on `v*.*.*` tags **+** `workflow_dispatch` (version input, must run from `main`) | Tag push is the happy path; manual dispatch is the break-glass path that creates the tag itself. |

## Architecture

Three workflow files under `.github/workflows/`.

### 1. `test.yml` (new) — reusable test gate

- `on: workflow_call`
- `permissions: contents: read` (least privilege; the test gate never needs more — keep OIDC and
  `environment` out of here so callers can't broaden test privileges).
- One `test` job, matrix py `["3.11", "3.12", "3.13", "3.14"]`, steps lifted from today's `gate`:
  checkout → `astral-sh/setup-uv` (cache on `uv.lock`) → `uv sync --frozen` → `ruff check` →
  `ruff format --check` → `ty check` → `uv run pytest` (95% floor via existing pytest config).
- Pinned action SHAs, matching the existing workflows.

### 2. `ci.yml` (refactored)

- `test` job becomes `uses: ./.github/workflows/test.yml` (no behavior change for contributors).
- New `release-lockstep` job (runs on every PR/push): checkout, then a `set -euo pipefail` script
  that reads the version from `pyproject.toml`, asserts `^[0-9]+\.[0-9]+\.[0-9]+$`, and
  `grep -Fq` that the **three** pinned files agree:
  - `"version": "${version}"` in `.claude-plugin/plugin.json`
  - `@v${version}` in `.mcp.json`
  - (the `pyproject.toml` line is the source of truth)
  - **CHANGELOG is intentionally NOT checked here** — see "CHANGELOG split" below.
- Existing triggers/permissions/concurrency preserved.

### 3. `publish.yml` (new)

- Triggers: `push: tags: ["v*.*.*"]` and `workflow_dispatch` with a required `version` string input
  ("with or without leading v").
- Top-level `permissions: contents: read`; `concurrency` group keyed on ref/version with
  `cancel-in-progress: false` (never cancel an in-flight publish).
- Jobs (chained by `needs`):
  1. **`release-metadata`** — derive `tag`/`version`:
     - On `workflow_dispatch`: require `github.ref == refs/heads/main`; strip leading `v`; assert the
       tag does **not** already exist on the remote.
     - On tag push: take `version` from the ref.
     - Assert `^[0-9]+\.[0-9]+\.[0-9]+$`.
     - `grep -Fq` the three pinned files agree **and** `CHANGELOG.md` contains `## [${version}]`.
     - Emit `tag` and `version` as job outputs.
  2. **`test`** — `needs: release-metadata`, `uses: ./.github/workflows/test.yml`.
  3. **`build`** — `needs: test`: setup-uv + Python 3.12 → `uv build --no-sources` →
     `uvx twine check dist/*` → upload `dist/*` as an artifact (`if-no-files-found: error`).
  4. **`create-tag`** — `needs: [release-metadata, build]`, `permissions: contents: write`: create
     the annotated tag if it doesn't already exist (pushed by `github-actions[bot]`). Runs **before**
     publish so an irreversible PyPI release never exists without its git tag; no-ops on tag-push
     events. A `GITHUB_TOKEN` tag push does not retrigger this workflow.
  5. **`publish`** — `needs: create-tag`, `environment: pypi`, `permissions: { contents: read,
     id-token: write }`: download artifact → `pypa/gh-action-pypi-publish` (Trusted Publishing, no
     token).
  6. **`github-release`** — `needs: [release-metadata, publish]`,
     `permissions: contents: write`: checkout (full history) → download artifact → **extract the
     `## [X.Y.Z]` section from `CHANGELOG.md`** as the release body → `gh release create "$TAG"
     dist/* --title "codex-in-claude $TAG" --notes-file …`. Idempotent: skip if the release already
     exists.

### CHANGELOG section extraction

A small, deterministic extractor (awk/sed) prints the lines from `## [X.Y.Z]` up to (not including)
the next `## ` heading, trimming surrounding blank lines, written to a notes file passed via
`--notes-file`. If the section is missing, `release-metadata` has already failed the run, so the
extractor can assume it exists; still, fall back to "See CHANGELOG.md for release notes." if the
extraction yields empty text (defensive, never blocks a release).

## The CHANGELOG split (important nuance)

The version literal in the three pinned files must **always** agree — so CI checks them on every
push. But `CHANGELOG.md` legitimately holds the next version under `## [Unreleased]` until release.
Requiring a `## [X.Y.Z]` section in CI would force a release just to make CI green (the current tree
is `0.1.0` in `pyproject.toml` but only `## [Unreleased]` in the changelog).

Resolution: **CI lockstep validates the three pinned files only; the `## [X.Y.Z]` CHANGELOG section
is validated at release time inside `publish.yml`.** This guarantees every *published* version has
notes without forcing premature releases. (The reference repo checks CHANGELOG in CI because it
already has shipped versions; pre-first-release, this split is cleaner.)

## First release

The current `CHANGELOG.md` has only `## [Unreleased]`. Cutting the first release converts that to
`## [0.1.0] - <date>` and opens a fresh empty `## [Unreleased]` on top, per the AGENTS.md "cutting a
release" rule. The release itself is then: bump nothing (already `0.1.0`), land the changelog cut on
`main`, push `v0.1.0` (or run the dispatch). Doing the changelog cut is a release-time action, not
part of this CI change.

## One-time manual setup (outside this repo's code)

1. On PyPI, configure a **Trusted Publisher** for the project: owner `briandconnelly`, repo
   `codex-in-claude`, workflow filename `publish.yml`, environment name `pypi`. Use PyPI's
   "pending publisher" flow so the very first publish can create the project.
2. In GitHub repo settings, create an **environment named `pypi`** (optionally with required
   reviewers for a manual approval gate before publish).
3. **Protect `v*` tags** (ruleset or classic protected tag) so only maintainers / release automation
   can create them — a `v*.*.*` tag push triggers a real PyPI publish.

These are documented in the plan as prerequisites; the workflows assume they exist.

## Testing / verification

- Workflows can't be unit-tested in this repo's pytest suite, so verification is:
  - `yamllint`/`actionlint` clean (if available) and a careful read against the reference.
  - `release-lockstep` and the metadata script exercised by intentionally introducing a mismatched
    version locally and confirming the grep fails (manual smoke).
  - A dry validation of the CHANGELOG extractor against `CHANGELOG.md` (run the awk/sed locally,
    confirm it prints the `## [Unreleased]`/a sample `## [X.Y.Z]` body).
  - The real end-to-end is the first `v0.1.0` release (or a TestPyPI dry run if desired — optional,
    not in scope by default).
- No change to existing pytest coverage; the package packaging test (CI matrix ↔ classifiers
  lockstep) is unaffected.

## Out of scope

- Changing install UX from marketplace/`git+…@tag` to `pip install codex-in-claude` (PyPI publish
  enables it, but rewriting README install docs is a separate decision).
- Auto-bumping versions / release-please style automation (versions are bumped deliberately in a PR).
- TestPyPI staging environment (can be added later if desired).
- Updating AGENTS.md "Release coordination" wording (it lists README as a bump target, but README
  has no version literal; a doc tidy can follow separately).

## Files touched

- `.github/workflows/test.yml` — new (reusable gate).
- `.github/workflows/ci.yml` — refactor `test` to call `test.yml`; add `release-lockstep`.
- `.github/workflows/publish.yml` — new (release pipeline).
- `docs/superpowers/specs/2026-06-18-release-automation-design.md` — this spec.
