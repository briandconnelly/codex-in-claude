# Release Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pushing a `vX.Y.Z` tag (or running a manual dispatch) runs the test gate, builds the
package, publishes to PyPI via Trusted Publishing, and creates a GitHub Release whose body is the
matching `CHANGELOG.md` section.

**Architecture:** Extract the existing CI test gate into a reusable `test.yml` (`on: workflow_call`)
called by both `ci.yml` and a new `publish.yml`. `ci.yml` gains a `release-lockstep` job that
verifies the three pinned version literals agree on every push. `publish.yml` chains
metadata-validation → test → build → PyPI publish → GitHub Release.

**Tech Stack:** GitHub Actions, `uv`/`hatchling` build, `pypa/gh-action-pypi-publish` (OIDC Trusted
Publishing), `gh` CLI for releases. No new Python code.

## Global Constraints

- Version literal lives in exactly three files and must always agree: `pyproject.toml`
  (`version = "X.Y.Z"`), `.claude-plugin/plugin.json` (`"version": "X.Y.Z"`), `.mcp.json`
  (`...@vX.Y.Z`). README has **no** version literal — do not grep it.
- `CHANGELOG.md` uses Keep a Changelog bracket format: `## [X.Y.Z] - DATE`, with `## [Unreleased]`.
- Pin every action by full commit SHA with a trailing `# vX.Y.Z` comment. Reuse SHAs already in the
  repo; new ones are listed per task.
- Least privilege: top-level `permissions: contents: read`; elevate only the specific job that needs
  it (`id-token: write` on publish, `contents: write` on the release job). Keep OIDC and
  `environment:` out of `test.yml`.
- Python test matrix is `["3.11", "3.12", "3.13", "3.14"]` — must stay identical to today's CI.
- All inline shell uses `set -euo pipefail` and passes untrusted values via `env:`, never inline
  `${{ }}` interpolation in the script body.

**Pinned action SHAs (authoritative list for this plan):**

| Action | SHA + tag |
|---|---|
| `actions/checkout` | `df4cb1c069e1874edd31b4311f1884172cec0e10 # v6.0.3` |
| `astral-sh/setup-uv` | `fac544c07dec837d0ccb6301d7b5580bf5edae39 # v8.2.0` |
| `actions/setup-python` | `a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6` |
| `actions/upload-artifact` | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7` |
| `actions/download-artifact` | `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8` |
| `pypa/gh-action-pypi-publish` | `cef221092ed1bacb1cc03d23a2d87d1d172e277b # release/v1` |

---

### Task 1: Extract reusable `test.yml` and wire `ci.yml` to it

**Files:**
- Create: `.github/workflows/test.yml`
- Modify: `.github/workflows/ci.yml` (replace the `gate` job body with a call to `test.yml`)

**Interfaces:**
- Produces: a reusable workflow at `./.github/workflows/test.yml` callable via
  `uses: ./.github/workflows/test.yml` with no inputs. Runs the full gate over the py-matrix.

- [ ] **Step 1: Create `test.yml` with the extracted gate**

```yaml
name: Test

on:
  workflow_call:

# Least-privilege: the gate only reads the repo. Keep OIDC/environment OUT of here.
permissions:
  contents: read

jobs:
  gate:
    name: lint + types + tests (py${{ matrix.python-version }})
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.11", "3.12", "3.13", "3.14"]
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6.0.3
      - uses: astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39 # v8.2.0
        with:
          python-version: ${{ matrix.python-version }}
          enable-cache: true
      - name: Install dependencies
        run: uv sync --frozen
      - name: Lint
        run: uv run ruff check .
      - name: Format
        run: uv run ruff format --check .
      - name: Type check
        run: uv run ty check
      - name: Test (95% coverage floor)
        run: uv run pytest
```

- [ ] **Step 2: Replace the `gate` job in `ci.yml` with a call to `test.yml`**

In `.github/workflows/ci.yml`, delete the entire `gate:` job (lines `jobs:` → end of the gate
steps) and replace the `jobs:` block with:

```yaml
jobs:
  test:
    uses: ./.github/workflows/test.yml
```

