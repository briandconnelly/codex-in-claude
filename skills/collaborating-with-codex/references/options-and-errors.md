# Options and error recovery

Treat live tool schemas, `codex_capabilities`, and `codex_status` as authoritative. This reference
explains invariants without duplicating their full schemas.

## Optional controls

- Default to omitting `model` and `reasoning_effort`, preserving the resolution chain (per-call
  param > `CODEX_IN_CLAUDE_*` env default > Codex's own resolution). Override only for an explicit
  user requirement or a constraint you state alongside the override; when uncertain, omit.
- Effort values are backend-defined per model: interpret them within the selected model, prefer
  the model's advertised default from `codex_models`, and do not infer cost, latency, or quality
  from an effort label.
- Discover valid model slugs — and each model's advertised reasoning-effort set — with
  `codex_models` before overriding `model` or `reasoning_effort`. Codex and its backend perform
  final validation; the discovery data is advisory.
- On `invalid_reasoning_effort` (a backend-rejected effort), correct the value or omit the
  override.
- Use `isolation` only when its effect on user configuration and repository rules is intended.
- Synchronous active tools accept a bounded `timeout_seconds`; async runs use the server's job
  deadline instead.
- Use `detail="summary"` normally and `detail="full"` only for diagnostic raw output.
- Supply an `idempotency_key` when an ambiguous disconnect may require safe replay.

An idempotency key is scoped to one concrete spend-committing tool and its effective arguments.
Retrying the same tool with the same key and arguments can replay the run. Changing arguments or
switching between synchronous and async tools cannot replay it and may either fail or create new
spend.

## Recovery

On every failure:

1. Branch on `ok: false`.
2. Read `error.code`, `error.temporary`, `retry_after_ms`, and `error.repair`.
3. Correct named fields using `error.details` or `invalid_arguments` when present.
4. Call only the repair tool or retry described by the concrete error and only after its condition
   has changed.

Do not assume the advertised error-code list is exhaustive. Do not echo rejected values from an
error; supplied values may contain secrets. On a setup failure, call free `codex_status` again and
require both readiness conditions before another paid attempt.

`CODEX_IN_CLAUDE_EXTRA_ARGS` is operator configuration applied to paid calls. `codex_status` reports
whether it is configured and valid without exposing its values. If `extra_args_valid` is false, no
paid call can pass preflight even when `ready` is true.
