# CODEOWNERS and Draft-First

Artifacts from the examples set — see [README.md](README.md) for the full index.

## CODEOWNERS Patterns (Monorepo and Solo)

Implements: §1 (T3)

The explicit-prefix pattern applies to any repo; the monorepo example below shows per-subtree ownership, and the solo variant follows it.

```text
# Owners on protected paths must be human users or teams, kept bot-free by membership hygiene.
# GitHub has no native "owners must be human" enforcement — this is a repository policy.
# A required CODEOWNERS review can only be satisfied by a listed owner;
# if a bot or agent account were listed, it could satisfy its own review (T3).

# ORDER MATTERS: CODEOWNERS is last-match-wins — the LAST matching pattern takes
# precedence. The catch-all therefore comes FIRST, so every explicit rule below
# overrides it; a trailing catch-all would silently override every explicit owner.

# Last-resort catch-all: any path not matched by a later rule goes to platform-team.
# Must be a human team, not a bot.
*                        @org/platform-team

# Per-package ownership — explicit path prefixes, never catch-all-only
/packages/auth/          @org/auth-team
/packages/billing/       @org/billing-team
/packages/core/          @org/platform-team

# Shared infrastructure owned by platform
/.github/                @org/platform-team
/infra/                  @org/infra-team

# Documentation owned by docs team
/docs/                   @org/docs-team
```

**Solo variant.** A single-maintainer repo uses one user owner and usually does NOT enable "require review from code owners," because the maintainer cannot approve their own PR:

```text
# Solo: one human owner. If you DO enable required code-owner review,
# the maintainer must merge their own owned-path PRs via the §2 human bypass.
*        @maintainer
/infra/  @maintainer
```

## Draft-First Convention, Not a CI Gate

Implements: §1 (T3)

Draft-first (open a protected-path change as a draft, let a human promote it to ready) is an OPERATE convention, not something a required status check can robustly enforce.

No robust required check exists for this property.
A required check that fails while the PR is ready-for-review would block merge permanently — a PR must be non-draft to merge, so a check that demands draft state would never clear.
A variant that only inspects the `opened` action is cleared by the next push (`synchronize`), so it is trivially bypassed.
"Opened as draft" is not durably re-verifiable: once the PR is promoted, there is no webhook event that reliably re-runs the check to confirm the original state.

The actual enforcement for protected-path changes is the required CODEOWNERS review (§2): a CODEOWNERS-listed human must approve, and agents are never listed as owners.
Pair that enforcement with the operate-playbook draft-first rule.

If you want a nudge, a NON-required workflow may post a comment when a protected-path PR is opened ready — but never make draft-state a required merge gate.