Leave the `name`, `on`, top-level `permissions: contents: read`, and `concurrency` blocks unchanged.
The final `ci.yml` reads:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

# Least-privilege default token; this workflow only reads the repo.
permissions:
  contents: read

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  test:
    uses: ./.github/workflows/test.yml
```

- [ ] **Step 3: Verify both files parse as valid YAML**

Run:
```bash
python -c "import yaml,sys; [yaml.safe_load(open(f)) for f in ('.github/workflows/test.yml','.github/workflows/ci.yml')]; print('yaml ok')"
```
Expected: `yaml ok`

If `actionlint` is installed, also run `actionlint .github/workflows/test.yml .github/workflows/ci.yml`
and expect no output. (Optional; install via `brew install actionlint`.)

- [ ] **Step 4: Confirm the gate semantics are preserved**

Run:
```bash
grep -c "ruff check\|ruff format --check\|ty check\|uv run pytest\|uv sync --frozen" .github/workflows/test.yml
```
Expected: `5`

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/test.yml .github/workflows/ci.yml
git commit -m "ci: extract reusable test.yml and call it from ci.yml"
```

---

### Task 2: Add the `release-lockstep` job to `ci.yml`

**Files:**
- Modify: `.github/workflows/ci.yml` (add a second job)
- Create (temporary, for the smoke check): none — use a local one-off command

**Interfaces:**
- Consumes: the reusable `test.yml` from Task 1 (unchanged).
- Produces: a `release-lockstep` CI job that fails the build if the three pinned version literals
  disagree. Deliberately does **not** check `CHANGELOG.md` (see plan/spec "CHANGELOG split").

- [ ] **Step 1: Write a local smoke that proves the check logic catches drift**

Save this as `/tmp/lockstep.sh` (it is the exact logic the job will run, against the working tree):

```bash
#!/usr/bin/env bash
set -euo pipefail
version="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "pyproject.toml version must be X.Y.Z; got ${version}" >&2
  exit 1
fi
grep -Fq "\"version\": \"${version}\"" .claude-plugin/plugin.json
# Anchor on the trailing quote+comma so 0.1.1 does NOT match @v0.1.10.
grep -Fq "@v${version}\"," .mcp.json
echo "lockstep ok: ${version}"
```

- [ ] **Step 2: Run the smoke against the current tree (should pass)**

Run:
```bash
bash /tmp/lockstep.sh
```
Expected: `lockstep ok: 0.1.0`

- [ ] **Step 3: Run the smoke against a deliberately broken value (should fail)**

Run (portable — feeds a deliberately mismatched `.mcp.json` line to the same anchored grep,
no absolute paths, working tree untouched):
```bash
version="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)"
sed 's/@v[0-9.]*"/@v9.9.9"/' .mcp.json \
  | grep -Fq "@v${version}\"," && echo "UNEXPECTED PASS" || echo "correctly failed on drift"
```
Expected: `correctly failed on drift`

- [ ] **Step 4: Add the `release-lockstep` job to `ci.yml`**

Append to the `jobs:` block in `.github/workflows/ci.yml` (after the `test:` job):

```yaml
  release-lockstep:
    name: Release lockstep
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6.0.3
      - name: Validate version literals agree
        run: |
          set -euo pipefail

          version="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)"
          if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo "pyproject.toml version must be X.Y.Z; got ${version}" >&2
            exit 1
          fi

          # The CHANGELOG section is validated at release time (publish.yml), not here,
          # so a version can sit under ## [Unreleased] without breaking CI.
          grep -Fq "\"version\": \"${version}\"" .claude-plugin/plugin.json
          # Anchor on the trailing quote+comma so 0.1.1 does NOT match @v0.1.10.
          grep -Fq "@v${version}\"," .mcp.json

          echo "Version ${version} is consistent across pyproject.toml, plugin.json, .mcp.json"
```

- [ ] **Step 5: Verify YAML still parses**

