---
name: working-an-issue
description: Use when working a GitHub issue in this repo from selection through a green PR — pick and claim an issue, verify it, plan it (with Codex), implement it behind the gate, get a Codex review, open the PR, handle the Copilot review, and hand off for merge. Sequences the lifecycle and its human checkpoints; defers every rule to the AGENTS.md section that owns it, so read the cited section rather than relying on this file to restate it.
---

# Working an issue

Orchestrates the issue → green-PR lifecycle in this repo.
This skill owns the **order of operations and the human checkpoints**; every *rule* lives in `AGENTS.md` (auto-loaded each session), with named helper skills supplying the mechanism where a step needs one.

**Scope & dependencies.** Harness-neutral, but not capability-free.
Run this skill only in an agent session that has loaded this repo's `AGENTS.md`, can pause at the maintainer checkpoints, can operate Git/GitHub, and can invoke a separate Codex process or integration **that preserves the readiness, redaction, spend, and verify safeguards of** `collaborating-with-codex`.
Preferred helper in Claude Code: `collaborating-with-codex` (bundled with this plugin).
Optional review-disposition helper: `receiving-code-review` (an external Superpowers plugin).
When a helper skill is absent, use the fallback its phase names; `AGENTS.md` stays authoritative for every rule.

**How to read a phase: each phase names an action and cites the `AGENTS.md` § section that owns the rule (and, where a step needs one, a helper skill as the mechanism).
Open the cited section and follow it — this file deliberately does not restate the rule, so acting on a phase line alone will miss it.**

## Before you start: verification honesty

Run every check a phase prescribes — never predict its result.
Local inspection (`git show`, the working tree) proves neither a push nor a merge — it only shows a local object.
Before claiming something is **pushed**, compare the remote ref (`git rev-parse origin/<branch>` or `git ls-remote origin <branch>`); before claiming it is **merged**, query the PR or default branch (`gh pr view <n> --json state,mergedAt`, or confirm `origin/main` contains the commit).

## Phases

Work them in order.
▸ marks a phase that pauses for the maintainer.

1. **Select & claim** ▸ — Pick the most important open, unassigned issue, then run the claim protocol.
   Rules: `AGENTS.md` § *Git / PRs* (claiming) and § *Agent identity* (why a bot can't self-assign).
   **Checkpoint: confirm the issue choice with the maintainer before claiming.**

2. **Verify the issue** — Reproduce or otherwise confirm the issue's premise before planning; a filed premise can be false, and everything downstream builds on it.

3. **Plan** ▸ — Work out the approach, then obtain Codex input on it.
   Mechanism: the `collaborating-with-codex` skill where available, else an equivalent Codex integration that preserves its safeguards.
   **Checkpoint: present the plan and get approval before implementing.**

4. **Implement** — Test-first, then minimal code.
   Rules by section: § *Testing*; § *Git / PRs* (branch, commit, and one-change-per-PR conventions); § *Tooling* (run the gate).
   For a surface-changing PR: § *Versioning* decides whether it bumps `FINGERPRINT` and needs a CHANGELOG entry, and § *The result contract* owns the manifest-snapshot mechanism when it does.
   Don'ts: § *Release coordination* (version literals) and § *Agent identity* (`.github/workflows/`).

5. **Codex review the implementation** — Obtain a fresh Codex review of the branch; treat each finding as a claim to verify, not a command.
   Mechanism: the `collaborating-with-codex` skill where available, else an equivalent Codex integration that preserves its safeguards.
   Sequencing rule: for a breaking change, run both this review and the Phase-3 deliberation — they catch different defect classes.

6. **Open the PR** — Rules: § *Git / PRs*.
   Get every check green, then ask the maintainer to request the Copilot review (bot PRs get none automatically).

7. **Review loop** — Verify each Copilot comment against the code before acting; fix, reply, and resolve every thread, including ones you decline.
   Rules: § *Git / PRs*, which owns the disposition discipline; the `receiving-code-review` skill is an optional helper for applying it where available.
   Iterate until nothing new is actionable and every thread is resolved.

8. **Hand off** ▸ — Rules: § *Git / PRs*, which owns both the never-merge rule and claim release.
   Agents never merge — stop at green-and-approved unless the maintainer gives an explicit in-session instruction to merge this PR.
   Release your own claim per § *Git / PRs* whenever you stop working the issue — its triggers include *the work landed*, not only losing the race or abandoning.
