# Design: polled event-activity signal for async jobs (#139)

**Status:** approved (Claude + Codex agreed; Codex session `019f0b97`).
**Issue:** [#139](https://github.com/briandconnelly/codex-in-claude/issues/139) — `feat(jobs): expose progress/phase signal for long-running jobs`.
**Severity:** Minor enhancement, `priority: low`.

## Problem

A long-running async job (`codex_delegate_async`, `codex_consult_async`, `codex_review_changes_async`) is indistinguishable from a stalled one until its deadline.
`JobStatus` carries `status`, `elapsed_ms`, `poll_after_ms`, and `deadline_seconds`, but nothing that tells a polling agent whether the job is *currently producing output* or has gone quiet.

The honest gap is **"running-and-producing-output" vs "running-but-silent"** — *not* alive-vs-dead.
Alive-vs-dead is already answered: `JobStore` verifies the detached worker via its advisory lock / owned-child PID and reports `running` accordingly (`_core/jobs.py:409-450`).
`elapsed_ms` only shows *total* runtime, which cannot distinguish a job mid-work from one that froze a minute ago.

A detached job cannot emit request-bound `notifications/progress` after the launcher returns, so any signal must be **persisted to disk and polled**, consistent with the existing `codex_job_*` lifecycle.

## Decision

Surface a **polled event-activity signal** derived from Codex's `--json` event stream.
Explicitly **do not** add named phases, and **do not** add `taskSupport:"forbidden"` (see Rejected alternatives).

### Agent-visible surface

Three new **advisory** `JobStatus` fields:

| Field | Type | Meaning |
|-------|------|---------|
| `events_seen` | `int` | Monotonic count of Codex `--json` events observed for this job. |
| `last_event_at` | `str \| None` | ISO-8601 timestamp of the most recent observed event (`None` before the first event). |
| `event_age_ms` | `int \| None` | `now − last_event_at` in ms (`None` before the first event) — the "is it quiet?" signal. |

These are **advisory**.
Codex does not emit events at a fixed cadence, so silence is *not* proof of a stall, and **nothing auto-cancels** on these values.
They add information over `elapsed_ms` by showing *recent* activity rather than total runtime.
Naming follows existing `JobStatus` conventions (`started_at`/`expires_at` for ISO strings, `elapsed_ms` for durations).

`AsyncLifecycle` gains a **separate** discovery field — `activity_support: "codex_events"` — plus the names of the three new fields, so a client can discover the polled signal structurally.
`progress_support` stays `"none"`: it specifically denotes native MCP `notifications/progress`, which this server still does not provide, and overloading it would mislead capability consumers.

### Internals

1. **`_core/runtime.py` — observer-gated streaming.**
   Add an optional CLI-agnostic stdout line-observer callback to `run_async`.
   - **No observer (synchronous paid tools `codex_consult` / `codex_review_changes` / `codex_delegate`):** keep the existing `proc.communicate()` path **byte-for-byte** — zero behavior change, zero regression risk on the most-used paid paths.
   - **Observer supplied (the detached worker only):** use a new concurrent stdout/stderr line-draining path that invokes the callback per stdout line, while preserving stdout/stderr byte fidelity, the timeout, MCP cancellation, process-group teardown, and the *complete* captured streams (final metadata parse is unchanged).
   Cost: two code paths in `run_async` — an accepted regression-containment tradeoff.

2. **`codex.py` — thread the observer through.**
   `run_codex_exec` accepts and forwards the optional observer to `run_async`.
   The synchronous tool paths pass no observer.

3. **`_worker.py` — record activity.**
   The detached worker passes an observer that, per observed event, updates `<job_dir>/activity.json` with **counters and timestamps only** — `{events_seen, last_event_epoch}`.
   **Raw events are never persisted** (they can contain model messages / task content; only the final `result.json` retains output, as today).
   Writes are **throttled** (coalesced, e.g. by a small interval/count) and **atomic** (temp-file + replace, matching `_atomic_write`), with a **final flush** so the last event count is durable.

4. **`_core/jobs.py` — read it as an opaque file.**
   `JobStore` reads `activity.json` when building a status dict and maps it into the new fields, deriving `event_age_ms` from `last_event_epoch` at read time.
   `_core` owns this storage contract and imports nothing from the parent package — the one-way `_core` boundary is preserved (the worker, in the parent package, is the only writer; `JobStore` is an opaque reader).
   A missing/corrupt/absent `activity.json` degrades gracefully to `events_seen: 0`, `last_event_at: None`, `event_age_ms: None`.

### Concurrency / safety

- Single writer (the job's own worker), one or more concurrent `JobStore` readers across polls.
- Atomic replace means a reader sees either the old or the new file, never a torn write.
- A corrupt/missing file is treated as "no activity yet," never an error.

## Rejected alternatives

- **Named phases** (e.g. `preparing`/`running`/`finalizing` derived from Codex event types).
  Rejected: a phase taxonomy couples our contract to Codex's event-type schema, which `cli_contract.py` deliberately treats as unstable ("we never depend on a specific event shape").
  Plugin-*owned* phases for non-Codex work could be revisited later, separately.
- **`progress_support` flipped to `"heartbeat"`.**
  Rejected: it denotes native `notifications/progress`; persisted event activity does not provide those, and overloading the value would mislead capability consumers. A separate `activity_support` field is used instead.
- **`execution.taskSupport:"forbidden"`.**
  Rejected for this issue: absent `taskSupport` already defaults to forbidden under the installed MCP model, and FastMCP's `tool` decorator exposes no clean `execution` hook, so forcing it would add framework coupling without changing client semantics. Revisit separately only if a stable API appears.
- **Single unified streaming path in `run_async`** (replace `communicate()` for all callers).
  Rejected in favor of the observer-gated path: routing every paid call through new draining code widens the regression surface onto the synchronous paid tools for no functional gain here.

## Release / contract impact

- Bump `FINGERPRINT` (agent-visible surface changed: new `JobStatus` fields, new `AsyncLifecycle` field).
- Regenerate `tests/fixtures/manifest_snapshot.json`; update `AsyncLifecycle` docstring and `CHANGELOG.md` under `## [Unreleased]`.
- **No** version-literal bump (`pyproject.toml` / `.claude-plugin/plugin.json` / `.mcp.json` pin stay at the released version) — this is a feature PR, not the release PR.
- Breaking change pre-1.0 ⇒ minor; mark `feat(jobs)!` / `BREAKING CHANGE:` and the `breaking-change` PR label.

## Testing (TDD; 95% floor)

Write failing tests first, covering:
- `run_async` observer path: callback invoked per stdout line; byte-identical stdout/stderr vs the `communicate()` path; large simultaneous stdout+stderr (no deadlock); timeout and cancellation still tear down the process group; no-observer path unchanged.
- Worker: `activity.json` written with monotonic `events_seen`, throttling coalesces writes, final flush records the last count, raw events never written.
- `JobStore`: zero events, multiple events, missing/corrupt `activity.json`, monotonic counts across polls, `event_age_ms` derivation, concurrent reads during a poll.
- Schema/manifest: new fields present and advisory-documented; `FINGERPRINT` changed; manifest snapshot regenerated; `progress_support` still `"none"`; `activity_support:"codex_events"` present.
