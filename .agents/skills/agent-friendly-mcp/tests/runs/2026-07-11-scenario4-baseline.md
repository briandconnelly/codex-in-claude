# Scenario 4 (Instructions and description prose) — baseline run

- **Date:** 2026-07-11
- **Tree:** `a3cd37f`
- **Run:** baseline (produced without the skill)
- **Artifact scored:** inlined verbatim below (produced in the run harness; not a repo file).

## Per-assertion scoring

| # | Result | Evidence |
| --- | --- | --- |
| A1 | **FAIL** | The `deploy_service` description is one undifferentiated paragraph: "Run with dry_run enabled first when possible ... Deploying to production requires a valid change ticket per SRE policy ... Prefer not to deploy to production on Fridays. Setting the `force` flag skips pre-deploy health checks ..." — four rules of different bindingness run together as prose clauses, no list items, no rules/context separation (lines 30–39). The server `instructions` fares slightly better (rules land in their own sentences/paragraphs) but still inside a narrative "Typical workflow" numbered list rather than a structurally marked rules block (lines 5–26). |
| A2 | **FAIL** | Strength is inconsistent: change-ticket ("require...will be rejected without one" — mandatory, fine) and rollback 30-day check are reasonably explicit, but dry-run keeps an unresolved hedge — "Run with dry_run enabled first **when possible**" (line 33, no override condition given, unlike a "default unless X" pattern) — and Friday is left as an unconditioned soft preference in the tool description, "**Prefer not to** deploy to production on Fridays" (line 35), with no override stated in that surface (the instructions text alone adds "unless it's urgent," line 14, but the tool description doesn't inherit it). |
| A3 | **PASS** | `force`: sourced fact (skips pre-deploy health checks) plus a permitted-use condition is present in both instructions (lines 23–25) and the deploy description (lines 36–38). Rollback: the observable 30-day-GC condition is stated explicitly in both instructions (lines 18–21) and the rollback description (lines 47–49), satisfying the checkable-directive bar even though a "generally fast and low-risk" hedge (lines 17, 45) rides alongside it rather than being fully supplanted. |
| A4 | **FAIL** | The rollback sentence is not cleanly atomized: instructions line 17–18 bundles symlink mechanics with an on-disk-existence caveat in one sentence ("...so it's normally fast and low-risk — but only works if that previous release still exists on disk"), and lines 19–21 bundle the 30-day fact with a required check action and a "safety net" framing in a second compound sentence. Same pattern in the rollback description (lines 47–50): on-disk condition + 30-day fact + check action + fallback advice ("you'll need to deploy forward instead") crammed into two sentences rather than split into a discrete mechanics statement and a discrete 30-day rule. |
| A5 | **FAIL** | Spinnaker/Jenkins-fiasco/2021 discretionary trivia is dropped entirely (good — instructions line 6 mentions only "backed by Spinnaker," no history), but the Friday soft norm is handled inconsistently: resolved with an explicit override in the instructions ("unless it's urgent," line 14) yet echoed as an unresolved vibe with no override in the `deploy_service` description ("Prefer not to deploy to production on Fridays," line 35) — the two required prose surfaces disagree, and the description-level instance is not dropped, resolved, or separated. |

**Total: baseline 1/5** (A3 pass only).

## Scored artifact (verbatim)

# deployctl — Baseline Prose (produced from general knowledge only)

## instructions (server-level)

```
deployctl manages deployments for internal services, backed by Spinnaker.

Typical workflow:
1. Run a dry-run deploy first to catch config drift before it reaches an environment.
2. Deploy to staging to validate. If staging seems stuck or unresponsive, a redeploy
   usually clears it.
3. Deploy to production. Production deploys require an approved change ticket
   (SRE policy) — have the ticket ID ready. Avoid deploying to production on
   Fridays unless it's urgent, since there's less coverage to respond to issues
   over the weekend.

Rolling back repoints the release symlink to the previous release, so it's normally
fast and low-risk — but only works if that previous release still exists on disk.
Releases older than 30 days are garbage-collected, so a rollback can fail if the
prior release has aged out; check availability before relying on rollback as a
safety net.

The `force` flag (on deploy) skips pre-deploy health checks. Use it only when you
understand why a health check is failing and are intentionally bypassing it —
it removes a safety net, not just a formality.
```

## description — deployctl_deploy_service

```
Deploys a service via Spinnaker.

Run with dry_run enabled first when possible — it surfaces config drift before
any real change is made. Deploying to production requires a valid change ticket
per SRE policy; the deploy will be rejected without one. Prefer not to deploy to
production on Fridays. Setting the `force` flag skips pre-deploy health checks —
only use it when you're deliberately bypassing a known, understood check, since
it removes the safety net that normally catches bad deploys before they roll out.
```

## description — deployctl_rollback_service

```
Rolls back a service to its previous release by repointing the release symlink.
This is generally fast and low-risk since it doesn't rebuild or redeploy artifacts.

Rollback only works if the previous release is still on disk — releases older
than 30 days are garbage-collected. Check that a prior release actually exists
before depending on rollback as your recovery plan; if it's been purged, rollback
will fail and you'll need to deploy forward instead.
```
