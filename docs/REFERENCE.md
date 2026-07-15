# Reference

Detailed contract for callers integrating with the MCP tools **directly**. Most users can skip
this — Claude Code consumes these envelopes for you behind the `/codex:*` slash commands. See the
[README](../README.md) for installation and everyday use.

## Result envelopes

Every tool returns a discriminated envelope keyed by `ok`. The success shape depends on the tool:
all of `codex_consult`/`codex_review_changes`/`codex_delegate` carry `summary`/`findings`/`meta`,
but the review-only `verdict`/`confidence`/`review_status`/`coverage` appear solely on
`codex_review_changes` and the proposed `diff` only on `codex_delegate` — consult (Q&A) carries
neither a verdict nor a diff. `review_status` distinguishes a completed review from one that never
ran the model (nothing reviewable → `not_run`/`unknown`, never a false `pass`), and `coverage`
discloses omitted untracked files, truncation, or redaction — downgrading a `pass` over partial
coverage to `unknown`. `codex_status`,
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
- `details` — `{field, fields, reason, allowed_values}`: `field` names a single offending input;
  `fields` (mutually exclusive with `field`; non-empty, unique) names inputs whose *combination* is
  invalid (e.g. a combined-size limit where no single input is at fault). The rejected `value` is
  deliberately never echoed (it may be a secret).
- `invalid_arguments` — set when `code` is `invalid_arguments`: a list of
  `{field, reason, allowed_values}` per offending argument; `details` mirrors the first.
- `limit_bytes`/`actual_bytes`/`candidate_roots` — size/roots context for the relevant codes.

Absent optional fields are omitted from the payload (no placeholder nulls), except
`retry_after_ms`. The full schema is published at the `codex://error-envelope` resource.

`codex_capabilities` lists the error codes each tool may return (`error_codes`) as an advisory guide
— useful for planning recovery, but not a closed contract. The envelope shape is versioned by
`fingerprint`; clients can cache by it.

Every result envelope also carries `server_version` beside `fingerprint`. The two answer different
questions and are not interchangeable:

- `fingerprint` — **contract identity**: which agent-visible surface (tool/field shapes, error
  codes, documented meaning) this result conforms to. A client cache key — bump it and a cached
  client re-fetches the contract.
- `server_version` — **release identity**: the installed `codex-in-claude` package version attributed
  to this result. Provenance, not a cache key — it lets a downstream consumer (an MCP error audit,
  say) scope an analysis to a release instead of guessing from timestamps. It is a *package* version,
  not a per-commit build id: an unreleased build reports the release it was cut from, so two runs
  sharing a `server_version` may still differ in code (and, if the surface moved, in `fingerprint`).

`server_version` is nullable. A **result** reply — a `done` job replaying its stored payload —
reports the `server_version` of the run that **produced** it, never the version of the server
replaying it: replaying never overwrites provenance with the replaying process's own identity. A
job result persisted before this field existed replays with `server_version` **absent** (omitted,
not backfilled), rather than being stamped with a plausible-but-wrong version.

A job in a non-`done` terminal state (`failed`, `timeout`, `cancelled`) or not found has no stored
payload to replay: the server synthesizes an error envelope for it, and that envelope's
`server_version` is the **polling** server's own version, not any producing worker's — the job
record persists no worker version, so there is nothing to preserve. A consumer reading synthesized
error envelopes (an error audit, say) must attribute `server_version` on those envelopes to whichever
server answered the poll, not to the run that failed.

**Read version fields by exact key, never by pattern.** Two tools carry version-ish keys, and they
do not name the same software. `codex_capabilities` returns `version` and `server_version` — both
*this* server's installed package version. `codex_status` returns `server_version` for this server
and `codex_version` for the **Codex CLI** — the external binary this server shells out to (its
companion `version_supported` is a boolean about that CLI, not about this server). Match the exact
key you mean; pattern-matching key names containing "version" risks reading the wrong software's
version.

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

## Idempotency

The six spend-committing tools — `codex_consult`, `codex_review_changes`, `codex_delegate` and their
`_async` variants — take an optional `idempotency_key`. Reusing a key on the **same tool** with the
same arguments replays the existing run instead of starting (and paying for) a duplicate Codex call:
a sync call reattaches to the in-flight run and returns its result; an `_async` call returns the same
`job_id`. The key is scoped to the concrete tool — the sync and `_async` variants are different tools
and never share a key's run. Reuse with different arguments (including a different `timeout_seconds`)
is refused with `idempotency_conflict`; a key whose prior result was already consumed/evicted is
`idempotency_result_unavailable`; a still-publishing reservation is `idempotency_in_progress`
(retryable). Omit the key for the prior no-dedup behavior.

- `meta.idempotency_replayed` — `true` only on a replayed response, marking that no new Codex spend
  occurred; omitted otherwise.
- `meta.job_kind` — set on a lifecycle (`codex_job_*`) error envelope when it resolved an existing
  job record, naming that job's kind (e.g. `codex_delegate`); omitted for not-found/pre-lookup errors.

## Background jobs

The `codex_job_*` lifecycle tools manage detached runs started by the `_async` tools (and the job
records that sync runs also create). Operational semantics:

- **Backoff.** Every polling response carries `poll_after_ms`; honor it rather than polling in a
  tight loop. It grows with a running job's elapsed runtime (bounded), so you back off
  automatically on long runs.
- **Deadline.** A job is bounded by a wall-clock cap (`CODEX_IN_CLAUDE_JOB_MAX_SECONDS`); a poll
  past the deadline reaps the job.
- **Retention.** Results are retained `ttl_seconds` **after** a job completes, so `expires_at` is
  `null` while it runs and is set once it finishes. Records are also evicted oldest-terminal-first
  past a per-workspace count cap (`CODEX_IN_CLAUDE_JOB_MAX_COUNT`).
- **`server_version` provenance.** Only a *replayed* payload carries the producing run's version;
  every freshly built envelope carries the responding server's. For a `done` job, a
  `codex_job_result`/`codex_job_consume_result` reply replays the stored payload and carries the
  `server_version` of the run that *produced* it, not the version of the server currently serving the
  poll — replaying never re-stamps provenance. A result from a job persisted before this field existed
  replays with `server_version` absent. Everything else is freshly constructed and therefore reports
  the *responding* server: `codex_job_status`, `codex_job_list`, and a successful `codex_job_cancel`,
  plus the synthesized error envelope for a job in another terminal state (`failed`, `timeout`,
  `cancelled`) or not found — those have no stored payload to preserve. See Result envelopes above.

## Rate-limit reporting

`codex_status` reads the current quota **live** from the Codex app-server
(`account/rateLimits/read`, `source: app_server_live`) — a read-only call with no model-token
spend and nothing persisted. Windows are classified by duration: `primary` is the shorter/rolling
window, `secondary` the longer one; the account reports only the windows that currently bind it, so
either may be null. `status` is `available`/`limited`/`exhausted`/`unknown` (the live read could not
complete, or codex is not ready — retry) / `unavailable` (this codex/account exposes no quota data).
On codex 0.144+ the quota block no longer rides the `codex exec` stream, so a run's `meta.rate_limit`
is `null`. The block is advisory.

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
