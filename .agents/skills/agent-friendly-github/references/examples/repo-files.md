# Repository Files

Artifacts from the examples set — see [README.md](README.md) for the full index.

## Issue and PR Templates

Implements: §1

Place the issue form at `.github/ISSUE_TEMPLATE/bug.yml` and the PR template at `.github/PULL_REQUEST_TEMPLATE.md`.

**`.github/ISSUE_TEMPLATE/bug.yml`** — a structured bug-report form that reduces ambiguity and prompt-injection surface (T1):

```yaml
name: Bug report
description: Report a reproducible defect
title: "[bug] "
labels:
  - type/bug
body:
  - type: markdown
    attributes:
      value: |
        Thank you for reporting a bug.
        Fill in every section; incomplete reports will be closed.
  - type: textarea
    id: description
    attributes:
      label: Description
      description: What happened, and what did you expect?
    validations:
      required: true
  - type: textarea
    id: reproduction
    attributes:
      label: Steps to reproduce
      description: Minimal steps that reliably trigger the bug.
    validations:
      required: true
  - type: textarea
    id: environment
    attributes:
      label: Environment
      description: OS, runtime version, package version.
    validations:
      required: true
  - type: checkboxes
    id: existing
    attributes:
      label: Existing issues
      options:
        - label: I searched open issues and this is not a duplicate.
          required: true
```

**`.github/PULL_REQUEST_TEMPLATE.md`** — requires a linked issue, a test plan, and a security note:

```markdown
## Summary

<!-- One paragraph describing what this PR does and why. -->

Closes #

## Test plan

- [ ] Unit tests added or updated
- [ ] Integration tests pass locally (`uv run pytest`)
- [ ] Manual verification steps (list them):

## Security considerations

<!-- Does this change touch auth, secrets, permissions, or CI? Note it here. -->
None / see below:

## Checklist

- [ ] Branch is up to date with `main`
- [ ] Commit messages follow conventional commits
- [ ] CODEOWNERS paths updated if new owned paths were added
- [ ] PR is in draft until all checks pass and human review is requested
```

## Agent Instruction File Pattern

Implements: §1

A canonical `AGENTS.md` at the repo root holds all agent guidance.
Per-tool files are thin pointers to it — they do not duplicate content.
For Claude Code, `CLAUDE.md` contains exactly one line (`@AGENTS.md`) and nothing else; Claude Code resolves the reference automatically.
Other tools use their own include or reference mechanism (for example, Gemini's `GEMINI.md` may use a different syntax — check the tool's documentation).
In a monorepo, add a nested `AGENTS.md` per subtree that has meaningfully different branching, testing, or off-limits rules; the root `AGENTS.md` sets defaults and the nested file overrides only the differences.

**`AGENTS.md`** — canonical agent instruction file at the repo root:

```markdown
# Agent Instructions

## Identity and attribution

- Use a distinct GitHub App identity or fine-grained PAT for automated commits.
- Every commit must carry a `Co-authored-by:` trailer when pairing with a human.
- Do not use classic broad PATs.

## Branching strategy

- Branch off `main` for all work: `feat/<slug>`, `fix/<slug>`, `chore/<slug>`.
- Never commit directly to `main` or a release branch.
- Never force-push a remote ref without explicit human instruction.

## Commit format

- Follow Conventional Commits: `type(scope): short description`.
- Keep the subject line under 72 characters.
- Reference the issue number in the commit body: `Closes #<n>`.

## Pull requests

- Open PRs touching CODEOWNERS-owned paths as drafts and leave them in draft: a human promotes them to ready, not the agent.
  For unowned paths, mark ready once all checks pass.
- Agents never merge PRs: open the PR, report check status, and stop — the maintainer merges.
- Agents never change repository configuration to unblock themselves — no editing rulesets, branch protection, required checks, Actions settings, environments, or secrets.
  (If merge automation is ever wanted, the delegation must be written here explicitly.)
- Never approve your own PR, and never treat a red required check as ignorable.
- Re-request human review after any post-approval push.
- CODEOWNERS paths require a human team review — do not attempt to satisfy it yourself.

## Testing

- Run `uv run pytest` before marking a PR ready.
- Every required check must be green before a PR is handed to the maintainer to merge.

