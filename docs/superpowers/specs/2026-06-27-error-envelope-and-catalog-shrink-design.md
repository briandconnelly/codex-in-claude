# Error-envelope alignment + tool-catalog shrink — design

**Issues:** #135 (`fix(schemas): align error envelope with unified repair contract; drop placeholder nulls`)
and #137 (`perf(schemas): shrink preloaded tool catalog dominated by output-schema unions`).

**Delivery:** one PR (squash-merge), one `FINGERPRINT` bump (`schema-15` → `schema-16`),
breaking-change label, `CHANGELOG.md` `## [Unreleased]` entry. Closes #135 and #137.

This design was developed collaboratively with Codex (a second model) and refined against its
critique; the refinements are called out inline as **(Codex R1–R5)**.

## Why these two together

Both touch how output/error schemas are published from `src/codex_in_claude/schemas.py`. #137's real
fix — publish a success-only output schema plus *one* compact, shared error envelope — is the natural
moment to also reshape `ErrorInfo` for #135. The shared compact error envelope is the seam that joins
them: splitting into two PRs would publish a compact error schema based on the *old* `ErrorInfo`, then
immediately reshape it — two fingerprint bumps and a dangerous intermediate contract. One atomic PR
keeps the reshape, the central serializer, the published schema, and the resource pointer synchronized
**(Codex R4)**.

## Background (verified against code)

- Every tool's advertised `outputSchema` is built by `_object_union_schema()`
  (`schemas.py:672`), which wraps a Pydantic `ok: true | false` union (a success model
  `| ErrorResult`) in a top-level `type: object`. FastMCP embeds each tool's full, self-contained
  JSON Schema document into `tools/list`.
- The real 16-tool wire catalog is ~180 KB (~45 K tokens). A client that preloads `tools/list`
  spends that before its first useful call.
- Measured levers on the representative `codex_consult` tool (10,909 bytes, JSON-minified):
  - success-only (drop the error branch): **−3,055**
  - strip generated `title`/`description`/`default`: **−4,202**
  - **both combined: −6,354 (58%)** → 4,555 bytes
- Largest repeated `$defs` per tool: `Meta` 2,389, `ErrorInfo` 1,951, `RateLimit` 1,822,
  `RateLimitWindow` 1,062. `Meta`/`RateLimit` cannot be dropped — success branches carry
  `meta` (and `meta.rate_limit`). The removable repeated cost is `ErrorInfo` (+ its repair
  sub-objects), which only the error branch needs.
- The error branch exists deliberately: the comment at `schemas.py:702` records that a returned
  `ErrorResult` must validate against the declared `outputSchema` for strict MCP clients. Codex
  confirmed MCP structured output must conform to `outputSchema` and strict clients may validate
  independently — so a true success-only schema would be **incorrect**, not merely risky. The error
  branch stays; we make it *opaque* and small.
- Error envelopes are built inline at ~15 call sites as `ErrorResult(...).model_dump(mode="json")`
  (`server.py` and others), which serializes every optional field as `null` on every error.

## Part A — #137 catalog shrink

### A1. Compact opaque error branch

Replace `_object_union_schema()` with a builder that produces, per tool:

- the **success branch(es)** unchanged in structure (full success schema, so `summary`,
  `findings`, `verdict`, `diff`, `meta`, … stay fully described), `anyOf`'d with