Run:
```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml ok')"
```
Expected: `yaml ok`

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add release-lockstep version-consistency check"
```

---

### Task 3: Add the CHANGELOG release-notes extractor + verify it

**Files:**
- Create: `.github/scripts/changelog-section.sh`
- Test: local smoke run against `CHANGELOG.md`

**Interfaces:**
- Produces: `.github/scripts/changelog-section.sh <version>` — prints the body of the
  `## [<version>]` section (lines after the heading up to the next `## `, with leading/trailing
  blank lines trimmed) to stdout. Exits 0 even if empty (caller decides fallback). Used by Task 4's
  `github-release` job.

- [ ] **Step 1: Write the extractor script**

Create `.github/scripts/changelog-section.sh`:

```bash
#!/usr/bin/env bash
# Print the CHANGELOG.md body for a given version section: lines after the
# "## [X.Y.Z]" heading up to (not including) the next "## " heading, with
# surrounding blank lines trimmed. Prints nothing if the section is absent.
set -euo pipefail

version="${1:?usage: changelog-section.sh <version>}"
file="${2:-CHANGELOG.md}"

awk -v ver="$version" '
  index($0, "## [" ver "]") == 1 { capture = 1; next }
  capture && /^## / { exit }
  capture { print }
' "$file" |
  # trim leading blank lines, then trailing blank lines
  awk 'NF { started = 1 } started { print }' |
  awk '{ lines[NR] = $0 } END { last = NR; while (last > 0 && lines[last] ~ /^[[:space:]]*$/) last--; for (i = 1; i <= last; i++) print lines[i] }'
```

- [ ] **Step 2: Make it executable and run it against the existing `## [Unreleased]` section**

Run:
```bash
chmod +x .github/scripts/changelog-section.sh
.github/scripts/changelog-section.sh Unreleased | head -3
```
Expected: the first lines of the Unreleased body, e.g. starting with
`Initial release: a Claude Code plugin that calls the OpenAI Codex CLI...` (proves heading match +
trimming work; `Unreleased` is used here only as a stand-in section name for the smoke).

- [ ] **Step 3: Verify a missing section prints nothing and exits 0**

Run:
```bash
.github/scripts/changelog-section.sh 99.99.99; echo "exit=$?"
```
Expected: no body lines, then `exit=0`.

- [ ] **Step 4: Commit**

```bash
git add .github/scripts/changelog-section.sh
git commit -m "ci: add changelog section extractor for release notes"
```

---

### Task 4: Create `publish.yml` (metadata → test → build → publish → release)

**Files:**
- Create: `.github/workflows/publish.yml`

**Interfaces:**
- Consumes: `./.github/workflows/test.yml` (Task 1); `.github/scripts/changelog-section.sh` (Task 3).
- Produces: the release pipeline. `release-metadata` emits outputs `tag` (e.g. `v0.1.0`) and
  `version` (e.g. `0.1.0`) consumed by downstream jobs.

- [ ] **Step 1: Create `publish.yml`**

