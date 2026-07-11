# Examples

Worked shapes for a fictional small repo, `lighthouse` (a billing service), and a fictional monorepo, `orbit` (an API package plus a web package).
Use these as concrete shapes to mimic; cross-reference [docs-checklist.md](docs-checklist.md) for the rules they instantiate and [SKILL.md](../SKILL.md) Vocabulary for the layer names used in the annotations below.

## 1. Layered small-repo layout

`lighthouse` is small enough to fit every layer in one tree.
Each entry below is annotated with the layer it belongs to, per SKILL.md Vocabulary.

```
lighthouse/
├── AGENTS.md                    # instruction layer — canonical, always loaded
├── CLAUDE.md                    # instruction layer — per-harness adapter, points at AGENTS.md
├── README.md                    # orientation layer — entry point and map
├── docs/
│   ├── architecture.md          # orientation layer — system map, read when orienting
│   ├── api-reference.md         # reference layer — loaded only when a task touches the API
│   └── testing.md               # reference layer — loaded only when running or writing tests
├── adr/
│   ├── 0001-use-postgres.md               # decision history — why, not current policy
│   ├── 0002-async-job-queue.md            # decision history — superseded by 0003 (see section 4)
│   └── 0003-replace-rabbitmq-with-kafka.md # decision history — supersedes 0002
└── src/
    └── billing/
        └── invoice.py           # code-adjacent context — comments here carry constraints coupled to this file, not repo-wide claims
```

Two things this layout makes visible at a glance:

- Nothing in `docs/` is always-loaded; an agent reaches `api-reference.md` or `testing.md` only by following a link from `README.md` or `AGENTS.md` when the task calls for it.
- `adr/` sits outside both the instruction and orientation layers; an agent that finds an ADR by search rather than by link should still read its status line before treating its decision as live (see section 4 below).

## 2. Monorepo layout with nested context files

`orbit` has two packages with different toolchains, so the instruction layer splits: a root file for cross-cutting norms and a nested file per package for what differs.

```
orbit/
├── AGENTS.md                     # instruction layer — root: cross-cutting norms only (commit format, PR conventions, shared CI gate)
├── CLAUDE.md                     # instruction layer — root adapter, points at AGENTS.md
├── README.md                     # orientation layer — root map, links to each package's README
├── docs/
│   └── architecture.md           # orientation layer — cross-package system map, root only
├── packages/
│   ├── api/
│   │   ├── AGENTS.md             # instruction layer — package-scoped: Go toolchain, `go test ./...`, owns packages/api/
│   │   ├── README.md             # orientation layer — package map
│   │   └── ...
│   └── web/
│       ├── AGENTS.md             # instruction layer — package-scoped: pnpm toolchain, `pnpm test`, owns packages/web/
│       ├── README.md             # orientation layer — package map
│       └── ...
```

What belongs at which level: cross-cutting norms (commit format, review expectations, the label taxonomy) live only in the root `AGENTS.md`; anything that differs by package — build command, test command, ownership, off-limits paths — lives in that package's nested `AGENTS.md` and overrides the root only where it differs.
An agent working in `packages/api/` reads both files: the root file for what's always true, the nested file for what's true here.

This example shows the resulting shape only.
The strategy question — why nest per-package instruction files at all, and how the `CLAUDE.md` adapter mechanics work — is owned by the agent-friendly-github skill's Agent-Instruction-File Strategy section; this skill does not restate those rules.

## 3. README de-bloat before/after

`lighthouse`'s `README.md` started as the only doc anyone wrote, so it accumulated reference-layer weight: a full endpoint-by-endpoint API reference sitting inside an orientation-layer doc.

**Before** (excerpt — the full file ran to roughly 2,400 words, most of it API detail):

````markdown
# lighthouse

A billing service.

## Setup

...

## API

### GET /invoices

Returns a paginated list of invoices. Query params: `status`, `cursor`, `limit`.

Response:
```json
{"items": [...], "next_cursor": "...", "has_more": true}
```

### POST /invoices

Creates an invoice. Body: `{"customer_id": "...", "line_items": [...]}`.

Response:
```json
{"id": "inv_123", "status": "draft", ...}
```