- a **compact opaque error branch**:

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
    "meta": {"$ref": "#/$defs/Meta"}
  }
}
```

- `error` is **opaque** (`type: object`) — this is what drops the ~1,951-byte `ErrorInfo` `$def`
  (and its new `Repair`/`details` sub-objects) from all 12 published schemas. The runtime error
  *value* is still fully populated and self-describing; only its *schema* moves to the resource.
- `meta` is a `$ref` to the `Meta` `$def` that the success branch already pulls into the document,
  so the ref costs only the reference string, not a second copy **(Codex R1)**. It accurately
  reflects that error envelopes do carry a full `Meta`.
- The one-line `description` on `error` is an **intentional discovery pointer**, exempt from the
  stripping in A2, so a client that never fetches resources still learns where the error shape lives
  **(Codex R3)**.

The top-level `ok` discriminator stays visible (current behavior). The `anyOf` ordering and the
`ok` const in each branch let a client select the branch deterministically.

### A2. Strip generated schema noise

A recursive helper strips `title`, `description`, and `default` from each published `outputSchema`
(including nested `$defs`), with one exception: the intentional `error` pointer description in A1.
Output schemas become pure validation/shape contracts; field semantics live in `codex_capabilities`
(`use_when`, `returns`, per-field docstrings) and in the self-describing result envelope.

Codex flagged blanket description removal as a modest regression and `codex_capabilities` as an
imperfect substitute (a client may never call it) **(Codex R2)**. Decision: accept the trade-off for
the success-schema field descriptions (the result envelope field names are self-explanatory and the
58% win is large), but preserve discoverability of the one thing that genuinely disappears from the
wire — the **error** shape — via the A1 pointer description + the A3 resource + `COMPATIBILITY.md`.

### A3. Publish the full error schema once

The full reshaped `ErrorResult`/`ErrorInfo` JSON Schema (post-#135 shape) is published in exactly
one canonical place and referenced elsewhere:

- a new MCP **resource** `codex://error-envelope` returning the full schema;
- a **pointer** in `codex_capabilities` (a stable string field, e.g.
  `error_envelope_resource: "codex://error-envelope"`) — a pointer, not the embedded schema, so the
  fingerprint-cacheable capabilities payload stays small;
- prose in `COMPATIBILITY.md` documenting the envelope as the canonical error contract and noting
  the deliberate omissions (opaque wire branch; `details.value` never echoed).

### A4. CI catalog-size gate

A test builds the real 16-tool wire catalog (mcp-shaped, compact, `mode="json"`) and asserts its
total **serialized byte size** stays under a cap. Bytes are the primary, deterministic gate; token
count is reported advisorily using a pinned tokenizer, not asserted, since token counts drift with
tokenizer/toolchain changes **(Codex R5)**. The cap is set from the measured post-change size plus
~15% headroom (target: well under half the current ~180 KB). The exact number is fixed during
implementation after measuring the assembled catalog.

## Part B — #135 error-envelope reshape

Target shape (agent-friendly-mcp §6 unified repair contract), path (a) full consolidation:

```python
class Repair(BaseModel):
    next_step: str                 # was: repair (prose)
    tool: str | None = None        # was: repair_tool
    arguments: dict[str, Any] | None = None   # was: repair_tool_params
    alternative: str | None = None # new: a fallback recovery path when present

class ErrorDetail(BaseModel):      # §6 details{field, value, reason}, value omitted by policy
    field: str | None = None       # was: offending_param
    reason: str | None = None
    allowed_values: list[str] | None = None   # was: top-level allowed_values
    # NOTE: no `value` key. The rejected value is deliberately never echoed — a Literal/string
    # param can carry a secret and best-effort redaction cannot reliably catch a plain one. The
    # caller already holds what it sent. Documented divergence from §6's details{field,value,reason}.

class ErrorInfo(BaseModel):
    code: ErrorCode
    message: str
    temporary: bool = False         # was: retryable
    retry_after_ms: int | None = None    # key kept present even when null (§6); see invariant
    repair: Repair
    details: ErrorDetail | None = None
    invalid_arguments: list[InvalidArgument] | None = None   # KEPT (see B2)
    # Documented top-level extensions (out of scope of the "fold the four repair siblings" change):
    limit_bytes: int | None = None
    actual_bytes: int | None = None
    candidate_roots: list[str] | None = None
```

### B1. Field renames / folds

- `retryable` → `temporary`.
- The four flat siblings fold into `Repair`: `repair` (prose) → `repair.next_step`;
  `repair_tool` → `repair.tool`; `repair_tool_params` → `repair.arguments`; `offending_param`
  and `allowed_values` move into `details` (`details.field`, `details.allowed_values`).
- `repair` becomes a required object (every error carries at least a `next_step`).

### B2. `details` vs `invalid_arguments` (Codex R2)

Codex correctly warned that a singular `details` would lose information for multi-field validation
failures. Resolution: **keep** `invalid_arguments: list[InvalidArgument]` as the complete
per-field carrier for the `invalid_arguments` error code. `details` is the §6 singular object and,
for an `invalid_arguments` error, **deterministically mirrors the first entry** (first by Pydantic
error order — the same mirroring rule the current code uses for `offending_param`/`allowed_values`).
For non-argument errors that have a single offending field, `details` carries it directly. No
information is lost; clients that want every field read `invalid_arguments`, clients that want the
§6 single-detail read `details`.

