---
name: working-an-issue
description: Use when working a GitHub issue in this repo from selection through a green PR — pick and claim an issue, verify it, plan it (with Codex), implement it behind the gate, get a Codex review, open the PR, handle the Copilot review, and hand off for merge. Sequences the lifecycle and its human checkpoints; defers every rule to the AGENTS.md section that owns it, so read the cited section rather than relying on this file to restate it.
---

# Working an issue

Orchestrates the issue → green-PR lifecycle in this repo. This skill owns the **order of
operations and the human checkpoints**; every *rule* lives in `AGENTS.md` (auto-loaded each
session) or the named sibling skill.

**Scope & dependencies.** Runs in a **Claude Code** session — it drives Codex and the Copilot
review, both Claude-Code-side. Sibling skills it names: `collaborating-with-codex` (bundled with
this plugin, so present in that session) and, optionally, `receiving-code-review` (an external
Superpowers plugin). When a named skill is absent, fall back to the cited `AGENTS.md` section — it
carries the binding rule regardless.

**How to read a phase: each phase names an action and cites the `AGENTS.md` § section (or sibling
skill) that owns the rule. Open the cited section and follow it — this file deliberately does not
restate the rule, so acting on a phase line alone will miss it.**

## Before you start: verification honesty

Run every check a phase prescribes — never predict its result. Local inspection (`git show`, the
working tree) proves neither a push nor a merge — it only shows a local object. Before claiming
something is **pushed**, compare the remote ref (`git rev-parse origin/<branch>` or
`git ls-remote origin <branch>`); before claiming it is **merged**, query the PR or default branch
(`gh pr view <n> --json state,mergedAt`, or confirm `origin/main` contains the commit).

## Phases

Work them in order. ▸ marks a phase that pauses for the maintainer.

1. **Select & claim** ▸ — Pick the most important open, unassigned issue, then run the claim
   protocol. Rules: `AGENTS.md` § *Git / PRs* (claiming) and § *Agent identity* (why a bot can't
   self-assign). **Checkpoint: confirm the issue choice with the maintainer before claiming.**

2. **Verify the issue** — Reproduce or otherwise confirm the issue's premise before planning; a
   filed premise can be false, and everything downstream builds on it.

3. **Plan** ▸ — Work out the approach, collaborating with Codex. How: the `collaborating-with-codex`
   skill. **Checkpoint: present the plan and get approval before implementing.**

4. **Implement** — Test-first, then minimal code. Rules by section: § *Testing*; § *Git / PRs*
   (branch, commit, and one-change-per-PR conventions); § *Tooling* (run the gate). For a
   surface-changing PR: § *Versioning* decides whether it bumps `FINGERPRINT` and needs a CHANGELOG
   entry, and § *The result contract* owns the manifest-snapshot mechanism when it does. Don'ts:
   § *Release coordination* (version literals) and § *Agent identity* (`.github/workflows/`).

5. **Codex review the implementation** — Have Codex review the branch; treat each finding as a
   claim to verify, not a command. How: `collaborating-with-codex`. Sequencing rule: for a breaking
   change, run both this review and the Phase-3 deliberation — they catch different defect classes.

6. **Open the PR** — Rules: § *Git / PRs*. Get every check green, then ask the maintainer to
   request the Copilot review (bot PRs get none automatically).

7. **Review loop** — Verify each Copilot comment against the code before acting; fix, reply, and
   resolve every thread, including ones you decline. Rules: § *Git / PRs*; for disposition
   discipline, the `receiving-code-review` skill when it's available, else § *Git / PRs*. Iterate
   until nothing new is actionable and every thread is resolved.

8. **Hand off** ▸ — Rules: § *Git / PRs*, which owns both the never-merge rule and claim release.
   Agents never merge — stop at green-and-approved unless the maintainer gives an explicit
   in-session instruction to merge this PR. Release your own claim per § *Git / PRs* whenever you
   stop working the issue — its triggers include *the work landed*, not only losing the race or
   abandoning.
