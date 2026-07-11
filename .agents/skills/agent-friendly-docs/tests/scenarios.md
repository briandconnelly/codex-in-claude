# Test Scenarios for agent-friendly-docs

Behavioral test scenarios for this skill, following the baseline/with-skill methodology: run each scenario with a fresh subagent that does NOT have the skill loaded (baseline), then with the skill loaded (treatment), and compare against the assertions.
A baseline run that already satisfies every assertion means the scenario is too easy; tighten it.
An assertion the with-skill run misses is a finding against the skill, not against the agent.

## How to run

1. **Baseline:** dispatch a subagent with only the scenario prompt below.
   Record which assertions its output satisfies.
2. **Treatment:** dispatch a fresh subagent with the skill content available (or triggered via its description) and the same prompt.
3. **Score:** every assertion is pass/fail with a one-line evidence pointer into the transcript.
   Record results in the table at the bottom.
   Assertion lists may be tightened over time; a results row reflects the assertions as of its date.

## Scenario 1: Design (application test)

**Prompt:**

> Structure the documentation surface for `fleetlog`, a mid-size vehicle-telemetry service.
> Today every doc lives as a flat pile of markdown at the repo root, with no folders and no stated reading order:
>
> ```text
> fleetlog/
> ├── README.md              — 30-line project blurb plus a wall of setup steps
> ├── CLAUDE.md               — build/lint/test commands, a few "always/never" rules
> ├── copilot-instructions.md — same build/lint/test commands, copy-pasted from CLAUDE.md
> ├── CONTRIBUTING.md         — PR process, commit style, review expectations
> ├── ARCHITECTURE.md         — service boundaries, data flow, current storage choice
> ├── TESTING.md              — how to run unit and integration tests, fixture layout
> ├── DEPLOY.md               — how to cut and ship a release
> ├── STYLE.md                — naming and formatting conventions
> ├── ADR-001-database.md     — why Postgres was chosen (no status field)
> ├── ADR-002-queue.md        — the original queue choice, later abandoned (no status field)
> └── API.md                  — full REST endpoint reference
> ```
>
> The repo's most common tasks are: add a feature, fix a bug, run the tests, and cut a release.
> Produce the restructured documentation surface: where each existing doc lives afterward, what (if anything) merges or splits, the read path for each of the four named tasks, and how each doc stays correct over time.

**Assertions (with-skill run must satisfy):**

- [ ] Every listed doc is assigned to exactly one layer — instruction, orientation, reference, or decision history — with `CLAUDE.md`/`copilot-instructions.md` in the instruction layer and `ADR-001`/`ADR-002` in decision history (Layer Placement).
- [ ] The build/lint/test commands duplicated between `CLAUDE.md` and `copilot-instructions.md` are flagged as an authority problem: exactly one authoritative home is named and the duplicate is reduced to a reference rather than a second copy, with the choice of adapter shape routed to agent-friendly-github rather than restated (Layer Placement, Authority And Precedence).
- [ ] A named, ordered read path is produced for each of the four tasks — add a feature, fix a bug, run the tests, cut a release — reachable from an entry point rather than left implicit (Task-To-Doc Routing).
- [ ] The database choice and the queue choice each get exactly one authoritative home, with `ADR-001`/`ADR-002` reduced to historical record plus a forward pointer if either decision is still binding (Authority And Precedence, ADR Status And Supersession).
- [ ] `ADR-001` and `ADR-002` are each assigned a status field, and `ADR-002` (superseded by the later queue move) links forward to whatever replaced it (ADR Status And Supersession).
- [ ] `API.md`'s endpoint reference is placed in the reference layer, not folded into `README.md` or `CLAUDE.md` (Token Economy).
- [ ] Every doc gets an owner and a stated PR-update expectation, not a timestamp alone (Freshness Mechanisms).
- [ ] The checklist walk is completed against the new structure, with every docs-checklist.md section answered or marked not-applicable with a one-line reason (Runnable Examples And Commands and Comment-Vs-Doc Placement included even though no example/comment was in the pile).

**Expected baseline failures:** no explicit layer assignment (docs left in a flat list with new folder names but no stated placement rule), `copilot-instructions.md` duplication left standing or merely "kept in sync" rather than turned into a pointer, read paths described in prose without a named per-task sequence, ADRs renamed or moved but given no status field, no owner or PR-update mechanism stated, no checklist walk performed.

## Scenario 2: Audit (retrieval test)

**Prompt:**

