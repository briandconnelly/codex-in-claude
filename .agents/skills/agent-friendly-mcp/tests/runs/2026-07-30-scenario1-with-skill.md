# Scenario 1 (Design) — with-skill run (post-#122 per-tool `outputSchema`)

- **Date:** 2026-07-30
- **Tree:** `36f6096` (branch `fix/milestone-2-evidence-base`, the #122/#124/#125 commit)
- **Mode:** with-skill (fresh general-purpose subagent on `claude-fable-5`; read `SKILL.md` + all eight `references/` files from an isolated scratchpad snapshot, explicitly forbidden from the repository, from writing files, and from git; 11 tool-uses, all reads)
- **Score:** 11/11
- **Purpose:** confirm the tightened per-tool A9 (#122) and the new `not-run` disposition assertion (#124) take effect in a treatment run

## Exact prompt given

The standard Scenario 1 GitHub Issues design prompt, preceded by an instruction to read `SKILL.md` and every file under `references/` in the snapshot directory as authoritative guidance, and to read nothing else.

## Per-assertion scoring

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| A1 | Task-completing granularity, 11 endpoints → ~4–7 tools | **PASS** | 11 endpoints → 6 tools with a per-merge justification table; list+search folded, get+list-comments folded, labels and lock/unlock folded into `github_update_issue`. The one addition (`github_list_labels`) is declared a named split exception because repair hints must be able to name a callable enumeration surface. |
| A2 | `snake_case`, service-prefixed, verb+noun | **PASS** | `github_search_issues`, `github_get_issue`, `github_create_issue`, `github_update_issue`, `github_add_comment`, `github_list_labels`. |
| A3 | Closed schemas, disambiguated names, omission semantics, server-applied defaults only | **PASS** | `additionalProperties: false` and a declared `$schema` throughout; `assignee_logins`, `updated_after`, `issue_number` ("not its global id"); every optional states omission semantics, and `github_update_issue` additionally declares clearing semantics per field (`[]` removes all assignees, `null` removes the milestone, `""` clears the body). |
| A4 | ≥2 error payloads, symbolic codes, field detail, repair naming a callable surface | **PASS** | Four worked payloads (`label_not_found`, `rate_limited`, `conflict`, plus a JSON-RPC `issue_not_found`); 13-code taxonomy; repairs name `github_list_labels` / `github_get_issue` / `github_search_issues` with literal arguments. |
| A5 | Tool errors as `isError: true` results, not JSON-RPC | **PASS** | "One envelope, two carriers": tool failures on the tool result, `resources/read` failures on JSON-RPC `error.data` with the `[6.rename]` spellings. |
| A6 | Capability summary with negative scope | **PASS** | `does_not` block (no PRs, no label/milestone definition, no deletes or transfers, no real-time updates) served as a resource, mirrored in advisory `instructions`, with essentials also in tool descriptions. |
| A7 | Pagination provenance-correct with detail toggle | **PASS** | House `has_more`/`next_cursor` inside tool payloads labeled a convention; native `nextCursor`/`ttlMs`/`cacheScope` reserved for list/read methods; `detail` declared density-only and a strict subset ("never changes row count"). |
| A8 | Honest annotations | **PASS** | Reads carry `readOnlyHint: true` with the mutation hints omitted per `[3.annotation-defaults]`; `github_create_issue` `idempotentHint: false` with an `idempotency_key` rationale; `github_update_issue` `destructiveHint: true, idempotentHint: true` with the sibling difference explicitly justified (replacement vs. additive semantics). |
| A9 | **(Tightened, #122.)** Every tool definition publishes an `outputSchema` | **PASS** | Machine-counted on the archived output: 6 tool definitions, 6 `outputSchema` blocks. The run also states the rule as a shared convention up front: "Every tool publishes `outputSchema` and returns conforming `structuredContent` on success". |
| A10 | Error path distinct; `outputSchema` scoped to success | **PASS** (same caveat as 2026-07-29) | "`isError: true` results carry the §6 error envelope instead and are not validated against `outputSchema` (documented per tool via a shared pointer, `[3.output-schema-scope]`)". Attributes the scoping by rule id rather than verbalizing the unsettled-spec-point hedge. |
| A11 | **(New, #124.)** Step 9 reported as measured or recorded `not-run` with the missing prerequisite named | **PASS** | "Step 9 is recorded **not-run**: no runnable host or agent harness exists at design time; running the suite against a reference implementation is the prerequisite before the contract is declared regression-gated." Step 8 is still authored — the fixture suite is specified — which is the intended split. |

**Passed:** all (11/11).

## What this run does and does not establish

It establishes that the tightened A9 is satisfiable by a treatment agent and that the `not-run` disposition is reachable and used correctly on the first prompt that needs it.
Both were the open questions #122 and #124 left.

It does **not** reproduce the exact 2026-07-29 failure.
That run failed the tightened assertion on `github_get_capabilities` — a discovery-fallback *tool* with no `outputSchema`.
This design serves its capability summary as a resource plus advisory `instructions` and ships no discovery tool at all, so the specific shape that regressed was never re-entered.
A9 passing here means every tool this run produced carries a schema; it does not prove a run that adds a discovery tool would now give it one.
A scenario that forces a discovery tool into the catalog would close that gap.

No baseline was run at this tree.
The 2026-07-29 baseline scored 7/10 against the ten-assertion set and has not been rescored against the eleven-assertion set, so no baseline contrast is claimed here.

## Full final output

Archived below verbatim, extracted programmatically from the subagent transcript rather than transcribed.

---

All skill files read (SKILL.md plus the eight files under `references/`). Below is the full design, following `design-workflow.md` Steps 0–7, with the checklist walk required by the skill's Done Criteria.

---

# `github-issues-mcp` — Agent-Facing Contract Design

## Step 0 — Protocol revision target

Target: **MCP 2026-07-28 only**, no extensions (`io.modelcontextprotocol/tasks` not advertised — see §7 N/A below). Transport: **stdio** (local single-client), credential from `GITHUB_TOKEN`; an HTTP variant is documented in the capability summary but not designed here. All requests require the per-request `_meta` (`io.modelcontextprotocol/protocolVersion`, `io.modelcontextprotocol/clientCapabilities`); server capabilities advertised via `server/discover`.

## Steps 1–3 — Tasks and granularity (11 endpoints → 6 tools)

Endpoint-mirroring would yield 11 tools; the real agent tasks are 6. Granularity decisions per `[3.task-completing]` / `[3.hide-steps]`:

| Underlying endpoints | Tool | Rationale |
|---|---|---|
| list issues + search issues | `github_search_issues` | One task: "find issues matching criteria." Whether the server uses the list or search endpoint internally is a hidden step. |
| get issue + list comments | `github_get_issue` | Reading an issue almost always means reading its thread; comments ride along, paginated. |
| create issue (labels/assignees at create) | `github_create_issue` | Create-with-metadata is one task, not create-then-label. |
| update issue + add labels + remove labels + lock + unlock | `github_update_issue` | "Close it, relabel it, lock the thread" is one task. All modes mutate, so one honest annotation set is possible (no `[3.ap-mode-annotations]` violation — no read mode is mixed in). |
| add comment | `github_add_comment` | Distinct external-send task; needs its own idempotency key. |
| (derived from issue listings) | `github_list_labels` | Split exception: value-space enumeration that repair hints must be able to name as a callable tool (`[6.repair-callable]`). Honest caveat: the given API surface has no label-catalog endpoint, so this is derived from labels observed on issues — declared in its description. |

No prompts (§5: `[5.optional]` — none is load-bearing). No composable-primitive splits beyond `github_list_labels`.

## Step 4 — Tool list with input schemas

Conventions applying to every tool (stated once, not repeated per description, per `[8.ap-fixed-prose]`):

- Dialect `https://json-schema.org/draft/2020-12/schema`; all object schemas closed (`additionalProperties: false`); no `$defs` on the wire — shapes inlined per entry (`[3.portable-schema]`, `[2.compact-baseline]`).
- Every tool publishes `outputSchema` and returns conforming `structuredContent` on success; `isError: true` results carry the §6 error envelope instead and are not validated against `outputSchema` (documented per tool via a shared pointer, `[3.output-schema-scope]`).
- `content[0].text` carries the same JSON serialized, as fallback for weak clients.
- Timestamps RFC3339 UTC. Cursors are opaque, integrity-protected stateless tokens (HMAC), valid **15 minutes**, expiry → `cursor_expired` error with restart repair (`[8.cursor-lifetime]`, `[3.sec-handle-modes]`).
- Optimistic concurrency: GitHub issues expose no version counter, so `version` is a content-derived token — `sha256` over a canonical serialization (sorted keys, sorted label list) of `{title, body, state, state_reason, labels, assignees, milestone, locked}` — returned by every read and mutation result, compared by `github_update_issue.expected_version` (`[3.version-precondition]`).

**Shared `issue` object** (shown once; inlined in each `outputSchema` on the wire). `detail: "summary"` fields are a strict subset of `"full"` (`[8.detail-orthogonal]`):

- summary: `issue_number` (integer), `title`, `state` (`"open"|"closed"`), `author` (login), `labels` (string array), `comment_count` (integer), `updated_at` (date-time), `url` (html URI), `preview` (first ~200 chars of body)
- full adds: `body`, `state_reason` (`"completed"|"not_planned"|"reopened"|null`), `assignees`, `milestone` (`{number, title}|null`), `locked` (boolean), `lock_reason` (`"off-topic"|"too heated"|"resolved"|"spam"|null`), `created_at`, `closed_at` (nullable), `version`

### 1. `github_search_issues`

```json
{
  "name": "github_search_issues",
  "title": "Search issues",
  "description": "Find GitHub issues in one repository or across every repository the configured token can read.\n\nWhen to use: any 'find/list/browse issues' task — by repo, label, state, assignee, date, or free-text query. Provide `owner`+`repo`, or `query`, or both; a call with neither fails with `invalid_field` (fields: [\"query\",\"owner\",\"repo\"]). Results are issues only, never pull requests.\n\nDefaults: `state` open, `sort` updated desc, `detail` summary, 30 items per page, hard cap 300 items per walk (then `truncated: true` with a narrowing hint).\n\nExample: {\"owner\": \"acme\", \"repo\": \"api\", \"labels\": [\"bug\"], \"state\": \"open\"}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "query": {"type": "string", "minLength": 1, "maxLength": 256, "description": "GitHub issue-search syntax, e.g. \"memory leak label:bug\". Omitted means filter-only browsing, which then requires owner and repo."},
      "owner": {"type": "string", "pattern": "^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$", "description": "Repository owner login, e.g. \"acme\". Omitted means search across all accessible repos (requires query)."},
      "repo": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,100}$", "description": "Repository name without owner, e.g. \"api\". Omitted means search across all accessible repos (requires query)."},
      "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open", "description": "Omitted means \"open\" (the server applies this default)."},
      "labels": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 100}, "maxItems": 10, "description": "Issue must carry ALL listed labels. Omitted means no label filter. Valid names: github_list_labels."},
      "assignee_login": {"type": "string", "description": "GitHub login of an assignee. Omitted means no assignee filter."},
      "creator_login": {"type": "string", "description": "GitHub login of the issue author. Omitted means no author filter."},
      "updated_after": {"type": "string", "format": "date-time", "description": "Only issues updated at or after this instant (RFC3339 UTC). Omitted means no lower bound."},
      "sort": {"type": "string", "enum": ["created", "updated", "comments"], "default": "updated", "description": "Omitted means \"updated\" (server-applied)."},
      "order": {"type": "string", "enum": ["asc", "desc"], "default": "desc", "description": "Omitted means \"desc\" (server-applied)."},
      "detail": {"type": "string", "enum": ["summary", "full"], "default": "summary", "description": "Field density only; never changes row count. Omitted means \"summary\" (server-applied)."},
      "cursor": {"type": "string", "description": "Opaque continuation token from a prior next_cursor. Valid 15 minutes; expiry returns cursor_expired. Omitted means first page."}
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["issues", "has_more", "truncated"],
    "properties": {
      "issues": {"type": "array", "items": {"...": "shared issue object, density per detail"}},
      "has_more": {"type": "boolean"},
      "next_cursor": {"type": "string", "description": "Present iff has_more is true."},
      "estimated_total": {"type": "integer", "minimum": 0, "description": "Present when the upstream reports a match count."},
      "truncated": {"type": "boolean", "description": "True when the 300-item walk cap applies; paging stops there even if has_more pages exist below the cap."},
      "truncation_hint": {"type": "string", "description": "Present iff truncated; names a callable narrowing parameter."},
      "rate_limit_remaining": {"type": "integer", "minimum": 0, "description": "Present only when the remaining upstream budget is below 10% — an early-warning observability field."}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": true, "openWorldHint": true}
}
```

(`destructiveHint`/`idempotentHint` deliberately omitted on read-only tools per `[3.annotation-defaults]` — same on `github_get_issue` and `github_list_labels`.)

### 2. `github_get_issue`

```json
{
  "name": "github_get_issue",
  "title": "Get issue with comments",
  "description": "Read one issue and its comment thread.\n\nWhen to use: the agent knows owner/repo/issue_number (from a search result, a URL, or the user) and needs the content. Comments return oldest-first, 30 per page, continued via comments_cursor. The returned `version` token is required input for safe github_update_issue calls.\n\nExample: {\"owner\": \"acme\", \"repo\": \"api\", \"issue_number\": 512}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo", "issue_number"],
    "properties": {
      "owner": {"type": "string", "pattern": "^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$"},
      "repo": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,100}$"},
      "issue_number": {"type": "integer", "minimum": 1, "description": "The issue's number in the repo (from URL or search), not its global id."},
      "detail": {"type": "string", "enum": ["summary", "full"], "default": "full", "description": "Omitted means \"full\" (server-applied): a targeted single-issue read usually wants the body."},
      "comments_cursor": {"type": "string", "description": "Opaque token from a prior comments_next_cursor. Omitted means the first comment page."}
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["issue", "version", "comments", "comments_has_more"],
    "properties": {
      "issue": {"...": "shared issue object"},
      "version": {"type": "string", "description": "Content-derived optimistic-lock token; pass as expected_version to github_update_issue."},
      "comments": {"type": "array", "items": {"type": "object", "required": ["comment_id", "author", "created_at", "body"], "properties": {"comment_id": {"type": "integer"}, "author": {"type": "string"}, "created_at": {"type": "string", "format": "date-time"}, "body": {"type": "string"}, "url": {"type": "string", "format": "uri"}}, "additionalProperties": false}},
      "comments_has_more": {"type": "boolean"},
      "comments_next_cursor": {"type": "string", "description": "Present iff comments_has_more is true."}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": true, "openWorldHint": true}
}
```

### 3. `github_create_issue`

```json
{
  "name": "github_create_issue",
  "title": "Create issue",
  "description": "Open a new issue, optionally with labels, assignees, and milestone in the same call — never create-then-update for initial metadata.\n\nSet labels only from names known to exist (github_list_labels); an unknown label fails the whole call with label_not_found (creation is atomic — no partial issue is left behind).\n\nIf a call times out, do not blind-retry: re-send with the same idempotency_key, or check for the issue via github_search_issues (query the exact title) before retrying without one.\n\nExample: {\"owner\": \"acme\", \"repo\": \"api\", \"title\": \"500 on /v2/users when page > 100\", \"body\": \"Steps to reproduce…\", \"labels\": [\"bug\"]}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo", "title"],
    "properties": {
      "owner": {"type": "string", "pattern": "^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$"},
      "repo": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,100}$"},
      "title": {"type": "string", "minLength": 1, "maxLength": 256},
      "body": {"type": "string", "maxLength": 65536, "description": "GitHub-flavored Markdown. Omitted means an issue with no body."},
      "labels": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 100}, "maxItems": 20, "description": "Omitted means no labels applied."},
      "assignee_logins": {"type": "array", "items": {"type": "string"}, "maxItems": 10, "description": "GitHub logins. Omitted means unassigned."},
      "milestone_number": {"type": "integer", "minimum": 1, "description": "Existing milestone number. Omitted means no milestone."},
      "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 128, "description": "Re-sends with the same key within 10 minutes (scoped per owner/repo) deduplicate to one issue. Omitted means no deduplication — each call creates."}
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["issue_number", "url", "state", "version", "created_at"],
    "properties": {
      "issue_number": {"type": "integer"},
      "url": {"type": "string", "format": "uri"},
      "state": {"type": "string", "enum": ["open"]},
      "version": {"type": "string"},
      "created_at": {"type": "string", "format": "date-time"}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": true}
}
```

(`idempotentHint: false` is honest — a retry without the key creates a duplicate — which is exactly why `idempotency_key` exists, per `[3.idempotency-key]` and `[3.dispatched-vs-applied]`. GitHub's REST API confirms mutations synchronously, so no `pending_verification` state is needed; the timeout-ambiguity path is the description's reconcile guidance.)

### 4. `github_update_issue`

```json
{
  "name": "github_update_issue",
  "title": "Update issue",
  "description": "Edit, relabel, assign, close/reopen, and lock/unlock one issue in a single call.\n\nOmitted fields are left unchanged — this is a partial update. At least one mutating field is required; a call with none fails with invalid_field. Pass expected_version (from github_get_issue or a prior mutation result) whenever editing title/body or changing labels; on mismatch the call fails with `conflict` and you must re-read then re-apply. Calls that overwrite `body` without expected_version are accepted but can silently clobber concurrent edits — prefer sending it.\n\nClearing semantics per field: assignee_logins [] removes all assignees; milestone_number null removes the milestone; body \"\" sets an empty body. add_labels/remove_labels are incremental and may be combined; an unknown name in either fails the whole call (label_not_found) with no partial application.\n\nlock_reason is honored only when locked is true; unlocking ignores it.\n\nExample: {\"owner\": \"acme\", \"repo\": \"api\", \"issue_number\": 512, \"state\": \"closed\", \"state_reason\": \"completed\", \"add_labels\": [\"resolved\"], \"expected_version\": \"v1:sha256:9c41…\"}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo", "issue_number"],
    "properties": {
      "owner": {"type": "string", "pattern": "^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$"},
      "repo": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,100}$"},
      "issue_number": {"type": "integer", "minimum": 1},
      "title": {"type": "string", "minLength": 1, "maxLength": 256, "description": "Omitted means unchanged."},
      "body": {"type": "string", "maxLength": 65536, "description": "Full replacement text. Omitted means unchanged; \"\" clears the body."},
      "state": {"type": "string", "enum": ["open", "closed"], "description": "Omitted means unchanged."},
      "state_reason": {"type": "string", "enum": ["completed", "not_planned", "reopened"], "description": "Only meaningful alongside state. Omitted with state \"closed\" means \"completed\" (server-applied)."},
      "add_labels": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 100}, "maxItems": 20, "description": "Labels to add; existing labels are kept. Omitted means none added."},
      "remove_labels": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 100}, "maxItems": 20, "description": "Labels to remove; absent names in this list are ignored. Omitted means none removed."},
      "assignee_logins": {"type": "array", "items": {"type": "string"}, "maxItems": 10, "description": "FULL replacement of the assignee set. Omitted means unchanged; [] removes all assignees."},
      "milestone_number": {"type": ["integer", "null"], "minimum": 1, "description": "Omitted means unchanged; null removes the milestone."},
      "locked": {"type": "boolean", "description": "true locks the conversation, false unlocks it. Omitted means unchanged."},
      "lock_reason": {"type": "string", "enum": ["off-topic", "too heated", "resolved", "spam"], "description": "Only honored when locked is true. Omitted means a lock with no recorded reason."},
      "expected_version": {"type": "string", "description": "Optimistic-lock token from the last read or mutation. Omitted means no precondition check. Mismatch fails with `conflict`."}
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["issue_number", "state", "version", "changed_fields", "url"],
    "properties": {
      "issue_number": {"type": "integer"},
      "state": {"type": "string", "enum": ["open", "closed"]},
      "version": {"type": "string", "description": "The NEW version token after this update."},
      "changed_fields": {"type": "array", "items": {"type": "string"}, "description": "Which published parameters actually changed stored state (a no-op relabel yields an empty array)."},
      "url": {"type": "string", "format": "uri"}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": false, "destructiveHint": true, "idempotentHint": true, "openWorldHint": true}
}
```

(Annotation reasoning, stated in the tool description on the wire per `[3.annotation-defaults]`'s sibling rule: `destructiveHint: true` is honest because `body`/`title` replacement can overwrite content this API surface cannot restore; `idempotentHint: true` because re-applying the same arguments converges to the same state. This differs from its siblings' `destructiveHint: false, idempotentHint: false`, and the reason — replacement vs. additive semantics — is declared.)

### 5. `github_add_comment`

```json
{
  "name": "github_add_comment",
  "title": "Add comment",
  "description": "Post a comment on an existing issue.\n\nFails with issue_locked when the conversation is locked and the token lacks maintainer rights; the error's repair names the unlock call. If a call times out, re-send with the same idempotency_key, or check the thread tail via github_get_issue before retrying without one — never blind-retry.\n\nExample: {\"owner\": \"acme\", \"repo\": \"api\", \"issue_number\": 512, \"body\": \"Fixed in 41c9e2f; closing.\"}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo", "issue_number", "body"],
    "properties": {
      "owner": {"type": "string", "pattern": "^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$"},
      "repo": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,100}$"},
      "issue_number": {"type": "integer", "minimum": 1},
      "body": {"type": "string", "minLength": 1, "maxLength": 65536, "description": "GitHub-flavored Markdown."},
      "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 128, "description": "Re-sends with the same key within 10 minutes (scoped per owner/repo/issue_number) deduplicate to one comment. Omitted means no deduplication."}
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["comment_id", "url", "created_at"],
    "properties": {
      "comment_id": {"type": "integer"},
      "url": {"type": "string", "format": "uri"},
      "created_at": {"type": "string", "format": "date-time"}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": true}
}
```

### 6. `github_list_labels`

```json
{
  "name": "github_list_labels",
  "title": "List labels in use",
  "description": "Enumerate label names observed on issues in one repository — the valid value space for `labels`, `add_labels`, and `remove_labels`.\n\nCaveat (contract, not prose garnish): the underlying API surface exposes no label catalog, so this list is derived from labels currently applied to at least one issue. A defined-but-unused label will not appear; applying such a label still succeeds. Treat the list as \"known-good values\", not \"all values\".\n\nExample: {\"owner\": \"acme\", \"repo\": \"api\"}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo"],
    "properties": {
      "owner": {"type": "string", "pattern": "^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$"},
      "repo": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,100}$"},
      "cursor": {"type": "string", "description": "Opaque continuation token. Omitted means first page."}
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["labels", "has_more", "derived_from_issues"],
    "properties": {
      "labels": {"type": "array", "items": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}, "color": {"type": "string"}, "description": {"type": "string"}}, "additionalProperties": false}},
      "has_more": {"type": "boolean"},
      "next_cursor": {"type": "string", "description": "Present iff has_more is true."},
      "derived_from_issues": {"type": "boolean", "description": "Always true on this server; flags the in-use-only derivation."}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": true, "openWorldHint": true}
}
```

Each tool additionally carries its repair-critical error codes inline under `_meta` `com.github-issues-mcp/errors` (convention extension, small — 3–5 codes each), with the complete catalog served on demand at `ghissues://server/errors` per `[6.document-codes]`.

## Step 6 — Error contract

One envelope, two carriers (`contract-checklist.md` §6): tool semantic failures ride the tool result (`isError: true`, envelope in `structuredContent`); `resources/read` failures ride JSON-RPC `error.data` with the mandatory `code`→`machine_code`, `message`→`human_message` rename. `retry_after_ms` is always present alongside `temporary` (nullable); optional fields are omitted entirely when absent — never `null` placeholders. No `recoverable` flag.

**Symbolic code taxonomy** (stable branch keys): `missing_credential`, `invalid_credential`, `insufficient_scope`, `invalid_field`, `repo_not_found`, `issue_not_found`, `comment_not_found`, `label_not_found`, `issue_locked`, `conflict`, `cursor_expired`, `rate_limited`, `upstream_unavailable`.

**Value-echo policy** (disclosed in the capability summary per `[6.offending-value]`): `details.value` is emitted for server-minted values (cursors, version tokens, URIs the server issued) and published enum members; for free-form parameters (`title`, `body`, `query`, label and login strings) `value` is omitted and `details.reason` locates the offense structurally — e.g. by zero-based array index — because sensitivity of a received free-form value cannot be reliably determined.

### Failure mode 1 — unknown label on update (tool-result error)

```json
{
  "resultType": "complete",
  "isError": true,
  "content": [
    {"type": "text", "text": "add_labels[1] names a label not observed in acme/api. List known-good names with github_list_labels, then re-call github_update_issue."}
  ],
  "structuredContent": {
    "code": "label_not_found",
    "message": "A label in add_labels does not exist in this repository; nothing was applied.",
    "details": {
      "field": "add_labels",
      "reason": "Element at index 1 matches no label observed in acme/api (value omitted per the disclosed free-form echo policy)."
    },
    "temporary": false,
    "retry_after_ms": null,
    "repair": {
      "next_step": "lookup_labels_then_retry",
      "tool": "github_list_labels",
      "arguments": {"owner": "acme", "repo": "api"},
      "alternative": "If the label was defined but never used, it will not appear in the derived list; re-call github_update_issue with the exact name confirmed in the GitHub UI."
    },
    "request_id": "req_01JGH7Q2M4",
    "fingerprint": "github-issues-mcp@1.0.0+c3f9a12"
  }
}
```

Repair preserves caller intent by routing through the enumeration tool first (the corrected value must be discovered, per `[6.repair-intent]`); the update is atomic, so the agent re-sends the whole corrected call.

### Failure mode 2 — rate limited on search (tool-result error, retryable)

```json
{
  "resultType": "complete",
  "isError": true,
  "content": [
    {"type": "text", "text": "GitHub rate limit exhausted. Retry the same call after 42 seconds."}
  ],
  "structuredContent": {
    "code": "rate_limited",
    "message": "Upstream GitHub rate limit exhausted for the configured token.",
    "temporary": true,
    "retry_after_ms": 42000,
    "rate_limit_remaining": 0,
    "repair": {
      "next_step": "wait_then_retry_same_call",
      "tool": "github_search_issues",
      "arguments": {"owner": "acme", "repo": "api", "labels": ["bug"], "state": "open"},
      "alternative": "Narrow with updated_after= to spend fewer upstream calls per page once the window resets."
    },
    "request_id": "req_01JGH7Q9ZC",
    "fingerprint": "github-issues-mcp@1.0.0+c3f9a12"
  }
}
```

(`temporary: true` — the same unchanged call may succeed later; `repair.arguments` echoes the original still-valid arguments per `[6.repair-intent]`.)

### Failure mode 3 — version conflict on update (tool-result error, non-retryable, repairable)

```json
{
  "resultType": "complete",
  "isError": true,
  "content": [
    {"type": "text", "text": "Issue changed since your read. Re-read with github_get_issue, merge, and re-apply with the fresh version token."}
  ],
  "structuredContent": {
    "code": "conflict",
    "message": "expected_version does not match the issue's current content.",
    "details": {
      "field": "expected_version",
      "value": "v1:sha256:9c41f0",
      "reason": "The issue was modified after this token was minted."
    },
    "temporary": false,
    "retry_after_ms": null,
    "repair": {
      "next_step": "reread_then_reapply",
      "tool": "github_get_issue",
      "arguments": {"owner": "acme", "repo": "api", "issue_number": 512},
      "alternative": "If clobbering is acceptable for this field, re-call github_update_issue without expected_version."
    },
    "request_id": "req_01JGH7QF88",
    "fingerprint": "github-issues-mcp@1.0.0+c3f9a12"
  }
}
```

(`value` is echoed here because the version token is server-minted — known-safe.)

### Failure mode 4 — resource read of a deleted/transferred issue (JSON-RPC carrier)

```json
{
  "jsonrpc": "2.0",
  "id": "req_01JGH7QKA0",
  "error": {
    "code": -32602,
    "message": "Resource not found.",
    "data": {
      "machine_code": "issue_not_found",
      "human_message": "No issue #9999 is visible in acme/api; it may be deleted, transferred, or private to this token.",
      "details": {
        "field": "uri",
        "value": "ghissues://repos/acme/api/issues/9999",
        "reason": "Upstream returned 404 for this issue number."
      },
      "temporary": false,
      "retry_after_ms": null,
      "repair": {
        "next_step": "search_then_read",
        "tool": "github_search_issues",
        "arguments": {"owner": "acme", "repo": "api", "query": "in:title <expected topic>"},
        "alternative": "If the repo was renamed or transferred, browse ghissues://repos/{owner}/{repo}/issues under the new owner."
      },
      "resource_uri": "ghissues://repos/acme/api/issues/9999",
      "request_id": "req_01JGH7QKA0",
      "fingerprint": "github-issues-mcp@1.0.0+c3f9a12"
    }
  }
}
```

Numeric allocation per `[6.jsonrpc-code-allocation]`: unknown/invalid URI → `-32602`; rate-limited or upstream-down resource reads use application codes `-31000`/`-31001` (outside the JSON-RPC reserved range); the spec band `-32020`…`-32022` is emitted only with its spec meanings (`-32021` `MissingRequiredClientCapability` if a request depends on an undeclared capability). Agents branch on `machine_code`, never on the numeric code. Credential failures are three distinct codes (`missing_credential` / `invalid_credential` / `insufficient_scope`, the last carrying `details.required_scopes`) per `[1.cred-modes]`.

## Step 5 — Discovery surface

**No `search_tools`/`describe_tool` layer.** Six compact tools are below the threshold where host-managed disclosure pays; on preloading clients it would only add cost (`[2.progressive-disclosure]`). The lever applied instead is client-independent surface reduction (6 task-completing tools) plus compact definitions, with a serialized `tools/list` byte budget enforced in CI (Step 8 fixture).

**Capability summary** — served three ways so every client class sees it (`[2.instructions-advisory]`): as resource `ghissues://server/summary`, mirrored in the `server/discover` `instructions` field (advisory), with the essentials (prerequisites, negative scope, repair pointers) also present in the six tool descriptions:

```json
{
  "server": {
    "name": "github-issues-mcp",
    "version": "1.0.0",
    "fingerprint": "github-issues-mcp@1.0.0+c3f9a12",
    "transport": "stdio",
    "protocol": {"revision": "2026-07-28", "extensions": []},
    "summary": "Search, read, create, update, comment on, label, and lock/unlock GitHub issues via the configured token."
  },
  "does": [
    "Search and browse issues in one repo or across all repos the token can read.",
    "Read an issue with its full comment thread.",
    "Create issues with labels, assignees, and milestone in one call.",
    "Edit, relabel, assign, close/reopen, and lock/unlock issues, with optimistic-lock protection.",
    "Post comments with idempotent retry support."
  ],
  "does_not": [
    "Pull requests, reviews, or PR comments (issues only; search filters PRs out).",
    "Create, rename, or delete labels or milestones (labels are applied, never defined, here).",
    "Delete or transfer issues, manage reactions, projects, or webhooks.",
    "Real-time updates: no resource subscriptions; freshness is bounded by resources/read ttlMs and annotations.lastModified."
  ],
  "error_carriers": {
    "tool_errors": "structuredContent (isError: true)",
    "non_tool_rpc_errors": "error.data (code/message renamed machine_code/human_message)"
  },
  "value_echo_policy": "details.value is emitted only for server-minted values and published enum members; free-form parameter values (titles, bodies, queries, label/login strings) are never echoed — errors locate the offense by field name and array index instead.",
  "prerequisites": {
    "auth": {
      "mode": "stdio environment credential",
      "credential_source": "GITHUB_TOKEN",
      "required_scopes": ["repo (classic) or Issues: read/write (fine-grained)"],
      "failure_codes": {
        "missing_credential": "GITHUB_TOKEN is not set",
        "invalid_credential": "GitHub rejected the token",
        "insufficient_scope": "token lacks issue write access; see details.required_scopes"
      }
    },
    "negotiated_capabilities": {
      "server": ["capabilities.completions"],
      "client": [],
      "fallbacks": {
        "no_completions": "invalid_field / *_not_found errors carry repair calls (github_list_labels, github_search_issues) that enumerate valid values"
      }
    },
    "state_handles": {
      "cursors": "opaque HMAC-protected tokens, valid 15 minutes, authorization re-checked on every continuation; expiry returns cursor_expired with restart repair",
      "version_tokens": "content-derived sha256 over canonicalized {title, body, state, state_reason, labels, assignees, milestone, locked} (sorted keys/lists); returned by every read and mutation, compared by expected_version"
    },
    "rate_limits": "GitHub's per-token budget; rate_limited errors carry retry_after_ms and rate_limit_remaining; success results include rate_limit_remaining when under 10% budget"
  }
}
```

**Resources** (read-oriented browsing; every read is also reachable through tools for clients that expose resources poorly — `github_search_issues`, `github_get_issue`, `github_list_labels` are self-sufficient from `tools/list` alone, per `[4.tool-fallback]`):

| URI / template | Content |
|---|---|
| `ghissues://server/summary` | The capability summary above. `ttlMs` 86400000, `cacheScope: "public"`. |
| `ghissues://server/errors` | Full per-tool error-code catalog + the envelope schema (the on-demand half of `[6.document-codes]`). |
| `ghissues://repos/{owner}/{repo}/issues{?state,labels,assignee_login,updated_after}` | Index of issue summaries — triage metadata only (`title`, `description` = preview, `mimeType`, `size`, `annotations.lastModified`), never bodies. |
| `ghissues://repos/{owner}/{repo}/issues/{issue_number}` | Full issue body incl. `version`. |
| `ghissues://repos/{owner}/{repo}/issues/{issue_number}/comments` | Comment thread, chunked 30/chunk with in-band `next_chunk_uri` (ex§4 pattern); chunk URIs published as a template. |
| `ghissues://repos/{owner}/{repo}/labels` | Derived label index (same content as `github_list_labels`). |

All templates published via `resources/templates/list`. `completion/complete` (server advertises `completions`) serves `{owner}` and `{repo}` from the token's accessible repos and `{labels}` from the derived label index — proactive prevention of the invalid-name first call. `resources/read` results carry honest cache hints: issue bodies `ttlMs: 30000, cacheScope: "private"` (visibility varies by token); the summary is public/long-lived. No `resources.subscribe` — issues mutate upstream outside our sight; staleness is bounded by short `ttlMs` + `annotations.lastModified` instead, and that limitation is in `does_not`.

**Versioning (§9):** capability fingerprint `github-issues-mcp@<semver>+<hash>` published in the summary, discovery responses, and every error envelope; `covers` = tools, resources, templates, completion support, error codes, summary. `tools/list` ordering deterministic (alphabetical). `notifications/tools/list_changed` etc. emitted to `subscriptions/listen` opt-ins on catalog change; deprecations will ride `_meta` `com.github-issues-mcp/lifecycle` on the native record plus the fingerprint, with a committed removal window (marker shape per `[9.deprecation-marker]`).

## Done-criteria walk (checklist coverage)

| § | Status |
|---|---|
| §1 Server-Level | Answered: distinctive name, stdio transport (no stdout logging), three credential failure codes, no implicit state beyond declared handles (cursors, version tokens — discipline declared), observability in responses (`rate_limit_remaining`, `retry_after_ms`, fingerprint), revision target declared, roots N/A (no filesystem scope). |
| §2 Discovery | Answered: capability summary on three surfaces, negative scope explicit, compact 6-tool baseline with CI byte budget, no disclosure layer (justified), completion for owner/repo/label, index-not-bodies resources. |
| §3 Tools | Answered: task-completing granularity with recorded exceptions, closed schemas, disambiguated names (`assignee_logins`, `updated_after`, `issue_number`), omission/clearing semantics per optional field, per-tool `outputSchema` + `structuredContent`, honest annotations with declared sibling differences, idempotency keys + timeout reconcile paths, version preconditions. |
| §4 Resources | Answered: stable hierarchical URIs, templates + completion, chunked comments with in-band next pointers, triage metadata, tool fallback self-sufficient, no subscriptions (declared, ttl-bounded). |
| §5 Prompts | **N/A** — no prompts shipped; nothing is load-bearing outside tool/resource schemas (`[5.optional]`). |
| §6 Failure Recovery | Answered: symbolic taxonomy, unified envelope on both carriers with the mandatory rename, field-level details with disclosed value-echo policy, retryability invariants, single `repair` object naming callable tools, correlation (`request_id`, `resource_uri`, `fingerprint`). |
| §7 Long-Running Ops | **N/A** — every operation is one synchronous upstream round-trip (worst case a few seconds); no tasks extension advertised, blocking `tools/call` throughout; timeout ambiguity handled by idempotency keys + reconcile guidance, not tasks. |
| §8 Token Efficiency | Answered: summary default with `detail` toggle (density-only, strict subset), house `has_more`/`next_cursor` in tool payloads (labeled convention) vs. native `nextCursor` + `ttlMs`/`cacheScope` on list/read methods, explicit truncation with callable hints, cursor lifetime + `cursor_expired`, RFC3339 UTC throughout, nulls stripped from concise responses except contract-required `retry_after_ms`. |
| §9 Versioning | Answered: fingerprint with `covers`, deterministic ordering, list-changed notifications, deprecation-marker convention reserved, error codes in the versioned surface. |

**Eval note (Steps 8–9):** the fixture suite is specified — cold-start (`tools/list` byte budget in CI), first-repair (inject `label_not_found`, assert next call is `github_list_labels` then a corrected `github_update_issue` in one hop), `forced_error` per tool asserting `isError: true` + envelope carrier on the serialized wire result, and a `host_capture` fixture per target client (which also gates any future stringified-argument shim — none is included now, since no captured evidence exists). Step 9 is recorded **not-run**: no runnable host or agent harness exists at design time; running the suite against a reference implementation is the prerequisite before the contract is declared regression-gated.
