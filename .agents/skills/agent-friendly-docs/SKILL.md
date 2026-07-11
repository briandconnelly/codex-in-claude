---
description: Use when designing, structuring, auditing, or reviewing the documentation surface of a repository that AI coding agents read while working — instruction files, README, docs/, ADRs, per-directory context files, and code-adjacent comments. Symptoms include agents re-deriving project context every session, reading stale or wrong docs, instruction files bloated with reference material, README token bloat, ADRs mistaken for current policy, embedded commands that fail as written, duplicated content drifting apart, and repo-wide context trapped in code comments. Covers layering and placement, discoverability and read paths, one authoritative home per fact, token economy, freshness mechanisms, and runnable examples. Not for published docs sites or llms.txt, generic prose quality, GitHub repo safety configuration, or rules-vs-context audits of instruction-file content.
metadata:
    github-path: agent-friendly-docs
    github-ref: refs/heads/main
    github-repo: https://github.com/briandconnelly/skills
    github-tree-sha: 804eeb0fc17477696e15534459838e966106e494
name: agent-friendly-docs
---
# Agent-Friendly Docs

Use this skill to make a repo's documentation surface cheap for agents to navigate and trust.

## Core Standard

- Structure docs by layer and route by task: an agent reaches the doc that answers its question without reading everything.
- Give every fact exactly one authoritative home; everything else links to it.
- Label historical material (ADRs, postmortems) as historical, not current policy.
- Spend agent tokens deliberately: keep the instruction layer minimal and always-loaded; put heavier material behind explicit pointers.
- Treat freshness as a mechanism, not an intention: ownership, PR doc-update expectations, generated-doc provenance, and verifiable links and commands.
- Make every embedded command or example runnable as written from the repo root, or state its preconditions.

## When To Use

- Designing or restructuring a repo's documentation surface.
- Auditing an existing documentation surface for agent-friendliness.
- Diagnosing a concrete agent failure: missing project conventions, reading stale docs, re-deriving context every session.

## When Not To Use

- Published documentation sites or llms.txt — this skill covers repo-internal docs only.
- Generic prose quality or writing style with no agent-consumption angle.
- Instruction-file strategy and repo safety configuration — use agent-friendly-github.
- Rules-vs-context content audits of instruction-file prose — use separating-context-from-constraints.
- Trivial edits to a doc surface that is already well-layered; just make the edit.

## Vocabulary

- **Instruction layer** — always-loaded binding norms: the canonical instruction file plus thin per-harness adapters.
  Adapter strategy itself is owned by agent-friendly-github, not this skill.
- **Orientation layer** — read-on-demand maps: README, architecture overviews, CONTEXT.md, per-directory context files.
  Loaded when an agent orients itself, not on every turn.
- **Reference layer** — detailed how-to and API material, loaded only when a task demands it.
  Kept out of the instruction layer precisely because it is heavy.
- **Decision history** — ADRs and postmortems that explain why a past choice was made.
  Never binds unless promoted into a current-policy doc.
- **Code-adjacent context** — comments and docstrings carrying constraints that must travel with the code.
  Repo-wide context does not belong here even when it is convenient to write.
- **Canonical instruction file** — the one instruction file a harness reads directly.
  Other harnesses point at it through a thin adapter instead of duplicating it.
- **Per-harness adapter** — a thin, harness-specific file that points at the canonical instruction file instead of restating it.
- **Read path** — the sequence of docs an agent loads, in order, to complete a given task.
- **Authoritative vs historical** — whether a doc states current policy or merely records a past decision.
- **Generated-doc provenance** — a marker on a generated file naming its source and regeneration command.
- These layers are a routing lens for "what does an agent read first, what is authoritative, what is historical, what is ignorable" — not a filing system every repo must adopt.

## Workflow

1. Identify the audience: repo owner, who can restructure, versus contributor or agent operator, who works within what exists.
   Audience decides what to lead with.
2. Classify the task: design/restructure, audit of an existing surface, or diagnosis of a concrete failure.
3. For design or restructure, follow [design-workflow.md](references/design-workflow.md); for audit or diagnosis, follow [review-workflow.md](references/review-workflow.md).
4. Both workflows walk [docs-checklist.md](references/docs-checklist.md) as the shared standard.
5. Delegate instruction-file strategy questions to agent-friendly-github.
   Delegate rules-vs-context content audits of instruction-layer docs to separating-context-from-constraints when available; otherwise apply a one-line inline check: does this sentence bind behavior or just inform it?
6. Use [examples.md](references/examples.md) for concrete shapes.

## Done Criteria

Before declaring done, read the relevant workflow and walk [docs-checklist.md](references/docs-checklist.md) against your output.

- **Design tasks**: every checklist section is answered in the produced structure or explicitly marked not-applicable with a one-line justification.
- **Review tasks**: every checklist section is covered by a finding, marked OK with brief evidence, or noted not-checked with reason.
- **Diagnosis tasks**: response names the most likely failure path and separates immediate mitigation from owner-side restructuring.
