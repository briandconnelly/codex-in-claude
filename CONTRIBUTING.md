# Contributing

Thanks for your interest in `codex-in-claude`. This file is the human-facing summary; the
authoritative working conventions for both humans and AI agents live in [AGENTS.md](AGENTS.md).

## Development setup

This project uses [`uv`](https://docs.astral.sh/uv/) for everything.

```bash
uv sync                 # create the env and install deps (incl. dev group)
uv run pytest           # run tests (95% coverage floor)
```

## Before you open a PR

Run the full gate locally — CI runs the same on Python 3.11–3.13:

```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest
```

Integration tests that call the real `codex` CLI are excluded by default; run them with:

```bash
uv run pytest -m integration --no-cov
```

## Conventions

[AGENTS.md](AGENTS.md) is the authoritative source; the highlights:

- **Commits & PR titles:** [Conventional Commits](https://www.conventionalcommits.org/) — types
  `feat` / `fix` / `chore` / `docs` / `refactor` / `test` / `perf` / `ci` / `build` / `revert`, with
  an optional scope (`feat(jobs): …`). Mark breaking changes with `!` or a `BREAKING CHANGE:` footer.
- **Merging:** PRs are **squash-merged**, so the PR title becomes the commit — it must be a valid
  Conventional Commit. Keep PRs to one logical change.
- **Branches:** `<type>/<slug>` (e.g. `feat/async-jobs`); never commit directly to `main`.
  The maintainer merges — agents do not merge their own PRs.
- **Versioning:** SemVer; pre-1.0 a minor may change the agent-visible surface. Such a change bumps
  `FINGERPRINT` and is labeled `breaking-change`.
- **The CLI contract** lives in `src/codex_in_claude/cli_contract.py`; see `COMPATIBILITY.md`.
- **The result contract** lives in `src/codex_in_claude/schemas.py`; bump `FINGERPRINT` when the
  agent-visible surface changes and note it in `CHANGELOG.md`.
- `_core/` must not import from its parent package (one-way dependency / extraction seam).

## Reporting issues

Use the issue templates. For security vulnerabilities, **do not** open a public issue — report
privately via GitHub Security Advisories (see [SECURITY.md](SECURITY.md)).
