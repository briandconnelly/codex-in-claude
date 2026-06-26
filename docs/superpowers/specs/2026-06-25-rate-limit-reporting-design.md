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
against each window's own `resets_at`, which makes a stale file self-correcting:

- **Per-window reset awareness.** If `now > resets_at` for a window, that window
  has certainly rolled over since capture → report it as `replenished`
  (`remaining_percent` treated as full / `reset_passed: true`), **not** as a stale
  high-usage number. This is what makes the 5-hour window safe: a snapshot older
  than 5 hours is recognized as reset, not shown as "90% used".
- **Hard expiry at the weekly window.** Once even the secondary window's reset has
  passed (≈7 days; both windows have definitely rolled over), the file carries no
  real information → report `status: "unknown"` plus a "run any Codex call to
  refresh" hint, instead of misleading numbers. The snapshot is self-expiring.
- **Always-visible freshness.** `as_of`, `age_seconds`, and `is_stale` (a
  configurable warn threshold) ride along on every report, plus a one-line caveat.
  Stale-but-pre-reset data is shown, clearly labeled — never silently presented as
  live.
- **Provenance guard.** Persist `CODEX_HOME` (resolved config root) and `plan_type`
  with the snapshot. If either differs from the current environment at read time,
  mark the snapshot `unverified` rather than trusting it (it may be from a previous
  login or a changed plan).
- **Clock-skew defense.** `resets_at` is advisory: compute `seconds_until_reset`
  defensively and clamp negatives to `0`. Never derive a hard guarantee from it.

Net: the disk snapshot can get old, but it cannot *mislead* — old short-window data
reports as `replenished`, very old data downgrades to `unknown`, and freshness is
always on the label.

## Agent-visible schema

A `rate_limit` block on both `StatusResult` and `Meta`. Each window is an object.

```
status:          available | limited | exhausted | replenished | unknown
as_of:           ISO-8601 capture time
age_seconds:     int
is_stale:        bool (past the warn threshold)
source:          "plugin_cache"
plan_type:       str | null
unverified:      bool (provenance mismatch — CODEX_HOME/plan_type differs)
limiting_window: "primary" | "secondary" | null   (the binding constraint:
                 lowest remaining, or whichever reached its limit)
primary:   { used_percent, remaining_percent, window_minutes, resets_at,
             seconds_until_reset, reset_passed }   # 5-hour window
secondary: { used_percent, remaining_percent, window_minutes, resets_at,
             seconds_until_reset, reset_passed }   # weekly window
```

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
- Interpretation matrix: fresh snapshot; primary reset passed → `replenished`;
  both resets passed / age past weekly → `unknown`; provenance mismatch →
  `unverified`; clock skew → `seconds_until_reset` clamped to 0.
- Persistence: write best-effort; a write failure does not fail the call; corrupt
  file degrades to `unknown`.
- `codex_status` with and without a cached snapshot.
- 95% coverage floor; live behavior covered by existing `integration`-marked tests
  where relevant.

## Out of scope

- Reading `~/.codex/sessions` rollout files (B2/B3).
- Any paid refresh path.
- Snapshot history or trend reporting.
