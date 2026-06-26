# Rate-limit quota reporting in `codex_status` (Plan B1)

**Status:** approved design, pre-implementation
**Date:** 2026-06-25

## Problem

An agent (or user) deciding whether to call Codex has no view of how much of the
account's Codex rate-limit quota remains.
Knowing "the weekly window is nearly exhausted" or "the 5-hour window is fine"
would meaningfully inform a "should I use Codex now?" decision.

`codex_status` is the natural home for this — but it is contractually **free /
local-only**: it must never trigger `codex exec`, `codex login`, or any network/model work.

## Key constraint (verified)

Remaining-quota data is **not** available from any free `codex` command.
It arrives **only** as a side effect of a real (billable) `codex exec` turn,
embedded in the `--json` JSONL stream's `token_count` event at `payload.rate_limits`:

```json
"rate_limits": {
  "limit_id": "codex", "limit_name": null,
  "primary":   {"used_percent": 12.0, "window_minutes": 300,   "resets_at": 1780534461},
  "secondary": {"used_percent": 8.0,  "window_minutes": 10080, "resets_at": 1780864628},
  "credits": null, "plan_type": "plus", "rate_limit_reached_type": null
}
```

- `primary` = the 5-hour window (`window_minutes: 300`).
- `secondary` = the weekly window (`window_minutes: 10080` = 7 days).
- `used_percent` is **account-wide** usage of that window as of the call — not just
  this plugin's spend. So the *value* reflects all Codex usage (TUI included);
  only its *freshness* depends on plugin calls.
- `resets_at` is epoch seconds.
- Codex does not cache this in any documented/stable place; only per-session
  rollout JSONL files under `~/.codex/sessions/.../*.jsonl` retain it.

## Approach: B1 — opportunistic plugin cache

Capture `payload.rate_limits` from the JSONL we already parse on every paid call,
persist the latest snapshot to a plugin-owned file, and report it from
`codex_status` (and on active-call `Meta`) with zero extra spend.

### Rejected alternatives

- **B2 / B3 (read `~/.codex/sessions` rollout files):** rejected for the initial
  surface. Reading raw transcripts (which may contain prompts, outputs, file paths,
  or secrets) and coupling to an undocumented session-file layout is a privacy and
  stability liability disproportionate to the incremental freshness gained. May be
  reconsidered later as an explicit opt-in source, isolated behind the CLI-contract
  boundary, if first-run staleness proves to be a real problem.
- **Paid refresh probe (Option C):** rejected — breaks the free/local-only contract
  of `codex_status`.
- **History / multiple snapshots:** YAGNI. Latest-only.

## Data flow

1. **Capture.** `normalize.parse_event_metadata` already walks the `token_count`
   event for token usage; extend it to also return a `RateLimitSnapshot` parsed
   from the sibling `rate_limits` block. Tolerant parsing: a missing/renamed field
   degrades to `None`, never raises.
2. **Persist.** On any successful call (`consult` / `review` / `delegate`, sync +
   async) that yields a snapshot, write it last-wins to a single JSON file at
   `~/.cache/codex-in-claude/rate_limit_snapshot.json` (sibling of the `jobs/`
   store; honors `CODEX_IN_CLAUDE_STATE_DIR` and `XDG_CACHE_HOME`). The write is
   best-effort: a failure to persist must never fail the underlying call.
3. **Report — `codex_status`.** Read the file (free, local), interpret it against
   the current clock and environment (below), and include a `rate_limit` block.
4. **Report — `Meta`.** Active calls also attach the *live* snapshot to
   `Meta.rate_limit`, so quota is visible exactly when spend happens.

## Staleness mitigation (core design point)

A snapshot is **never** read as a flat "current" number. It is always interpreted
against each window's own `resets_at`. The governing principle (refined after a
cross-model review): **a window whose current usage is unobserved must never produce
a positive "go ahead and spend" signal.** Within a window, usage only climbs until
the reset, so a captured `used_percent` is a *lower bound* on current usage — which
means low-remaining verdicts are conservative (safe even when stale), but
high-remaining verdicts are optimistic and only trustworthy for a still-open window.

This yields a deliberately asymmetric status rule:

- **`available` is earned, not assumed.** Reported only when **both** windows are
  present, **not** reset-passed, carry a usable `resets_at`, and sit above the
  threshold. If any window has rolled over, is missing, or lacks `resets_at`, its
  current state is unobserved and could be the binding constraint → `unknown`, never
  `available`. (There is no `replenished` "healthy" status: a window past its reset
  tells us nothing about post-reset usage, so claiming health would be unsound.)
- **Risk signals are conservative and survive staleness.** `limited`
  (remaining < 25%) and `exhausted` (remaining ≤ 0, or `rate_limit_reached_type`
  names that window) are derived only from still-open windows; because captured
  usage is a lower bound, these verdicts only err toward caution.
- **`rate_limit_reached_type` selects the window.** When Codex names the window that
  hit its limit, `limiting_window` reports that exact window (when still open); if
  the named window has since reset or is absent, the snapshot is no longer actionable
  → `unknown`.