## Off-limits paths

- `.github/workflows/` — do not edit CI workflows without explicit human instruction.
- `CODEOWNERS` — do not edit without explicit human instruction.
- `infra/` — do not edit production infrastructure files.

## Security

- Never interpolate `github.event.*` values directly into shell commands.
- Never enable `ACTIONS_STEP_DEBUG` or `ACTIONS_RUNNER_DEBUG` in production.
- Pin any third-party action you add to a full 40-hex SHA with a version comment.
```

**`CLAUDE.md`** — the complete file for Claude Code; a single line that references the canonical instruction file:

```text
@AGENTS.md
```

## Label Taxonomy

Implements: §1

A minimal label set covering type, priority, and (in monorepos) scope.
Create labels via `gh label create` or import them from a YAML file with a tool like `github-label-sync`.

```yaml
# labels.yml — adapt names and colors to your project conventions
labels:
  # Type labels
  - name: "type/bug"
    color: "d73a4a"
    description: "Something is not working"
  - name: "type/feature"
    color: "0075ca"
    description: "New feature or request"
  - name: "type/chore"
    color: "e4e669"
    description: "Maintenance, refactoring, or tooling"
  - name: "type/docs"
    color: "0052cc"
    description: "Documentation only change"
  # Priority labels
  - name: "priority/p0"
    color: "b60205"
    description: "Critical — blocking or data-loss risk"
  - name: "priority/p1"
    color: "e99695"
    description: "High — should land this sprint"
  - name: "priority/p2"
    color: "f9d0c4"
    description: "Normal — backlog"
  # Workflow labels
  - name: "agent:in-progress"
    color: "ededed"
    description: "An agent has claimed this issue (see the operating playbook's claim rule)"
  # Scope labels (monorepo — add one per major subtree)
  - name: "scope/auth"
    color: "c5def5"
    description: "packages/auth subtree"
  - name: "scope/billing"
    color: "bfd4f2"
    description: "packages/billing subtree"
  - name: "scope/core"
    color: "d4c5f9"
    description: "packages/core subtree"
```

## Starter .gitignore

Implements: §1 (T5)

This is a starting point, not exhaustive; it does not untrack files already committed (`git rm --cached <file>` plus a history rewrite is needed for those — and the rewrite is the dangerous force-push the playbook warns against).

```text
# Secrets and credentials
.env
.env.*
*.pem
*.key
*.p12
*.pfx
*credentials*
*.secret

# Build output and caches
node_modules/
__pycache__/
.venv/
dist/
build/
*.log
.coverage
coverage/
```

## Minimal .gitattributes

Implements: §1 (productivity)

`text=auto` normalizes line endings on the next add/commit and will produce a one-time churn commit if the repo already has mixed line endings.
`linguist-generated` only affects GitHub's diff display and stats; it does not block or enforce anything.
Do not mark lockfiles as generated, because lockfile diffs are part of dependency review.

```text
# Normalize line endings for all text files
* text=auto eol=lf

# Mark true generated output so GitHub collapses its diffs and excludes it from language stats
dist/** linguist-generated=true
build/** linguist-generated=true
```

## Supporting Files (CONTRIBUTING.md, SECURITY.md)

Implements: §1 (T1)

**`CONTRIBUTING.md`** — points contributors and agents at the canonical instruction file and sets process expectations:

```markdown
# Contributing

Agent guidance lives in [`AGENTS.md`](/AGENTS.md); read it before opening any PR.

- Branch off `main`: `feat/<slug>`, `fix/<slug>`, `chore/<slug>`.
- Open PRs touching CODEOWNERS-owned paths as drafts; a code owner promotes them to ready. For other paths, mark ready once all required checks pass.
- At least one human reviewer must approve before merge — agents may not self-approve.
- Run `uv run pytest` locally before marking a PR ready.
- Follow [Conventional Commits](https://www.conventionalcommits.org/) for all commit messages.
```

**`SECURITY.md`** — directs security reporters away from public issues:

```markdown
# Security Policy

**Do not file security vulnerabilities as public GitHub issues.**

Report security issues privately using [GitHub private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
or email the security contact listed in the repository's package metadata.

We will acknowledge reports within 5 business days and aim to resolve critical issues within 30 days.
```