```yaml
name: Publish

on:
  workflow_dispatch:
    inputs:
      version:
        description: Version to release, with or without leading v
        required: true
        type: string
  push:
    tags: ["v*.*.*"]

permissions:
  contents: read

concurrency:
  group: publish-${{ github.ref_name }}-${{ inputs.version }}
  cancel-in-progress: false

jobs:
  release-metadata:
    name: Validate release metadata
    runs-on: ubuntu-latest
    outputs:
      tag: ${{ steps.metadata.outputs.tag }}
      version: ${{ steps.metadata.outputs.version }}
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6.0.3
        with:
          fetch-depth: 0

      - name: Validate version references
        id: metadata
        env:
          EVENT_NAME: ${{ github.event_name }}
          INPUT_VERSION: ${{ inputs.version }}
          REF_NAME: ${{ github.ref_name }}
          GITHUB_REF: ${{ github.ref }}
        run: |
          set -euo pipefail

          if [[ "$EVENT_NAME" == "workflow_dispatch" ]]; then
            if [[ "$GITHUB_REF" != "refs/heads/main" ]]; then
              echo "Manual releases must run from main; got $GITHUB_REF" >&2
              exit 1
            fi
            version="${INPUT_VERSION#v}"
            tag="v${version}"
            if git ls-remote --exit-code --tags origin "refs/tags/${tag}" >/dev/null 2>&1; then
              echo "Tag ${tag} already exists" >&2
              exit 1
            fi
          else
            tag="$REF_NAME"
            version="${tag#v}"
          fi

          if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo "Version must be X.Y.Z; got ${version}" >&2
            exit 1
          fi

          grep -Fq "version = \"${version}\"" pyproject.toml
          grep -Fq "\"version\": \"${version}\"" .claude-plugin/plugin.json
          # Anchor on the trailing quote+comma so 0.1.1 does NOT match @v0.1.10.
          grep -Fq "@v${version}\"," .mcp.json
          grep -Fq "## [${version}]" CHANGELOG.md

          {
            echo "tag=${tag}"
            echo "version=${version}"
          } >> "$GITHUB_OUTPUT"

  test:
    needs: release-metadata
    uses: ./.github/workflows/test.yml

  build:
    name: Build distributions
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6.0.3

      - uses: astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39 # v8.2.0
        with:
          enable-cache: true
          cache-dependency-glob: uv.lock

      - uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6
        with:
          python-version: "3.12"

      - name: Build distributions
        run: uv build --no-sources

      - name: Check distributions
        run: uvx twine check dist/*

      - name: Upload distributions
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7
        with:
          name: python-package-distributions
          path: dist/*
          if-no-files-found: error

  # Create the tag BEFORE publishing so a PyPI release (irreversible) never
  # exists without its git tag. For tag-push events the tag already exists and
  # this no-ops; for workflow_dispatch it creates the tag from the dispatch SHA.
  # A tag pushed with GITHUB_TOKEN does not retrigger this workflow.
  create-tag:
    name: Create tag
    needs: [release-metadata, build]
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6.0.3
        with:
          fetch-depth: 0

      - name: Create tag if needed
        env:
          TAG: ${{ needs.release-metadata.outputs.tag }}
        run: |
          set -euo pipefail

          if git ls-remote --exit-code --tags origin "refs/tags/${TAG}" >/dev/null 2>&1; then
            echo "Tag ${TAG} already exists."
            exit 0
          fi

          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git tag -a "$TAG" -m "codex-in-claude ${TAG}" "$GITHUB_SHA"
          git push origin "$TAG"

  publish:
    name: Publish to PyPI
    needs: create-tag
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      contents: read
      id-token: write
    steps:
      - name: Download distributions
        uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8
        with:
          name: python-package-distributions
          path: dist/

      - name: Publish distributions
        uses: pypa/gh-action-pypi-publish@cef221092ed1bacb1cc03d23a2d87d1d172e277b # release/v1

  github-release:
    name: Create GitHub Release
    needs: [release-metadata, publish]
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6.0.3
        with:
          fetch-depth: 0

      - name: Download distributions
        uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8
        with:
          name: python-package-distributions
          path: dist/

      - name: Build release notes
        env:
          VERSION: ${{ needs.release-metadata.outputs.version }}
        run: |
          set -euo pipefail
          .github/scripts/changelog-section.sh "$VERSION" > release-notes.md
          if [[ ! -s release-notes.md ]]; then
            echo "See CHANGELOG.md for release notes." > release-notes.md
          fi

      - name: Create release
        env:
          GH_TOKEN: ${{ github.token }}
          TAG: ${{ needs.release-metadata.outputs.tag }}
        run: |
          set -euo pipefail

          if gh release view "$TAG" >/dev/null 2>&1; then
            echo "GitHub Release ${TAG} already exists."
            exit 0
          fi

          gh release create "$TAG" dist/* \
            --title "codex-in-claude ${TAG}" \
            --notes-file release-notes.md
```

- [ ] **Step 2: Verify YAML parses and the job graph is well-formed**

Run:
```bash
python -c "import yaml; d=yaml.safe_load(open('.github/workflows/publish.yml')); print(sorted(d['jobs']))"
```
Expected: `['build', 'create-tag', 'github-release', 'publish', 'release-metadata', 'test']`

- [ ] **Step 3: Verify the metadata grep block matches the current tree**

