# Review Workflow

Use this workflow when auditing an existing documentation surface or diagnosing a concrete agent failure.

## 1. Route Audit Vs Diagnosis

If the user reports a concrete failure — an agent missed a convention, read a stale doc, or re-derived context it should have found — start in diagnosis mode.

In diagnosis mode:

- name the most likely failure path: which doc the agent should have read, and why it didn't
- give the smallest caller-side mitigation that unblocks the agent today (a pointer, a link, a reminder in the task prompt)
- separate that from the owner-side restructuring that fixes the surface itself
- if confirming the hypothesis needs reading a doc the caller hasn't shared, ask before assuming its contents

If the user is asking for a general audit of the documentation surface, continue with the full workflow below.

## 2. Establish Evidence

Prefer direct evidence over inferred behavior.

Label each piece of evidence within a finding as one of:

- `observed` — you read the doc, followed the link, or ran the command yourself
- `inferred` — deduced from related material without reading the doc directly
- `absence-of-evidence` — a required piece is missing: no read path for a core task, no owner named, no regeneration command on a generated doc

Absence of a required piece is itself a finding, not a gap to note and move past.

Read the repo's actual doc tree rather than trusting an index or a prior summary of it; a doc that moved or was deleted since the index was written produces a false `OK`.

## 3. Walk The Checklist

Walk every section of [docs-checklist.md](docs-checklist.md) in order: Layer Placement, Discoverability And Read Path, Task-To-Doc Routing, Authority And Precedence, Token Economy, Freshness Mechanisms, Runnable Examples And Commands, Comment-Vs-Doc Placement, Generated-Doc Provenance, and ADR Status And Supersession.

For each section, produce at least one of:

- a finding (see Finding Format below)
- `OK` with the brief evidence that grounds it (the doc, link, or command you checked)
- `not-checked` with the reason (out of scope, no access, not applicable to this repo)

A section with no finding, no `OK`, and no `not-checked` reason is unfinished work, not a pass.
Cite the checklist's own checks by section name rather than restating them; this workflow does not re-derive what docs-checklist.md already defines.

## 4. Severity Scale

- `blocking` — agents get wrong or contradictory answers: authority conflicts between two docs, a stale ADR read as current policy, an embedded command that fails as written.
- `degrading` — agents pay an avoidable cost: token bloat in the instruction layer, a missing read path forcing rediscovery, context buried where an agent won't find it in time.
- `polish` — friction without measurable cost: inconsistent terminology, a link that could be one hop shorter, formatting noise.

Rank ties toward the effect on the agent's next action, not the effort to fix them.

## 5. Finding Format

Each finding includes:

- severity: `blocking`, `degrading`, or `polish`
- checklist section: the docs-checklist.md section it violates
- location: file and section cited
- evidence labels: one per evidence item — `observed`, `inferred`, or `absence-of-evidence`
- impact: one line, stated in terms of what an agent does wrong or pays for
- remediation: the smallest fix first, with a larger restructuring noted separately if one is warranted

## 6. Report Format

Order the report as:

1. Findings, ordered by severity (`blocking` first, then `degrading`, then `polish`).
2. The section-by-section coverage table produced in Walk The Checklist: every docs-checklist.md section marked with a finding reference, `OK`, or `not-checked`.

When no findings surface, state that explicitly and name any section left `not-checked` and why.