> Audit this repo's documentation surface for agent-friendliness and report your findings.
> You cannot browse the repo or run commands; you have only this captured material.
> The audience is the repo's own maintainers, who can change anything.
>
> File tree:
>
> ```text
> ledgerly/
> ├── README.md
> ├── CLAUDE.md
> ├── .github/copilot-instructions.md
> ├── docs/
> │   ├── adr/0003-postgres-over-dynamo.md
> │   ├── api.md              — canonical REST endpoint reference
> │   └── testing.md
> ├── test/
> │   └── unit/test_reconcile.py
> └── src/ledgerly/reconcile.py
> ```
>
> `README.md` (excerpt, ~120 lines total):
>
> ```markdown
> # ledgerly
>
> Ledgerly is an internal invoicing and reconciliation service.
>
> ## Setup
> ...(12 lines)...
>
> ## Full API Reference
> ### POST /invoices
> ...(38 lines fully duplicating docs/api.md, endpoint by endpoint, with the same request/response examples)...
>
> ## Running Tests
> Run the full suite with `pytest tests/unit -v`.
> ...(9 more lines duplicating docs/testing.md's instructions almost verbatim)...
>
> ## Changelog
> ...(every release back to v0.1, 40 lines)...
> ```
>
> `CLAUDE.md` (canonical instruction file, in full):
>
> ```markdown
> # Instructions
> - Run tests with `uv run pytest --cov=ledgerly test/unit`; coverage must stay at or above 85%.
> - Model all ledger entries per ADR-0003 (Postgres rows) — this is current policy.
> - Never commit generated files under `dist/`.
> ```
>
> `.github/copilot-instructions.md` (in full):
>
> ```markdown
> # Copilot Instructions for ledgerly
> - Run tests with `pytest test/unit`; coverage must stay at or above 95%.
> - Model all ledger entries per ADR-0003 (Postgres rows).
> - Never commit generated files under `dist/`.
> ```
>
> `docs/adr/0003-postgres-over-dynamo.md` (in full):
>
> ```markdown
> # ADR-0003: Postgres over DynamoDB for ledger storage
>
> We will model ledger entries as rows in our existing Postgres instance rather than
> standing up DynamoDB, to avoid operating a second datastore.
> ```
>
> Separately captured context — a note from the platform team, six months after the ADR above was written, in a Slack thread never folded into the ADR file itself:
>
> > "we migrated ledger storage to DynamoDB in v2 for the throughput; this doc is now historical."
>
> `docs/testing.md` (excerpt):
>
> ```markdown
> ## Running the suite
> From the repo root: `pytest tests/unit -v`
> ```
>
> `src/ledgerly/reconcile.py` (top-of-file comment, in full):
>
> ```python
> # Reconciliation policy for the whole ledgerly service:
> # - every mismatch retries 3x with exponential backoff starting at 200ms
> # - a mismatch that survives 3 retries pages the on-call rotation
> # - this applies to every reconciliation job in the service, not just this file
> # (There is no other doc that states this; it lives only here.)
> import ...
> ```
>
> No doc anywhere mentions how to cut a release.

**Assertions (with-skill run must satisfy):**

- [ ] `ADR-0003` is flagged `blocking` under ADR Status And Supersession: it carries no status field, is cited by `CLAUDE.md` as current policy, and the service has since moved off Postgres per the buried Slack note — evidence labeled `observed` for the doc text and `inferred` for whether DynamoDB fully replaced the pattern in code.
- [ ] The `CLAUDE.md`/`copilot-instructions.md` divergence (85% vs. 95% coverage, `--cov=ledgerly` flag present vs. dropped) is flagged `blocking` under Authority And Precedence as a contradictory-authority finding, `observed` directly from the two quoted files.
- [ ] The `pytest tests/unit -v` command in both `README.md` and `docs/testing.md` is flagged `blocking` under Runnable Examples And Commands: the tree shows tests live at `test/unit` (singular), not `tests/unit`, so the command fails as written — `observed` from the tree plus the quoted command.
- [ ] `README.md`'s duplicated API reference, test instructions, and full changelog are flagged `degrading` under Token Economy as bulk reference material carried in the orientation layer instead of pointed to, `observed` from the excerpt.
- [ ] The reconciliation retry/backoff policy embedded only in `reconcile.py`'s header comment is flagged `degrading` under Comment-Vs-Doc Placement: repo-wide policy with no doc-level authoritative home, `observed` from the comment plus `absence-of-evidence` for a corresponding doc.
- [ ] The missing release read path is flagged `degrading` under Task-To-Doc Routing, evidence labeled `absence-of-evidence`.
- [ ] A section-by-section coverage table is produced covering all ten docs-checklist.md sections, with `not-checked` and a reason for any section the captured material cannot answer (e.g., Discoverability And Read Path beyond what the tree shows, Generated-Doc Provenance).
- [ ] Findings are ordered blocking first, then degrading, per Report Format, and each uses the six-part finding format: severity, checklist section, location, evidence labels, impact, remediation.