Run (mirrors what `release-metadata` does for `0.1.0`, minus the changelog which isn't cut yet):
```bash
version=0.1.0
grep -Fq "version = \"${version}\"" pyproject.toml \
  && grep -Fq "\"version\": \"${version}\"" .claude-plugin/plugin.json \
  && grep -Fq "@v${version}\"," .mcp.json \
  && echo "metadata grep ok"
```
Expected: `metadata grep ok`

- [ ] **Step 4: Confirm OIDC/environment is scoped to the publish job only**

Run:
```bash
grep -c "id-token: write" .github/workflows/publish.yml; grep -c "environment: pypi" .github/workflows/publish.yml
```
Expected: `1` and `1` (both belong to the `publish` job; `test.yml` must contain neither).

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/publish.yml
git commit -m "ci: add publish workflow for PyPI + GitHub releases on version tags"
```

---

### Task 5: Document the release process and one-time prerequisites

**Files:**
- Create: `docs/RELEASING.md`
- Modify: `AGENTS.md` (point its "Release coordination" section at `docs/RELEASING.md`)

**Interfaces:**
- Consumes: nothing at runtime — human/agent documentation only.
- Produces: a single source of truth for how to cut a release and the out-of-band PyPI/GitHub setup.

- [ ] **Step 1: Write `docs/RELEASING.md`**

```markdown
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
```

- [ ] **Step 2: Point `AGENTS.md` at the new doc**

In `AGENTS.md`, under the `## Release coordination` heading, add this line immediately after the
existing "Bump together:" sentence:

```markdown
See `docs/RELEASING.md` for the full release procedure and the one-time PyPI/GitHub setup.
```

- [ ] **Step 3: Verify the doc renders and links resolve**

Run:
```bash
test -f docs/RELEASING.md && grep -q "docs/RELEASING.md" AGENTS.md && echo "docs ok"
```
Expected: `docs ok`

- [ ] **Step 4: Commit**

```bash
git add docs/RELEASING.md AGENTS.md
git commit -m "docs: document release process and PyPI trusted-publishing setup"
```

---

## Self-Review

**Spec coverage:**
- Reusable `test.yml` → Task 1. ✅
- `ci.yml` refactor + `release-lockstep` (3 files, no CHANGELOG) → Tasks 1–2. ✅
- `publish.yml` metadata/test/build/publish/release chain → Task 4. ✅
- Trusted Publishing, OIDC scoped to publish job, `environment: pypi` → Task 4 (verified Step 4). ✅
- Auto-extract CHANGELOG section as release body, with fallback → Tasks 3 + 4. ✅
- CHANGELOG split (CI checks 3 files; CHANGELOG checked at release) → Tasks 2 + 4. ✅
- First-release / one-time PyPI+env+tag-protection setup documented → Task 5. ✅
- Pinned SHAs throughout → all tasks (SHA table). ✅
- Tag created BEFORE PyPI publish (irreversible publish never outruns its tag) → Task 4 `create-tag`
  job (`needs: [release-metadata, build]`, before `publish`). ✅
- `.mcp.json` version grep anchored on trailing `",` so `0.1.1` ≠ `0.1.10` → Tasks 2, 4. ✅

**Placeholder scan:** No TBD/TODO; every workflow file and script is shown in full; every step has a
concrete command and expected output.

**Type/name consistency:** `release-metadata` outputs `tag`/`version` are consumed with those exact
names in `test`/`build`/`publish`/`github-release`. The extractor is invoked as
`.github/scripts/changelog-section.sh "$VERSION"` exactly as defined in Task 3. `.mcp.json` grep uses
`@v${version}` consistently in Tasks 2 and 4. Matrix `["3.11","3.12","3.13","3.14"]` matches today's
CI.

**Note on Task 2 Step 3:** the broken-value smoke is illustrative; the authoritative pass/fail
behavior is that `grep -Fq "@v${version}"` returns non-zero on a mismatched `.mcp.json`. If the
sandboxed copy command is awkward locally, a simpler equivalent is:
`printf '%s' '...@v9.9.9' | grep -Fq "@v0.1.0" || echo "correctly failed on drift"`.
