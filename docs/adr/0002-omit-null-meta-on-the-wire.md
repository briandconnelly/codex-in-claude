# ADR 0002: Omit null `meta` members from delivered success envelopes

**Status:** Accepted (2026-07-27)

## Context

Roughly 40% of a success envelope on the wire was null-valued `meta` keys.
Measured against the committed representative fixtures: consult 810 B, review 1,028 B, delegate
823 B, of which ~18 `meta` members per envelope were explicit nulls (`session_id`, `usage`,
`rate_limit`, `context_summary`, `scope`, `base`, `commit`, `paths`, …).
Every consult, review, delegate, and retrieved job result paid this on every call, which undercut
`detail="summary"`'s purpose.

Three things constrained the fix, and each was checked rather than assumed:

- **Do the published schemas accept absence?** Yes. Validated with a JSON Schema validator against
  the advertised `outputSchema`s and `codex://result-meta`, not merely against the Pydantic models;
  the same validator was confirmed to reject an added key, a removed `summary`, and a retyped
  `summary`, so the passing result is evidence rather than a blind instrument.
- **Would it disturb stored results?** Only if the *persisted* shape moved. Bumping `RESULT_FORMAT`
  makes `server.py`'s replay check treat every already-stored `result.json` as
  `job_result_incompatible`.
- **Where can it even be applied?** Every `dump_success` call site runs in the worker process
  writing `result.json`. The only route back to a caller is `_finished_job_envelope` →
  `apply_detail`, shared by the synchronous await path and the job replay/consume paths.

## Decision

Omit null-valued members of `meta`, and only `meta`, from **delivered** success envelopes of
`codex_consult`/`codex_review_changes`/`codex_delegate`.
Absence carries the same meaning the explicit null did.

Applied at the single delivery chokepoint, after `apply_detail`. `dump_success` is unchanged.

Four carve-outs:

1. **Empty collections are retained.**
2. **Everything outside `meta` is delivered verbatim** — top-level fields and all of `raw_response`.
3. **Only the three result envelopes slim**, keyed structurally on the `tool` discriminator.
4. **Stripping keys on `is None`, never on falsiness.**

Versioning: `FINGERPRINT` bumps (`schema-60` → `schema-61`); the change is **not breaking**;
`RESULT_FORMAT` stays at `7`.

## Rationale

**Wire-only rather than changing the serializer.** Slimming in `dump_success` would bump
`RESULT_FORMAT` and strand every stored job result for no compatibility gain. Delivering through
one chokepoint instead means the synchronous and replayed shapes are identical *by construction*
rather than by convention, `result.json` keeps its full forensic detail, and payloads stored by
older releases are slimmed on read too.

**Why `meta` only, when omitting every null saves ~4 percentage points more.** The extra scope
would have cost three guarantees for very little:

- `codex_delegate` emits `diff=diff or None`, so a no-changes run stores `diff: null`. Dropping
  that key removes the field the result's own `next_steps` tells the caller to review, and
  `result["diff"]` is the natural access pattern — a `KeyError` exactly when an agent is least
  prepared for one.
- `apply_detail` promises `raw_response` stays present with its `text` nulled. Slimming inside it
  would weaken a documented guarantee, which this repo classifies as breaking.
- Confining the rule to `meta` states in one sentence, and `meta` is where the nulls actually were.

**Why empty collections stay.** `Coverage` enforces `status == "partial"` *iff* `omission_reasons`
is non-empty, so an empty array is the machine-checkable half of a validated invariant, not noise.
`findings: []` must also stay iterable.

**Why scoping is structural, not prose.** `JobStarted` is `ok: Literal[True]` and carries a full
null-laden `Meta`; `JobStatus`/`JobSummary` carry `result_ok`, a required nullable documented
"always present, never omitted" with a regression test guarding it. Keying on the `tool`
discriminator makes the boundary impossible to cross by accident.

**Why not breaking.** No field is removed from discovery, retyped, or narrowed; no required input
is added; no documented guarantee weakens. The published schemas deliberately do not require any
affected property, so a client that assumed unconditional key presence was relying on more than the
contract offered. The two `meta` descriptions that assigned null a documented meaning (`model`,
`reasoning_effort`) are published un-stripped through `codex://result-meta` and were reworded to
"absent or null means…" in the same change, so no published statement became false.

**Why a new snapshot.** Neither existing acknowledgment guard can see a wire-only change: the
manifest snapshot captures schemas, and the result-format snapshot renders only through
`dump_success`/`serialize_error`. `tests/fixtures/wire_shape_snapshot.json` pins the delivered
shape and records exactly which keys each envelope loses, so the change — and any future drift —
is reviewable instead of invisible.

## Consequences

Clients testing `"session_id" in result["meta"]` observe a behavior change; clients using `.get`
do not. The rule is published on `codex://result-meta` so it is discoverable rather than only
diffable, and `docs/REFERENCE.md` documents it beside the matching error-envelope convention.

The non-envelope result surfaces (`StatusResult`, the dry-run results, `TransferResult`,
`JobListResult`, job status) still send their nulls.
That was audited in #389 and the answer is **no further surface slims** — the rule stops where this
ADR left it.
Recorded here so the ground is not re-derived (byte figures measured against live calls at
`schema-61`; they will drift, the reasoning will not):

- **Three surfaces cannot slim without breaking a published statement.** `JobStatus`/`JobSummary`
  document `result_ok` as "Always present (null-meaningful), never omitted", and `codex_job_status`'s
  tool description separately promises `expires_at` "is null while running" — so preserving
  `result_ok` alone is not sufficient. A *recursive* exclusion on `StatusResult` reaches `RateLimit`,
  where `docs/REFERENCE.md` § Rate-limit reporting publishes both "reports `limiting_window: null`
  while still showing the windows" and "tri-state, and null is *not* false" for
  `spend_control_reached`. Weakening any of these is breaking under AGENTS.md § Versioning.
- **Three more are no-ops.** `JobListResult` already hand-omits its only eligible key
  (`truncation_hint`); `TransferResult` has no production-reachable top-level null on the success
  path; `JobStarted` is published as explicitly not-slimmed on `codex://result-meta`.
- **The two dry-run previews were the only real candidates, and lose on cost.** Measured against
  live calls, top-level omission saves 111 B (14.1%) on `codex_dry_run` and 62 B (6.9%) on
  `codex_delegate_dry_run` — roughly 30 and 17 tokens on free, typically one-shot previews. The
  price is a `FINGERPRINT` bump, which `docs/REFERENCE.md` documents as a **client cache key**, plus
  a third serialization convention (envelope-`meta` slimming, hand-omission, and a per-preview
  allowlist) for every future contributor to hold. Not worth it.

#389's own headline — `StatusResult` at "33.2%" — does not reproduce on any constructible payload:
it was measured without `caveat`, which is required, has no default, and is ~828 B, i.e. **40.6% of
a live `codex_status` response**. The real top-level saving there is **44 B (2.2%)**, making the
most semantically hazardous surface also the least valuable one.

One trap for anyone revisiting this: the `_job_status_model(...).model_dump(...)` call at the second
job-status site serves `codex_job_cancel`, not a second read path, so a change there moves two tools'
responses.
