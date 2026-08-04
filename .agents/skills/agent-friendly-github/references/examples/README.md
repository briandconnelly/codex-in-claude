# Examples

Copy-adaptable artifacts implementing the controls in `../config-checklist.md`; each is labeled with the §N section and threat class (Tn) it implements.
Adapt names, paths, org slugs, and SHAs to your repository before use.

Load only the file you need — that is why these are separate files.

| File | Artifacts | Implements |
| --- | --- | --- |
| [rulesets.md](rulesets.md) | Hardened ruleset JSON, optional signing variant, solo-profile adaptation, production environment gate | §2, §3 (T3, T4, T5, T8) |
| [workflows.md](workflows.md) | Least-privilege injection-safe CI workflow (with OIDC job), always-running monorepo gate check | §2, §3 (T2, T5, T6) |
| [required-checks.md](required-checks.md) | Required check: PR links a real open issue; required check: human-only approvals | §1, §2 (T3) |
| [codeowners.md](codeowners.md) | CODEOWNERS patterns (monorepo and solo), draft-first convention note | §1 (T3) |
| [identity.md](identity.md) | GitHub App registration steps, token scope inventory, co-authorship trailers | §4 (T8, T9, T10) |
| [harness-deny.md](harness-deny.md) | Deny rules for the surface no GitHub setting can gate, and how to verify them | §2, §4 (T3, T10, T11) |
| [repo-files.md](repo-files.md) | Issue and PR templates, `AGENTS.md` pattern, label taxonomy, starter `.gitignore`, minimal `.gitattributes`, `CONTRIBUTING.md` and `SECURITY.md` | §1 (T1, T5) |

## Before you copy anything

Action SHAs and version comments throughout these files are illustrative examples only — verify and re-pin each SHA against the release you actually intend to use before deploying.
The pinned SHAs are deliberately NOT kept current; treat any SHA here as stale and resolve the version you want yourself.
Note in particular that `actions/checkout` v6 changed where `persist-credentials` stores the token (a file under `$RUNNER_TEMP` rather than `.git/config`); the input still defaults to `true`, so the `persist-credentials: false` guidance is unchanged across versions.

None of these artifacts has been applied to a live repository — they are parsed and linted only (see `../../decisions/001-github-fact-sheet.md`).
The human-only-approvals check in [required-checks.md](required-checks.md) carries a specific unverified assumption; read its status note before relying on it.