- **Reset-passed windows null their percentages.** A rolled-over window sets
  `reset_passed: true`, `seconds_until_reset: 0`, and **nulls**
  `used_percent`/`remaining_percent` — there is one source of truth, so a present
  percentage always means "current-ish," never "obsolete."
- **Always-visible freshness.** `as_of`, `age_seconds`, and `is_stale` (a
  configurable warn threshold) ride along, plus a `note`. `unknown` with no data
  carries a "run any Codex call to refresh" hint.
- **Provenance guard (CODEX_HOME-only).** Persist `CODEX_HOME` (resolved config
  root) and `plan_type`. If the cached `CODEX_HOME` differs from the current
  environment, set `home_unverified` (honestly named — it does **not** verify the
  account or current plan, which have no free source). `plan_type` is captured
  metadata, not a verified current-plan assertion.
- **Clock-skew defense.** `resets_at` is advisory: compute `seconds_until_reset`
  defensively and clamp negatives to `0`.
- **Tolerant reads.** The cache envelope is type-validated before interpretation
  (`captured_at` must be numeric, `codex_home` a string); a corrupt or
  hand-edited file degrades to `unknown` rather than raising — `codex_status` must
  never crash.
- **Atomic writes.** The cache is written via a unique temp file + `replace`, so a
  concurrent paid call or a `codex_status` read never observes a truncated file.

Net: the disk snapshot can get old, but it cannot *mislead* — an unobserved window
never yields `available`, risk signals stay conservative, and freshness/provenance
are always on the label.

## Agent-visible schema

A `rate_limit` block on both `StatusResult` and `Meta`. Each window is an object.

```
status:          available | limited | exhausted | unknown
source:          "current_run" (live, on Meta) | "plugin_cache" (from codex_status)
as_of:           ISO-8601 capture time
age_seconds:     int
is_stale:        bool (past the warn threshold)
plan_type:       str | null   (captured metadata, not a verified current plan)
home_unverified: bool (cached CODEX_HOME differs from the current environment)
limiting_window: "primary" | "secondary" | null   (the binding constraint:
                 the window reaching its limit, or lowest remaining among open windows)
note:            str | null   (e.g. refresh hint, or which window is unobserved)
primary:   { used_percent, remaining_percent, window_minutes, resets_at,
             seconds_until_reset, reset_passed }   # 5-hour window
secondary: { used_percent, remaining_percent, window_minutes, resets_at,
             seconds_until_reset, reset_passed }   # weekly window
```

(`used_percent`/`remaining_percent` are null on a `reset_passed` window.)

- `remaining_percent = max(0, 100 - used_percent)` is the decision-oriented value;
  raw `used_percent` is retained because it is what Codex actually emitted.
- When no snapshot has ever been captured, `codex_status` reports
  `rate_limit: { status: "unknown", ... }` with a hint to run any Codex call,
  rather than omitting the field silently.

This block is a new agent-visible surface → **bump `FINGERPRINT`** and bump the
release-coordination version set (`pyproject.toml`, `.claude-plugin/plugin.json`,
the `.mcp.json` PyPI pin, `CHANGELOG.md`).

## Touched modules

- `cli_contract.py` — the `rate_limits` field names (`primary`/`secondary`/
  `used_percent`/`window_minutes`/`resets_at`/`plan_type`) live here as a CLI
  assumption, consistent with the rest of the contract.
- `normalize.py` — extract the `RateLimitSnapshot` alongside `Usage`.
- `rate_limit.py` (new, small, isolated) — persist, load, and **interpret** a
  snapshot (the per-window reset / staleness / provenance logic). One clear
  purpose, independently testable.
- `schemas.py` — `RateLimitWindow` + `RateLimit` models; `Meta.rate_limit` and
  `StatusResult.rate_limit`; `FINGERPRINT`.
- `orchestration.py`, `delegate.py` — capture-and-persist after a successful call;
  attach live snapshot to `Meta`.
- `server.py` `codex_status` — read + interpret + include the block.
- `config.py` — snapshot-file path helper; `is_stale` warn-threshold knob.
- `CHANGELOG.md` — note under `## [Unreleased]`.

## Testing

- TDD: failing test first, then minimal code.
- Parsing: `rate_limits` extracted from a representative `token_count` event;
  tolerant degradation on missing/renamed fields.
- Interpretation matrix: both windows open + healthy → `available`; an open window
  below threshold → `limited`/`exhausted`; one window reset-passed → `unknown` (never
  `available`); both reset-passed → `unknown`; `rate_limit_reached_type` naming an
  open vs reset window; a window missing `resets_at` → `unknown`; provenance mismatch
  → `home_unverified`; clock skew → `seconds_until_reset` clamped to 0; corrupt cache
  envelope → `unknown` without raising.
- Persistence: write best-effort; a write failure does not fail the call; corrupt
  file degrades to `unknown`.
- `codex_status` with and without a cached snapshot.
- 95% coverage floor; live behavior covered by existing `integration`-marked tests
  where relevant.

## Out of scope

- Reading `~/.codex/sessions` rollout files (B2/B3).
- Any paid refresh path.
- Snapshot history or trend reporting.
