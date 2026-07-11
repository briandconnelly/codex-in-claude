# Docs Checklist

Use this as the detailed standard for both design and review tasks.

## Layer Placement

- Every doc is assigned to exactly one layer: instruction, orientation, reference, decision history, or code-adjacent context.
- No doc serves two layers at once (e.g., an instruction file that also carries reference-depth material).
- The instruction layer contains only binding norms, not orientation or reference material.
- Decision history is labeled historical, not current policy.

Layer names and definitions live in SKILL.md Vocabulary; this section only checks that every doc has been placed in one of them.
A doc that seems to need two layers is a signal to split it, not to leave it ambiguous.
Whether a given sentence in the instruction layer is a binding norm or merely informative context is a separating-context-from-constraints question, not one this checklist re-derives; apply that skill (or its one-line inline fallback) when available.
Canonical-instruction-file and per-harness-adapter strategy is owned by agent-friendly-github; this section checks placement only, not adapter mechanics.

## Discoverability And Read Path

- Every doc is reachable by links from at least one entry point (README or an instruction file).
- An agent orienting itself can locate the doc that answers a given question without reading the whole repo.
- No doc is orphaned: reachable only by knowing its path in advance.

Entry points carry the map; everything else earns its keep by being linked from somewhere on that map.

## Task-To-Doc Routing

- Each of the repo's common tasks (add a feature, fix a bug, run tests, cut a release) has an identifiable read path: a named sequence of docs to load.
- The entry point names that read path, or names where to find it, for each common task.
- A read path can be completed by following links; it does not require guessing which file is relevant.

This is Discoverability And Read Path applied to specific, recurring tasks rather than to an arbitrary question.

## Authority And Precedence

- Every fact has exactly one authoritative home.
- Every other mention of that fact is a link to the authoritative home, not a restatement.
- Where two docs could plausibly conflict, a stated precedence order resolves which one wins.

Duplication is the symptom; the fix is a link, not a rewrite in both places.

## Token Economy

- The instruction layer is minimal: it does not carry material that only some tasks need.
- Heavy or task-specific material sits behind an explicit pointer from the instruction layer, not inline in it.
- No bulk content is duplicated across layers (e.g., reference material copied into the instruction file for convenience).
- Orientation-layer docs stay lean; bulk reference or historical material (full API detail, changelogs) moves behind a pointer to its own layer.

Every always-loaded token is a token spent on every single turn; the instruction layer's size is a standing cost, not a one-time one.

## Freshness Mechanisms

- Each doc (or the section of the repo it covers) has a named owner or owning team.
- PRs that change behavior a doc describes carry a stated expectation to update that doc.
- No doc claims freshness by timestamp or "last updated" date alone, without a mechanism that keeps it true.

A timestamp records when a doc was last touched, not whether it is still correct; ownership and PR expectations are what keep it correct.

## Runnable Examples And Commands

- Every embedded command runs as written from the repo root, or the doc states the precondition needed to run it (working directory, environment variable, prior setup step).
- Every path referenced in an example resolves from the stated starting point.
- No example depends on unstated state (an unmentioned file, an unmentioned prior command) to succeed.

An agent will copy-paste the example as written; if it doesn't run, the doc has failed at its one job.

## Comment-Vs-Doc Placement

- Constraints coupled to a specific piece of code (why this line is guarded, why this default was chosen) live in a comment or docstring next to that code.
- Repo-wide context (architecture, conventions, cross-cutting policy) never hides in a code comment where only a reader of that one file will see it.
- Where a comment states a repo-wide fact, that fact also has an authoritative home in a doc, per Authority And Precedence.

Code-adjacent context travels with the code by design; repo-wide context needs to be found without first finding the code.

## Generated-Doc Provenance

- Every generated doc is marked as generated.
- Every generated doc names its source (the file, script, or tool that produces it).
- Every generated doc gives the regeneration command.

An agent that doesn't know a doc is generated will edit it directly and lose the edit on the next regeneration.

## ADR Status And Supersession

- Every ADR carries a status (e.g., proposed, accepted, superseded, rejected).
- Every superseded ADR links forward to the ADR or doc that superseded it.
- No ADR is treated as binding current policy unless its content has been promoted into a current-policy doc; ADRs are decision history by default, per SKILL.md Vocabulary.

Status and forward links are what let an agent tell a live decision from a dead one without reading the whole history.