### PATCH /invoices/:id

... (12 more endpoints follow, each with request/response bodies)
````

**After** — the API detail moves to `docs/api-reference.md`; `README.md` keeps only what every task needs to orient, plus a pointer:

```markdown
# lighthouse

A billing service.

## Setup

...

## API

Full endpoint reference: [docs/api-reference.md](docs/api-reference.md).
Load it only when a task touches the API surface directly.
```

Token-cost rationale: before the split, `README.md` was loaded on every orientation pass at roughly 2,400 words, even for tasks — fixing a typo, updating a test — that never touch the API.
After the split, `README.md` is roughly 400 words; the 2,000 words of endpoint detail sit behind an explicit link in `docs/api-reference.md`, loaded only when a task actually needs it.
The fact didn't move to a worse home — it moved to the layer that matches how often an agent actually needs it.

## 4. ADR header with status and supersession

`lighthouse` chose RabbitMQ for its async job queue in ADR 0002, then replaced it with Kafka in ADR 0003.
Both headers carry a `Status` line an agent can read before the surrounding prose.

`adr/0002-async-job-queue.md`:

```markdown
# ADR 0002: Use RabbitMQ for the async job queue

Status: Superseded by [ADR 0003](0003-replace-rabbitmq-with-kafka.md)

## Context

...
```

`adr/0003-replace-rabbitmq-with-kafka.md`:

```markdown
# ADR 0003: Replace RabbitMQ with Kafka for the async job queue

Status: Accepted
Supersedes: [ADR 0002](0002-async-job-queue.md)

## Context

...
```

How an agent should read this: on opening `0002-async-job-queue.md`, the `Status` line is the first fact after the title, before any context or rationale prose.
`Superseded by` means the decision in this ADR is historical, not current policy; the agent follows the forward link to `0003-replace-rabbitmq-with-kafka.md` and treats that ADR's decision as live instead.
If current code or a current-policy doc (e.g., `docs/architecture.md`) still describes RabbitMQ, that is itself a finding — the ADR's content was never promoted forward when the decision changed.

## 5. PR doc-update convention

Freshness in `lighthouse` is enforced two ways: a PR template line that prompts the doc update, and a CODEOWNERS line that routes doc changes to a reviewer who can catch a missing one.

`.github/pull_request_template.md` (excerpt):

```markdown
## Checklist

- [ ] Updated the doc that describes this behavior (README, `docs/`, or the relevant `AGENTS.md`), or marked N/A with a one-line reason.
- [ ] Tests pass locally (`pytest`).
```

`CODEOWNERS` (excerpt):

```
docs/       @lighthouse-org/docs-owners
AGENTS.md   @lighthouse-org/docs-owners
adr/        @lighthouse-org/docs-owners
```

The checklist line makes the expectation visible on every PR; the CODEOWNERS line makes it enforceable — a PR that touches behavior without touching the doc still routes past someone who owns the doc and can ask for the update before merge.

## 6. Harness adapter syntax

As of 2026-07.
Include and reference syntax for coding-agent instruction files changes across tools without much notice — verify the current syntax against your harness's own documentation before relying on the specifics below.

This is the only section in this skill's references where harness-specific syntax appears; everywhere else, "canonical instruction file" and "per-harness adapter" are used generically (see [SKILL.md](../SKILL.md) Vocabulary).
The authoritative home for the adapter pattern — including the `CLAUDE.md` include shape below — is the agent-friendly-github skill's Agent-Instruction-File Strategy section; this section is a dated illustration for doc-surface design, and if the two ever disagree, that skill wins.

For Claude Code, the entire content of `CLAUDE.md` is a single line, and Claude Code resolves the reference automatically at load time:

```text
@AGENTS.md
```

Other harnesses use their own mechanism, and some may not support a live include at all:

- A tool whose instruction file supports an include directive: point it at the canonical file the same way, using that tool's own syntax rather than `@`-include — do not assume the syntax is identical across tools.
- A tool whose instruction file has no include mechanism: the adapter is a short file manually kept in sync with the canonical file instead of a live pointer, and the sync burden is the tradeoff for supporting that harness.

Confirm which case a given harness falls into from its current docs before choosing the adapter shape.
