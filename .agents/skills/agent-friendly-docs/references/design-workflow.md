# Design Workflow

Use this workflow when creating a new documentation surface, redesigning an existing one, or bringing an under-documented repo up to standard for agents to navigate.

## 1. Inventory

List every doc an agent can encounter while working in the repo.

- Instruction files, wherever the harness in use looks for them.
- README and any docs/ directory content.
- ADRs and other decision-history material.
- Per-directory context files, if the repo uses that pattern.
- Prominent doc-comments and docstrings that carry repo-wide claims, not just code-adjacent ones.

Walk the repo tree rather than trusting memory or a stale index.
A doc that isn't in the inventory can't be assigned a layer, linked from an entry point, or checked for staleness later.

## 2. Assign Layers

Place each inventoried doc in exactly one layer: instruction, orientation, reference, decision history, or code-adjacent context.
Layer definitions live in SKILL.md Vocabulary; this stage only places docs, it doesn't redefine the layers.

- A doc that reads as two layers at once — most often an instruction file carrying reference-depth material — is a signal to split it, not to leave it ambiguous.
- Reference-depth material found in the instruction layer is a Token Economy problem: flag it now, move it behind a pointer when you consolidate authority.
- A repo-wide claim sitting only in a code comment is a Comment-Vs-Doc Placement problem: give the fact a doc-level authoritative home and reduce the comment to a pointer to that doc, or to a narrowly code-local note — not a retained repo-wide copy.

Record every straddling or misplaced doc as you go; later stages resolve the split, this stage only finds it.

## 3. Define Read Paths

For the repo's three to five most common tasks — add a feature, fix a bug, run tests, cut a release, or whatever the repo's own recurring tasks are — write the ordered list of docs an agent should load to complete each one.

- Name each read path explicitly; "the agent will find it" is not a read path.
- Wire every entry point (README, instruction file) to link to the first doc in each read path.
- Confirm every doc in a read path is reachable by following links, not by knowing its location in advance.

This stage produces Discoverability And Read Path and Task-To-Doc Routing: an entry point that names or links to the read path for each common task, and no orphaned docs.

## 4. Consolidate Authority

For each fact that appears in more than one doc, pick one authoritative home and replace every other copy with a link to it.

- Where two docs could plausibly conflict despite consolidation, record a stated precedence order that resolves which one wins.
- Decision history is never itself the authoritative home for current policy: if an ADR's content is still binding, promote it into a current-policy doc and link the ADR forward to that doc.

This stage produces Authority And Precedence.

## 5. Set Maintenance Mechanisms

Freshness is a mechanism, not a claim, so give each doc one.

- Assign an owner or owning team to each doc, or to the section of the repo it covers.
- Add a PR expectation: changes to behavior a doc describes carry a stated expectation to update that doc.
- Mark every generated doc with its source and regeneration command.
- Give every ADR a status (proposed, accepted, superseded, rejected) and a forward link from any superseded ADR to what replaced it.
- Do not rely on a timestamp or "last updated" date alone; see Freshness Mechanisms for why that isn't sufficient on its own.

This stage produces Freshness Mechanisms, Generated-Doc Provenance, and ADR Status And Supersession.

## 6. Checklist Walk

Walk every section of docs-checklist.md against the structure you designed: Layer Placement, Discoverability And Read Path, Task-To-Doc Routing, Authority And Precedence, Token Economy, Freshness Mechanisms, Runnable Examples And Commands, Comment-Vs-Doc Placement, Generated-Doc Provenance, and ADR Status And Supersession.

Design Done Criteria: every section is answered in the produced structure, or is explicitly marked not applicable with a one-line justification.
A section with no answer and no justification is unfinished work, not a pass.

Two sections deserve a fresh look here even where earlier stages already touched them: Token Economy and Runnable Examples And Commands.
Re-run their checks from docs-checklist.md against the finished structure rather than trusting earlier-stage answers; a design pass tends to accumulate exactly the convenience additions and unstated preconditions those two sections check for.

## Delegate, Don't Restate

- Instruction-file strategy — canonical instruction file choice, per-harness adapter mechanics — routes to agent-friendly-github.
- Content audits of instruction-layer prose — whether a given sentence binds behavior or merely informs it — route to separating-context-from-constraints.

Apply those skills' answers directly; this workflow does not re-derive them.
