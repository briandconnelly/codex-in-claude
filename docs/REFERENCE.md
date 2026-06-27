# Reference

Detailed contract for callers integrating with the MCP tools **directly**. Most users can skip
this — Claude Code consumes these envelopes for you behind the `/codex:*` slash commands. See the
[README](../README.md) for installation and everyday use.

## Result envelopes

Every tool returns a discriminated envelope keyed by `ok`. The success shape depends on the tool:
all of `codex_consult`/`codex_review_changes`/`codex_delegate` carry `summary`/`findings`/`meta`,
but the review-only `verdict`/`confidence` appear solely on `codex_review_changes` and the proposed
`diff` only on `codex_delegate` — consult (Q&A) carries neither a verdict nor a diff. `codex_status`,
`codex_capabilities`, the `codex_job_*` lifecycle tools, `codex_dry_run`, and `codex_delegate_dry_run`
return their own documented shapes (branch on the tool, or on `ok`/`tool`/`status`, before reading
fields). Failure is uniform: an `error` object built for machine-driven recovery, not just prose:

- `code` — a stable error code from a fixed set (e.g. `invalid_arguments`, `job_running`,
  `job_not_found`).
- `message` — human-readable detail.
- `temporary` + `retry_after_ms` — whether retrying can succeed and how long to back off
  (`retry_after_ms` is always present; `null` unless `temporary` is true).
- `repair` — `{next_step, tool, arguments, alternative}`: `next_step` is a stable SYMBOLIC
  label you branch on (e.g. `poll_job_status`, `correct_arguments`); `tool`/`arguments` name a
  tool to call to recover; `alternative` is prose fallback. Omitted only when no corrective
  path exists.
- `details` — `{field, reason, allowed_values}` for a single offending field. The rejected
  `value` is deliberately never echoed (it may be a secret).
- `invalid_arguments` — set when `code` is `invalid_arguments`: a list of
  `{field, reason, allowed_values}` per offending argument; `details` mirrors the first.
- `limit_bytes`/`actual_bytes`/`candidate_roots` — size/roots context for the relevant codes.

Absent optional fields are omitted from the payload (no placeholder nulls), except
`retry_after_ms`. The full schema is published at the `codex://error-envelope` resource.

`codex_capabilities` lists the error codes each tool may return (`error_codes`) as an advisory guide
— useful for planning recovery, but not a closed contract. The envelope shape is versioned by
`fingerprint`; clients can cache by it.

Secret-looking values are redacted from every free-text surface before it leaves the plugin —
`summary`, `findings`/`questions`/`assumptions`/`next_steps`, and `raw_response.text` — in addition
to gathered diffs. Inline matches become `[redacted: secret value]`. This is **best-effort
defense-in-depth, not a guarantee**: it covers content the plugin itself surfaces, not whatever Codex
may read or act on during a run. The schema is unchanged; the inline marker is the only signal.

### Detail levels

`codex_consult`, `codex_review_changes`, `codex_delegate`, and async result retrieval
(`codex_job_result`, `codex_job_consume_result`) take a `detail` parameter:

- `detail="summary"` (**default**) — omits the raw model text (`raw_response.text`), which usually
  duplicates content already in `summary`/`findings`/`diff`. The structured fields remain
  authoritative, and the parser shape is unchanged: `raw_response` is still present with `text` set to
  `null` (its `session_id`/`model` — also in `meta` — are kept).
- `detail="full"` — includes the complete raw model output for diagnostics.

An unrecognized value is rejected with `unsupported_detail`. For async work the worker always stores
the full envelope, so a later `codex_job_result(..., detail="full")` can still recover the raw text.

## Workspace selection

When calling the MCP tools directly, pass `workspace_root` as an absolute path to the repository you
want Codex to inspect or edit. Claude Code usually supplies the current repo as an MCP root for slash
commands; if neither an MCP root nor `workspace_root` is available, the server may fall back to its
own launch directory and return `meta.workspace_warning`.

The job-lifecycle tools (`codex_job_status`, `codex_job_list`, `codex_job_cancel`) carry the resolved
workspace on **successful** responses too — a compact `workspace` object with `cwd`,
`workspace_source` (`param`/`roots`/`cwd`), and `workspace_warning` (set on a cwd fallback). Because
jobs are scoped per workspace, this lets you confirm which repository a poll or list targeted instead
of mistaking a wrong-workspace lookup for an empty list or `job_not_found`. (Error responses already
carry the same context via `meta`.)

Review and delegate operations need a git repository. `codex_delegate` also requires at least one
commit so it can create the temporary worktree.
