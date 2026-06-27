# Error-envelope alignment + tool-catalog shrink — design

**Issues:** #135 (`fix(schemas): align error envelope with unified repair contract; drop placeholder nulls`)
and #137 (`perf(schemas): shrink preloaded tool catalog dominated by output-schema unions`).

**Delivery:** one PR (squash-merge), one `FINGERPRINT` bump (`schema-15` → `schema-16`),
breaking-change label, breaking commit marker (`!`), `CHANGELOG.md` `## [Unreleased]` entry.
Closes #135 and #137.

Developed collaboratively with Codex (a second model) across two review rounds. Round-1 refinements
are marked **(Codex R#)**; round-2 review of this spec drove the revisions marked **(Codex²)**.

## Why these two together

Both touch how output/error schemas are published from `src/codex_in_claude/schemas.py`. #137's real
fix — a success-only output schema plus *one* compact shared error envelope — is the natural moment
to also reshape `ErrorInfo` for #135. The shared compact error envelope is the seam joining them:
splitting into two PRs would publish a compact error schema based on the *old* `ErrorInfo`, then
immediately reshape it — two fingerprint bumps and a dangerous intermediate contract. One atomic PR
keeps the reshape, the central serializer, the published schema, and the resource pointer
synchronized **(Codex R4)**.

## Background (verified against code)

- Every tool's `outputSchema` is built by `_object_union_schema()` (`schemas.py:672`), wrapping a
  Pydantic `ok: true | false` union (success model `| ErrorResult`) in a top-level `type: object`.
  FastMCP embeds each tool's full self-contained JSON Schema document into `tools/list`.
- The real 16-tool wire catalog is ~180 KB (~45 K tokens) — a cold-start cost for clients that
  preload `tools/list`.
- Measured levers on the representative `codex_consult` tool (10,909 bytes, JSON-min):
  success-only **−3,055**; strip generated `title`/`description`/`default` **−4,202**;
  **both −6,354 (58%)** → 4,555 bytes.
- Largest repeated `$defs` per tool: `Meta` 2,389, `ErrorInfo` 1,951, `RateLimit` 1,822,
  `RateLimitWindow` 1,062.
- **`Meta` presence is not uniform (Codex²).** Only these success models carry a `meta: Meta` field:
  `ConsultResult`/`ReviewResult`/`DelegateResult` (via `_SuccessBase`) and `JobStarted`. The other
  success models — `StatusResult`, `CapabilitiesResult`, `ModelCatalogResult`, `JobStatus`,
  `DryRunResult`, `DelegateDryRunResult`, `JobListResult` — have **no `Meta`**; `Meta` appears in
  their published `$defs` today only because the error branch (`ErrorResult.meta`) pulls it in. This
  invalidates the round-1 plan to `$ref` `Meta` from the error branch (it would dangle). See A1.
- The error branch must stay: a returned `ErrorResult` must validate against the declared
  `outputSchema` for strict MCP clients (`schemas.py:702`); Codex confirmed MCP structured output
  must conform and strict clients may validate. We keep it but make it **fully opaque**.
- Error envelopes are constructed at **~38** sites (`server.py` 30, `delegate.py` 4,
  `orchestration.py` 3, `_worker.py` 1, `schemas.py` 1), most as
  `ErrorResult(...).model_dump(mode="json")`, serializing every optional as `null` **(Codex²)**.

## Part A — #137 catalog shrink

### A1. Fully-opaque compact error branch

Replace `_object_union_schema()` with a builder that emits, per tool, the success branch(es)
`anyOf`'d with a **fully opaque** error branch — `error` *and* `meta` opaque:

```json
{
  "type": "object",
  "required": ["ok", "error", "meta"],
  "properties": {
    "ok": {"const": false},
    "error": {
      "type": "object",
      "description": "Populated error envelope; full schema at resource codex://error-envelope"
    },
    "meta": {"type": "object"}
  }
}
```

- `error` opaque drops the ~1,951-byte `ErrorInfo` `$def` (and its `Repair`/`ErrorDetail`
  sub-objects) from every published schema.
- `meta` opaque (not `$ref`) is the **(Codex²)** fix for the dangling-ref bug: the 7 success models
  without `Meta` would otherwise reference a `$def` nothing in the success closure provides. It also
  *removes* `Meta`/`RateLimit`/`RateLimitWindow` from those 7 tools' published `$defs` entirely
  (extra shrink). The 5 tools whose success carries `Meta` keep it in `$defs` for the success
  branch, where it is genuinely described.
- The one-line `description` on `error` is an **intentional discovery pointer**, exempt from A2
  stripping, so a client that never fetches resources still learns where the full error shape lives
  **(Codex R3 / Codex²)**.

### A2. Builder algorithm (explicit) (Codex²)

Round-1 left "the `ErrorInfo` defs disappear" unspecified; the old builder copies the *whole* union
`$defs`. Specify it precisely:

```
def published_schema(*success_models):                # 1 or 3 models
    # success-only union → its $defs are EXACTLY the success closure (no error defs)
    adapter = TypeAdapter(Union[success_models]) if len(success_models) > 1 else TypeAdapter(success_models[0])
    s = adapter.json_schema()                          # minimal $defs already
    branches = s["anyOf"] if "anyOf" in s else [{k: s[k] for k in (...)}]   # success branch(es)
    doc = {
        "type": "object",
        "properties": {"ok": {"type": "boolean", "description": "...discriminator..."}},
        "required": ["ok"],
        "anyOf": [*branches, OPAQUE_ERROR_BRANCH],     # success branch(es) + ONE opaque error
        "$defs": s.get("$defs", {}),                   # success closure only
    }
    return strip_noise(doc)                            # A2 stripping; keep error-pointer description
```

- Because the opaque error branch references **no** `$def`, `$defs` is exactly the success closure —
  no manual pruning, no unreachable defs, no dangling refs.
- `JOB_RESULT_SCHEMA` passes **all three** success models
  (`DelegateResult, ConsultResult, ReviewResult`) → 3 success branches + 1 opaque error = **4
  discriminated branches** (a test asserts exactly four).

### A3. Strip generated schema noise

A recursive helper strips `title`, `description`, `default` from each published `outputSchema`
(including nested `$defs`), with the single exception of the A1 `error` pointer description. Field
semantics live in `codex_capabilities` (`use_when`, `returns`, per-field docstrings) and the
self-describing result envelope. Codex flagged blanket description removal as a modest regression
and `codex_capabilities` as an imperfect substitute (a client may never call it) **(Codex R2)**;
the accepted trade-off preserves discoverability of the one thing that truly leaves the wire — the
error shape — via the A1 pointer + the A4 resource + `COMPATIBILITY.md`.

### A4. Publish the full error schema once

Canonical, single source of truth, referenced elsewhere:

- a new MCP **resource** `codex://error-envelope` whose document root is the **full `ErrorResult`**
  (outer `ok`/`error`/`meta`, `$defs` included), served as `application/schema+json` with an
  explicit `$schema` dialect **(Codex² — resolves the root-ambiguity finding)**;
- a **pointer** in `codex_capabilities` (a stable string field
  `error_envelope_resource: "codex://error-envelope"`) — a pointer, not the embedded schema, so the
  fingerprint-cacheable capabilities payload stays small;
- prose in `COMPATIBILITY.md`, `README.md`, and `docs/REFERENCE.md` documenting the canonical error
  contract and the deliberate divergences (opaque wire branch; `details.value` never echoed).

### A5. CI catalog-size gate (Codex R5)

A test builds the real 16-tool wire catalog (mcp-shaped, compact, `mode="json"`) and asserts its
total **serialized byte size** stays under a cap — bytes are the primary deterministic gate. Token
count is reported advisorily with a pinned tokenizer, not asserted. The cap is set from the measured
post-change catalog plus ~15% headroom (the exact number fixed during implementation after
assembling the catalog).

## Part B — #135 error-envelope reshape

Target (agent-friendly-mcp §6, path (a) consolidation):

```python
RepairStep = Literal[                  # symbolic, branchable — NOT prose (Codex²)
    "retry_after_delay", "correct_arguments", "use_allowed_value", "reduce_input",
    "use_workspace_in_roots", "poll_job_status", "list_jobs", "start_new_job",
    "authenticate", "install_codex", "install_git", "init_git_repo",
    "update_plugin", "inspect_and_retry", "retry_then_report",
]

class Repair(BaseModel):
    next_step: RepairStep              # stable symbolic label
    tool: str | None = None           # was: repair_tool
    arguments: dict[str, Any] | None = None   # was: repair_tool_params
    alternative: str | None = None    # human-readable prose fallback (was: the prose `repair`)

class ErrorDetail(BaseModel):         # §6 details{field,value,reason}; value omitted by policy
    field: str | None = None          # was: offending_param
    reason: str | None = None
    allowed_values: list[str] | None = None   # was: top-level allowed_values
    # No `value` key: a Literal/string param can carry a secret and best-effort redaction cannot
    # reliably catch a plain one; the caller already holds what it sent. Documented §6 divergence.

class ErrorInfo(BaseModel):
    code: ErrorCode
    message: str
    temporary: bool                   # was: retryable — schema-REQUIRED (no default) (Codex²)
    retry_after_ms: int | None = Field(...)   # required-nullable, ge=0; always present (§6) (Codex²)
    repair: Repair | None = None      # omitted only when no corrective path exists (§6)
    details: ErrorDetail | None = None
    invalid_arguments: list[InvalidArgument] | None = None   # KEPT — multi-field carrier (B2)
    # Documented top-level extensions (unchanged; out of scope of the four-sibling fold):
    limit_bytes: int | None = None
    actual_bytes: int | None = None
    candidate_roots: list[str] | None = None
```

### B1. Renames / folds + symbolic next_step (Codex²)

`retryable`→`temporary`. The four flat siblings fold into `Repair`: `repair_tool`→`repair.tool`;
`repair_tool_params`→`repair.arguments`; the prose `repair`→`repair.alternative`; `offending_param`
and `allowed_values`→`details`. **Crucially, `next_step` is a symbolic label, not the old prose** —
§6 examples (`"unarchive_then_retry"`, `"lookup_then_retry"`) show agents branch on it. A per-error-code
mapping table (implementer fills exact `tool`/`alternative` text from the existing prose):

| ErrorCode | next_step | tool / arguments |
|---|---|---|
| `codex_rate_limited` | `retry_after_delay` | — (uses `retry_after_ms`) |
| `job_running` | `poll_job_status` | `codex_job_status{job_id}` |
| `job_not_found` | `list_jobs` | `codex_job_list` |
| `job_cancelled`, `job_timeout` | `start_new_job` | — |
| `job_failed`, `nonzero_exit`, `worktree_error` | `inspect_and_retry` | — |
| `invalid_arguments`, `invalid_scope`/`invalid_base`/`invalid_commit`/`invalid_paths`, `invalid_workspace_root` | `correct_arguments` | — |
| `unsupported_tier`/`unsupported_sandbox`/`unsupported_isolation`/`unsupported_detail` | `use_allowed_value` | — (uses `details.allowed_values`) |
| `workspace_outside_roots` | `use_workspace_in_roots` | — (uses `candidate_roots`) |
| `input_too_large`, `context_too_large` | `reduce_input` | — (uses `limit_bytes`/`actual_bytes`) |
| `codex_auth_required` | `authenticate` | — |
| `codex_not_found` | `install_codex` | — |
| `git_unavailable` | `install_git` | — |
| `not_a_git_repo` | `init_git_repo` | — |
| `cli_contract_changed`, `unexpanded_env_placeholder` | `update_plugin` | — |
| `internal_error`, `invalid_json`, `schema_violation` | `retry_then_report` | — |

### B2. `details` vs `invalid_arguments` (Codex R2 / Codex²)

`invalid_arguments: list[InvalidArgument]` is **kept** as the complete per-field carrier for the
`invalid_arguments` code (avoids losing multi-field failures). `details` is the §6 singular object;
for an `invalid_arguments` error it **deterministically mirrors the first entry** (first by Pydantic
error order). For other single-field errors `details` carries that field directly. Clients wanting
every field read `invalid_arguments`; clients wanting the §6 single-detail read `details`.

### B3. Invariants in the model (Codex R4 / Codex²)

Enforced by Pydantic on `ErrorInfo`, not the serializer:
- `temporary == False ⇒ retry_after_ms is None` (a `model_validator`);
- `retry_after_ms` is `ge=0`;
- `temporary` and `retry_after_ms` are **schema-required** (required-nullable for `retry_after_ms`)
  so the canonical resource schema (A4) matches emitted payloads — not optional-with-default, which
  would publish them as absent-capable.

### B4. Central error builder + serializer (Codex²)

A single module owns error construction and serialization — e.g. `make_error(code, message, *, repair=..., details=..., temporary=..., retry_after_ms=...) -> ErrorInfo` and
`serialize_error(ErrorResult) -> dict`. `serialize_error`:
- `model_dump(mode="json", exclude_none=True)` to strip absent optionals (§8), then
- **force-restores `error.retry_after_ms = null`** when absent (§6 keeps that key).

All ~38 construction sites route through it (`server.py`, `delegate.py`, `orchestration.py`,
`_worker.py`). A **mechanical test** asserts no `ErrorResult(...).model_dump(` call exists outside
the serializer module, preventing regressions.

### B5. Persisted-job error paths (Codex²)

- `_job_result_impl` currently returns a stored error payload **unchanged**. Change it to return
  `serialize_error(ErrorResult.model_validate(payload))` after validation, so stored errors get the
  new shape + null stripping.
- **Migration policy (user-approved): invalidate, documented.** A pre-upgrade (schema-15)
  `result.json` that fails the new `ErrorResult` validation is treated as corrupt via the existing
  `_job_result_corrupt` path (points at start-a-new-job). Jobs are TTL-bounded and short-lived, so a
  job spanning an upgrade is rare; documented in `CHANGELOG.md`/`COMPATIBILITY.md`. No translation
  shim.
- `_worker.py`'s disk-written crash error uses the new shape via `make_error`/`serialize_error`. A
  long-lived worker that writes an old-format result after a new server starts falls under the same
  invalidate-documented policy.

## §6 conformance — honest divergence list (Codex²)

Document these as deliberate house divergences; do **not** claim "full §6 alignment":
- envelope nests as `ok`/`error`/`meta` (correlation `request_id`/`fingerprint` live under `meta`,
  not in `error`);
- `details.value` is never echoed (security; §6 says include-when-safe / redact-when-sensitive);
- `invalid_arguments` is a house extension to §6 for multi-field validation;
- `repair` is omitted only when no corrective path exists (§6-faithful), but in practice every
  current `ErrorCode` maps to a `next_step` (table B1).

## Security: bounded secret non-reflection (Codex²)

Replace the single broad guarantee with bounded tests stating what is and isn't guaranteed:
1. an invalid-argument **value** never appears anywhere in the envelope (`message`, `details`,
   `repair.arguments`, `invalid_arguments[].reason`);
2. unknown-key **names** are bounded/handled per the existing `invalid_arguments` policy;
3. CLI failure text becoming `error.message` (`codex.py:256` embeds stderr/stdout) passes through
   the **existing redaction policy** first — message reflection is bounded by redaction, not an
   absolute guarantee;
4. `repair.arguments` contains only explicitly-mapped values (table B1), never raw user input.

## Testing (TDD — failing test first per unit)

1. **Builder/schema** — for **all 12** schema constants: representative success and error payloads
   validate; **every `$ref` resolves** (no dangling refs); `ok` selects the intended branch; no
   `title`/`default` and no `description` except the A1 error pointer.
2. **JOB_RESULT_SCHEMA** — exactly 4 discriminated branches; all three success types validate (B/A2).
3. **Catalog size** — assembled 16-tool wire catalog under the byte cap (A5).
4. **`ErrorInfo` model** — renames; `Repair`/`ErrorDetail` shape; `temporary`/`retry_after_ms`
   required + `ge=0`; invariant raises on `temporary=False, retry_after_ms=set` (construction-level).
5. **Serializer** — absent optionals stripped; `retry_after_ms: null` retained; round-trips for a
   retryable (backoff) and non-retryable error. Mechanical no-raw-`model_dump` test (B4).
6. **`invalid_arguments`** — multi-field error preserves every entry; `details` mirrors first
   deterministically (B2).
7. **Secret non-reflection** — the four bounded tests above.
8. **Persisted jobs** — old-format `result.json` → corrupt path; new-format error round-trips
   through `serialize_error` with `job_id`/`fingerprint` patched (B5).
9. **Resource + capabilities** — `codex://error-envelope` returns the full `ErrorResult` schema and
   validates against a real runtime error value; capabilities exposes the pointer.
10. **Regression** — every existing error-path test migrated to new field names; existing
    strict-client output-schema validation still passes; `default_errors` removal reflected.

Coverage ≥ 95% (CI floor). `ruff check`, `ruff format --check`, `ty check` must pass.

## Lockstep / surface bookkeeping

- `FINGERPRINT`: `schema-15` → `schema-16`.
- `CHANGELOG.md` `## [Unreleased]`: Changed (breaking — error field renames, output-schema shapes,
  `default_errors` removal, job-migration policy); Added (`codex://error-envelope` resource, CI
  catalog cap).
- **No** version-literal bumps (`pyproject.toml`, `.claude-plugin/plugin.json`, `.mcp.json` pin stay
  at the released version — moved only in the dedicated `chore: release` PR).
- Update **`COMPATIBILITY.md`, `README.md` (lines ~158-159), `docs/REFERENCE.md` (lines ~18-31)** to
  the new field names; include an old→new migration table **(Codex²)**.
- PR title: a **breaking** Conventional Commit (e.g. `refactor(schemas)!: …` or with a
  `BREAKING CHANGE:` footer); `breaking-change` label; `Closes #135` + `Closes #137` **(Codex²)**.

## Decisions resolved

- **`StatusResult.default_errors`: removed** (unused; un-embeds `ErrorInfo` from the status success
  schema). User-approved.
- **Job migration: invalidate, documented.** User-approved.
- **`codex://error-envelope` root: full `ErrorResult`** (outer envelope, `$defs` included).

## Out of scope (YAGNI)

- Stripping nulls from *success* envelopes (issue scopes it to errors).
- Cross-document `$ref` to share `Meta`/`RateLimit` across tools — infeasible (self-contained
  documents); the opaque-`meta` branch already removes `Meta` from the 7 Meta-less tools.
- Reducing the 16-tool count — justified by distinct tasks; the lever is per-definition size.

## Open implementation details (decide while coding, not blocking)

- Exact byte cap number (A5) — measured assembled catalog + ~15% headroom.
- Module home for `make_error`/`serialize_error` — `schemas.py` vs a small `errors.py`; pick by call-site
  readability; `_core` must not import from its parent package.
- Exact `tool`/`alternative` prose per row in table B1 — lifted from the existing repair strings.