### B3. Invariant in the model (Codex R4)

`temporary == False ⇒ retry_after_ms is None` is enforced by a Pydantic `model_validator` on
`ErrorInfo`, **not** by the serializer. Constructing an `ErrorInfo(temporary=False, retry_after_ms=5)`
raises. The serializer is a thin policy layer on top. Both layers are tested independently.

### B4. Central error serializer + null stripping

A single helper — e.g. `serialize_error(result: ErrorResult) -> dict` — replaces every inline
`ErrorResult(...).model_dump(mode="json")`. It:

- does `model_dump(mode="json", exclude_none=True)` to drop absent optionals (§8 "strip
  null/placeholder fields"), then
- **force-restores `error.retry_after_ms = null`** when absent, because §6 wants that key always
  present (it is the one intentional retained null).

All ~15 call sites in `server.py` (and any in `orchestration.py`/`_worker.py`/`delegate.py`) route
through this helper. Success envelopes are out of scope for this change (the issue scopes null
stripping to the error envelope); they keep their current serialization.

## Security: secret non-reflection (Codex R4 / next-steps)

A dedicated test asserts a rejected value cannot leak through **any** error field: `message`,
`repair.arguments`, `details`, `invalid_arguments[].reason`, or CLI-derived text. This guards the
deliberate `details.value` omission against regressions elsewhere.

## Testing (TDD — failing test first for each unit)

1. **Schema builder** — every tool's published `outputSchema`:
   - validates a representative success payload and a representative error payload (the opaque
     branch accepts the full runtime error object);
   - every internal `$ref` resolves (no dangling refs after strip/assembly) **(Codex R1/R5)**;
   - `ok` selects the intended branch (`true` → success shape, `false` → error branch);
   - contains no `title`/`default` and no `description` except the A1 error pointer.
2. **Catalog size** — assembled 16-tool wire catalog under the byte cap (A4).
3. **`ErrorInfo` model** — rename surface; `Repair`/`ErrorDetail` shape; the `temporary`/
   `retry_after_ms` invariant raises on violation (construction-level).
4. **Serializer** — absent optionals stripped; `retry_after_ms: null` retained; round-trips for a
   retryable error (with backoff) and a non-retryable error.
5. **`invalid_arguments`** — a multi-field validation error preserves every entry in
   `invalid_arguments` and `details` mirrors the first deterministically (B2).
6. **Secret non-reflection** — rejected value absent from all error fields.
7. **Resource + capabilities** — `codex://error-envelope` returns the full schema; capabilities
   exposes the pointer.
8. **Regression** — every existing error-path test updated to the new field names; existing
   strict-client output-schema validation still passes.

Coverage stays ≥ 95% (CI floor). `ruff check`, `ruff format --check`, and `ty check` must pass.

## Lockstep / surface bookkeeping

- `FINGERPRINT`: `codex-in-claude/0.1/schema-15` → `schema-16` (agent-visible surface changes:
  error field names, output-schema shapes, a new resource).
- `CHANGELOG.md`: entry under `## [Unreleased]` (Changed — breaking; Added — `codex://error-envelope`
  resource + CI catalog cap).
- **No** version-literal bumps (`pyproject.toml`, `.claude-plugin/plugin.json`, `.mcp.json` pin
  stay at the released version — those move only in the dedicated `chore: release` PR).
- `COMPATIBILITY.md`: document the canonical error envelope and the two deliberate divergences
  (opaque wire branch; `details.value` never echoed).
- PR: Conventional Commit title, `breaking-change` label, `Closes #135` + `Closes #137`.

## Out of scope (YAGNI)

- Stripping nulls from *success* envelopes (issue scopes it to errors).
- Cross-document `$ref` to share `Meta`/`RateLimit` across tools — infeasible (each `tools/list`
  entry is a self-contained document; cross-document refs aren't portable). Stated in #137.
- Reducing the 16-tool count — the count is justified by distinct tasks; the lever is per-definition
  size.

## Open implementation detail (decide while coding, not blocking)

- Exact byte cap number (A4) — set from the measured assembled catalog + ~15% headroom.
- Whether `serialize_error` lives in `schemas.py` or a small `errors.py` — pick by where the call
  sites read most naturally; `_core` must not import from its parent package.
