# Contributing

Thanks for your interest in `codex-in-claude`. This file is the human-facing summary; the
authoritative working conventions for both humans and AI agents live in [AGENTS.md](AGENTS.md).

## Development setup

This project uses [`uv`](https://docs.astral.sh/uv/) for everything.

```bash
uv sync                 # create the env and install deps (incl. dev group)
uv run pytest           # run tests (95% coverage floor)
uv run prek install --prepare-hooks   # one-time: install local Git hooks
```

[`prek`](https://prek.j178.dev) runs the same checks locally that CI enforces (see
[`prek.toml`](prek.toml)): file hygiene + `ruff` / `ty` / Actions-pinning / `uv lock --check` on
commit, `pytest` on push, and Conventional Commit validation on the commit message. It's a local
convenience — CI remains the authoritative gate.

## Before you open a PR

Run the gate locally — it is defined once in [AGENTS.md](AGENTS.md) under Tooling. CI runs the same
across every supported Python version (the matrix in
[`.github/workflows/test.yml`](.github/workflows/test.yml), kept in lockstep with the trove
classifiers in [`pyproject.toml`](pyproject.toml)).

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
- **Versioning:** SemVer; pre-1.0 a minor may change the agent-visible surface. Whether a change
  bumps `FINGERPRINT` and whether it is `breaking-change` are **two independent questions** — most
  surface changes bump the fingerprint without being breaking. Don't infer one from the other;
  [AGENTS.md](AGENTS.md) → Versioning carries the decision table.
- **The CLI contract** lives in `src/codex_in_claude/cli_contract.py`; see `COMPATIBILITY.md`.
- **The result contract** lives in `src/codex_in_claude/schemas.py`; the categories whose change
  triggers a `FINGERPRINT` bump are the `FINGERPRINT_COVERS` tuple in that file. Note every bump in
  `CHANGELOG.md`.
- `_core/` must not import from its parent package (one-way dependency / extraction seam).

## Reporting issues

Use the issue templates. For security vulnerabilities, **do not** open a public issue — report
privately via GitHub Security Advisories (see [SECURITY.md](SECURITY.md)).
