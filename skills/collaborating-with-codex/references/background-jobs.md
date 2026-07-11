# Background jobs

Use the `_async` form only when the corresponding consult, review, or delegate may outlast a useful
synchronous wait. Starting it commits spend immediately; abandoning polling does not stop the run.

## Lifecycle

1. Start exactly one matching `_async` tool and retain its `job_id`, `kind`, `poll_after_ms`, and
   workspace.
2. Wait at least `poll_after_ms`, then call `codex_job_status` with the same absolute
   `workspace_root`.
3. While running, honor each new `poll_after_ms`. Activity fields are advisory; silence does not
   prove a stall.
4. When `result_available` is true, fetch with `codex_job_result`.
5. Branch on `ok`, then on the fetched result's originating consult, review, or delegate type.

`codex_job_consume_result` fetches and deletes the record. Use it only when destructive consumption
is intended. `codex_job_cancel` requests cancellation; `codex_job_list` can recover a lost job id.

Jobs are workspace-keyed and disk-backed. Their deadlines bound runtime. Retention begins after
completion, so read `ttl_seconds` and `expires_at` and fetch completed work promptly. A fetched
result is not a generic lifecycle success object: it has the same success schema as the originating
active tool, with originating run posture in `meta`.

Treat `codex_capabilities` as authoritative for the current lifecycle tools, status fields, and
tool-specific errors.