**Expected baseline failures:** ad-hoc or no severity scale, no distinction between the three blocking findings and the three degrading ones (or all six flattened to one tier), no evidence labels, the adapter drift and the stale-ADR-as-policy finding conflated or missed entirely, the `test/unit` vs. `tests/unit` path mismatch missed because the command "looks fine" without cross-checking the tree, no coverage table.

## Scenario 3: Diagnosis (routing test)

**Prompt:**

> Our coding agent keeps missing our test conventions and reviewers keep bouncing the same PRs back for it.
> Our test conventions — table-driven tests, `t.Parallel()` on every subtest, golden files under `testdata/` — live in a "Testing" section partway down `CONTRIBUTING.md`.
> Our `CLAUDE.md` file lists build, lint, and test *commands* but never mentions `CONTRIBUTING.md` or these conventions.
> `README.md` links to `CONTRIBUTING.md` under a "Contributing" heading near the bottom of the file, below installation and deployment instructions.
> What's going on, and what should we do?

**Assertions (with-skill run must satisfy):**

- [ ] Names the most likely failure path: the agent loads `CLAUDE.md` (and possibly the top of `README.md`) but never reaches `CONTRIBUTING.md`'s "Testing" section, so the conventions never enter its context — the doc locations `observed` from the prompt, the "never reaches it" mechanism `inferred`.
- [ ] Leads with the smallest immediate mitigation — pointing `CLAUDE.md`'s test-command entry directly at `CONTRIBUTING.md`'s Testing section, or pulling the conventions themselves into `CLAUDE.md` — before proposing a larger restructuring (Review Workflow §1).
- [ ] Separates that immediate mitigation from owner-side restructuring (e.g., reconsidering why test conventions live inside a contributing-process doc rather than as their own referenced doc) and frames the latter as a follow-up, not a blocker to today's fix.
- [ ] Stays diagnosis-sized: no full checklist coverage table, no audit of unrelated sections (SKILL.md Done Criteria, Diagnosis tasks).

**Expected baseline failures:** jumps straight to "rewrite your docs" without naming why the agent misses the convention today, no evidence-label distinction between what's stated in the prompt and what's deduced about agent behavior, immediate fix and larger restructuring blended into one undifferentiated list, or a full audit-style walk of the whole doc surface for a two-file problem.

## Scenario 4: Boundary (delegation test)

**Prompt:**

> We're consolidating on a single canonical `CLAUDE.md`, with thin adapter files for Cursor and Copilot that point at it instead of repeating it — tell us how to structure those adapters.
> Separately: `CLAUDE.md` currently has "Keep responses concise" sitting right next to "Never delete a file without explicit confirmation," and we can't tell which of our instruction-file lines are actually supposed to bind the agent's behavior versus which are just style preference — sort that out for us too.

**Assertions (with-skill run must satisfy):**

- [ ] Declines to design the per-harness adapter mechanics itself and routes that question to agent-friendly-github instead of proposing an adapter file format or pointer syntax (SKILL.md Vocabulary, Workflow §5).
- [ ] Declines to adjudicate which `CLAUDE.md` lines bind versus inform and routes that question to separating-context-from-constraints, applying the one-line inline fallback ("does this sentence bind behavior or just inform it?") only if that skill is stated as unavailable rather than working the full distinction itself (Workflow §5).
- [ ] Does not silently answer both delegated questions in full as though they were in scope, and does not refuse the entire prompt outright — it names what it can still speak to directly, such as where the canonical file and its adapters sit in the layer model (Layer Placement) once the mechanics and the binds-vs-informs split are handled elsewhere.
- [ ] Does not restate agent-friendly-github's or separating-context-from-constraints's content from memory as if it were this skill's own material.

**Expected baseline failures:** answers the adapter-format question directly with an invented pointer syntax, sorts the "concise" vs. "never delete" lines into bind/inform buckets itself instead of naming the boundary, no mention of either sibling skill, or the whole prompt waved off as entirely out of scope with nothing addressed.

## Results

| Date | Scenario | Run | Assertions passed | Notes |
| --- | --- | --- | --- | --- |
