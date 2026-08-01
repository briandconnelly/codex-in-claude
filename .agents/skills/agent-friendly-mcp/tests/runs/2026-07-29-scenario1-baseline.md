# Scenario 1 (Design) — baseline run (2026-07-28 rebase re-run)

- **Date:** 2026-07-29
- **Tree:** `1b0b743` (main, post #130 rebase + #133 §6 fixes)
- **Mode:** baseline (fresh general-purpose subagent on `claude-fable-5`, prompt only, forbidden from reading the repo/skill or the web; 0 tool-uses confirms no skill access)
- **Score:** 7/10

## Exact prompt given

The Scenario 1 prompt from `tests/scenarios.md`, verbatim, wrapped with a guardrail forbidding all tool use (no file reads — especially `agent-friendly-mcp/` — no search, no web).

## Per-assertion scoring

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| A1 | Task-completing granularity: 11 endpoints → ~4–7 tools; labels folded into update, lock/unlock folded or justified | **PASS** | 6 tools with an explicit endpoint→tool collapse table; labels folded into `update_issue` (`add_labels`/`remove_labels`/`set_labels`); lock/unlock folded into boolean-state `set_locked`. |
| A2 | Names `snake_case`, service-prefixed, verb+noun | **FAIL** | `search_issues`, `get_issue`, … are snake_case verb+noun but carry no service prefix (`github_*`). |
| A3 | `additionalProperties: false`, disambiguated parameter names, declared omission semantics, `default` only where server-applied | **PASS** (caveat) | `additionalProperties: false` on every schema; omission semantics declared (`query` omitted = list API, `include_comments` default false). Caveat: uses `number` rather than the exemplar `issue_number`, though always paired with required `repo`. |
| A4 | ≥2 error payloads with stable symbolic codes, field-level detail, repair hint naming a real callable surface | **PASS** | Closed code enum; `details.field`/`rejected_value`/`valid_values`; `suggested_action` names `search_issues` without `query` and the corrected label value. |
| A5 | Errors return as tool result errors (`isError: true`), not JSON-RPC | **PASS** | "Errors are tool results with `isError: true` (not protocol errors)". |
| A6 | Capability summary states what the server does NOT do | **PASS** | `instructions` opens with "not pull requests — … this server cannot modify them"; a "Deliberately absent" section lists withheld surfaces. |
| A7 | Pagination cursor-based, provenance-correct, concise default with detail toggle | **PASS** (caveat) | Opaque `cursor`/`next_cursor` on tool results; `response_format: "concise"\|"detailed"` toggle with concise default. Caveat: native-list (`nextCursor`) provenance never discussed — not violated, but not demonstrated either. |
| A8 | Annotations present and honest | **PASS** | `readOnlyHint: true` only on reads; `idempotentHint: true` on `update_issue`/`set_locked`; create/comment not marked idempotent; `openWorldHint: true`. |
| A9 | `outputSchema` published; success as conforming `structuredContent` with `content` fallback | **FAIL** | Claimed in prose ("`outputSchema` on every tool"; results in both text and `structuredContent`) but no output schema is actually shown — same prose-only failure as the 2026-07-11 baseline. |
| A10 | **(Scored.)** Error path contract-correct and distinct: §6 envelope in `structuredContent` on `isError: true`, `outputSchema` scoped to success (stated as the skill's reading) or a documented union | **FAIL** | The error envelope is structured, but the design never addresses whether `outputSchema` covers error results — neither a success-only scoping statement nor a union; the success/error shape relationship is undefined. |

**Passed:** A1, A3, A4, A5, A6, A7, A8 (7/10).
A much stronger baseline than 2026-07-11's 4/9 (different model tier; assertion set also gained A10 since).
The remaining gaps are exactly the skill's most contract-specific teachings: service-prefixed naming, a shown `outputSchema` with the `structuredContent`/`content` pairing, and the success-scoped-`outputSchema` error-path rule.

## Full final output

Verbatim final message returned by the baseline subagent (0 tool calls):

# Agent-Facing MCP Contract: GitHub Issues

## Design stance

The 11 REST endpoints collapse to **6 tools**. Agents pay for every tool in context and every near-duplicate in confusion, so the contract is organized around agent intents (find, read, create, modify, discuss, moderate), not HTTP routes:

| REST endpoints | Tool |
|---|---|
| list issues, search issues | `search_issues` |
| get issue, list comments | `get_issue` |
| create issue | `create_issue` |
| update issue, add labels, remove labels | `update_issue` |
| add comment | `add_comment` |
| lock issue, unlock issue | `set_locked` |

Cross-cutting conventions:

- **Issue identity** is always `repo` (`"owner/name"`) + `number`. No internal node IDs in inputs; the server resolves those.
- **Pagination** is an opaque `cursor` in, `next_cursor` out (null when exhausted). Never page numbers — agents mishandle offset math and the search API's pagination differs from list's; the cursor hides that.
- **`response_format: "concise" | "detailed"`** on read tools, default `"concise"`. Concise is number, title, state, labels, assignees, updated_at, comment_count — enough to decide whether to fetch detail without burning tokens on full bodies.
- All read results are returned both as human-readable text and as `structuredContent` conforming to each tool's `outputSchema`.
- Timestamps are ISO 8601 UTC. All schemas set `additionalProperties: false` so typos fail loudly instead of being silently dropped.

## Tools

### 1. `search_issues` — read-only

*Description (verbatim, since descriptions are the contract):* "Find issues in a repository. Use filter fields for structural queries (state/labels/assignee); use `query` for full-text search with GitHub search syntax (e.g. `crash in parser label:bug`). Returns concise summaries — call `get_issue` for bodies and comments."

```json
{
  "type": "object",
  "properties": {
    "repo":   { "type": "string", "pattern": "^[^/\\s]+/[^/\\s]+$",
                "description": "owner/name, e.g. 'anthropics/skills'. Required unless query contains a repo: qualifier." },
    "query":  { "type": "string",
                "description": "Full-text search terms, GitHub search syntax. Omit for a pure filter listing (cheaper, not rate-limited like search)." },
    "state":  { "type": "string", "enum": ["open", "closed", "all"], "default": "open" },
    "labels": { "type": "array", "items": { "type": "string" },
                "description": "Issues must carry ALL listed labels." },
    "assignee": { "type": "string", "description": "GitHub login, or '*' for any assignee, 'none' for unassigned." },
    "sort":   { "type": "string", "enum": ["created", "updated", "comments", "relevance"], "default": "updated",
                "description": "'relevance' is only valid with query." },
    "order":  { "type": "string", "enum": ["asc", "desc"], "default": "desc" },
    "limit":  { "type": "integer", "minimum": 1, "maximum": 50, "default": 20 },
    "cursor": { "type": "string", "description": "Opaque; pass next_cursor from the previous page." },
    "response_format": { "type": "string", "enum": ["concise", "detailed"], "default": "concise" }
  },
  "additionalProperties": false
}
```

Output: `{ items: IssueSummary[], next_cursor: string|null, total_count: integer|null, truncated: boolean }`. The server routes to the list API when `query` is absent and the search API when present — the agent never chooses an endpoint.

### 2. `get_issue` — read-only

```json
{
  "type": "object",
  "properties": {
    "repo":   { "type": "string", "pattern": "^[^/\\s]+/[^/\\s]+$" },
    "number": { "type": "integer", "minimum": 1 },
    "include_comments": { "type": "boolean", "default": false,
      "description": "Include the newest 20 comments. Use comments_cursor from the response to page older ones." },
    "comments_cursor": { "type": "string" }
  },
  "required": ["repo", "number"],
  "additionalProperties": false
}
```

Output: full issue (title, body, state, state_reason, labels, assignees, milestone, locked, timestamps, author) plus `comments`, `comments_next_cursor`, `comments_total`. This subsumes "list comments"; a comment thread is never useful detached from its issue.

### 3. `create_issue` — write, not idempotent

```json
{
  "type": "object",
  "properties": {
    "repo":      { "type": "string", "pattern": "^[^/\\s]+/[^/\\s]+$" },
    "title":     { "type": "string", "minLength": 1, "maxLength": 256 },
    "body":      { "type": "string", "description": "Markdown. Strongly recommended; issues without bodies are hard to triage." },
    "labels":    { "type": "array", "items": { "type": "string" },
                   "description": "Must already exist in the repo — see the labels resource or the validation error, which lists valid labels." },
    "assignees": { "type": "array", "items": { "type": "string" }, "maxItems": 10 }
  },
  "required": ["repo", "title"],
  "additionalProperties": false
}
```

Output: `{ number, url, state: "open" }`. Description warns: "Creates a new issue every call — search first to avoid duplicates."

### 4. `update_issue` — write, idempotent

One mutation tool instead of update/add-labels/remove-labels, because agents reliably fumble "which of the three label tools do I call" and the read-modify-write race that `labels: [...]` replacement invites. Only fields present are changed.

```json
{
  "type": "object",
  "properties": {
    "repo":   { "type": "string", "pattern": "^[^/\\s]+/[^/\\s]+$" },
    "number": { "type": "integer", "minimum": 1 },
    "title":  { "type": "string", "minLength": 1, "maxLength": 256 },
    "body":   { "type": "string", "description": "Replaces the entire body. Fetch current body first if editing, not rewriting." },
    "state":  { "type": "string", "enum": ["open", "closed"] },
    "state_reason": { "type": "string", "enum": ["completed", "not_planned", "reopened"],
                      "description": "Only with state; 'reopened' only with state=open." },
    "add_labels":    { "type": "array", "items": { "type": "string" },
                       "description": "Additive — existing labels are kept. Preferred over set_labels." },
    "remove_labels": { "type": "array", "items": { "type": "string" },
                       "description": "Removing a label the issue lacks is a no-op, not an error." },
    "set_labels":    { "type": "array", "items": { "type": "string" },
                       "description": "Replace ALL labels. Mutually exclusive with add/remove_labels." },
    "assignees":     { "type": "array", "items": { "type": "string" }, "description": "Replaces the assignee set." }
  },
  "required": ["repo", "number"],
  "additionalProperties": false
}
```

Output: the updated concise issue, so the agent confirms the result without a follow-up `get_issue`.

### 5. `add_comment` — write, not idempotent

```json
{
  "type": "object",
  "properties": {
    "repo":   { "type": "string", "pattern": "^[^/\\s]+/[^/\\s]+$" },
    "number": { "type": "integer", "minimum": 1 },
    "body":   { "type": "string", "minLength": 1, "description": "Markdown." }
  },
  "required": ["repo", "number", "body"],
  "additionalProperties": false
}
```

Output: `{ comment_id, url }`. Description warns it posts publicly and fails with `issue_locked` on locked issues unless the token has maintainer rights.

### 6. `set_locked` — write, idempotent

Lock/unlock as one boolean-state tool: the agent states the desired end state; setting the state it's already in succeeds as a no-op.

```json
{
  "type": "object",
  "properties": {
    "repo":   { "type": "string", "pattern": "^[^/\\s]+/[^/\\s]+$" },
    "number": { "type": "integer", "minimum": 1 },
    "locked": { "type": "boolean" },
    "lock_reason": { "type": "string", "enum": ["off-topic", "too heated", "resolved", "spam"],
                     "description": "Only valid when locked=true." }
  },
  "required": ["repo", "number", "locked"],
  "additionalProperties": false
}
```

## Error shape

Errors are tool results with `isError: true` (not protocol errors — the agent should see and recover from them), carrying one uniform structure in both text and `structuredContent`:

```json
{
  "error": {
    "code": "<machine-readable enum>",
    "message": "<one-line human summary>",
    "retriable": false,
    "retry_after_seconds": null,
    "details": { },
    "suggested_action": "<imperative next step for the agent>"
  }
}
```

`code` is a closed enum: `not_found | validation_failed | permission_denied | issue_locked | rate_limited | conflict | upstream_unavailable`. Two worked failure modes:

**Rate limited (secondary/search limit hit):**

```json
{
  "error": {
    "code": "rate_limited",
    "message": "GitHub search API rate limit exceeded for this token.",
    "retriable": true,
    "retry_after_seconds": 42,
    "details": { "limit": 30, "remaining": 0, "resets_at": "2026-07-29T18:04:11Z" },
    "suggested_action": "Wait 42s and retry, or re-issue search_issues without 'query' — filter-only listings use a separate, larger quota."
  }
}
```

**Validation failure (unknown label on create/update):**

```json
{
  "error": {
    "code": "validation_failed",
    "message": "Label 'preformance' does not exist in octocat/hello-world.",
    "retriable": false,
    "retry_after_seconds": null,
    "details": {
      "field": "add_labels[0]",
      "rejected_value": "preformance",
      "did_you_mean": ["performance"],
      "valid_values": ["bug", "documentation", "enhancement", "performance", "wontfix"]
    },
    "suggested_action": "Retry with a label from valid_values; 'performance' is the closest match."
  }
}
```

The pattern generalizes: every error answers "can I retry?", "with what change?", and includes the data needed to self-correct in one turn (valid labels enumerated in the error rather than forcing a discovery round-trip). `not_found` distinguishes repo-not-found from issue-not-found in `details.entity` and suggests `search_issues` for renamed/transferred issues; `issue_locked` tells the agent locking blocked the comment, which no amount of retrying fixes.

## Discovery surface

**1. Server `instructions` (returned at `initialize`)** — the primary orientation document, ~150 words, covering exactly what tool descriptions can't:

> Tools for reading and managing GitHub issues (not pull requests — PRs appearing in search results are marked `is_pull_request: true`; this server cannot modify them). Typical flow: `search_issues` → `get_issue` → mutate. Search before creating to avoid duplicates. All reads default to concise summaries; request `response_format: "detailed"` only when you need bodies. Writes act as the authenticated user; the token's identity and write scope are in the `github://viewer` resource. Errors carry a `suggested_action` — follow it before retrying blindly. `retriable: true` with `retry_after_seconds` means wait, then retry once.

**2. Tool annotations** — every tool declares MCP annotations so hosts can gate confirmation UX correctly: `readOnlyHint: true` on `search_issues`/`get_issue`; `idempotentHint: true` on `update_issue`/`set_locked`; `destructiveHint: false` everywhere (nothing here deletes data — closing is reversible); `openWorldHint: true` (external service).

**3. `outputSchema` on every tool**, so agents can rely on field names (`next_cursor`, `total_count`, `error.code`) without parsing prose.

**4. Resources** for reference data that parameterizes writes but isn't worth a tool slot:

| URI | Content |
|---|---|
| `github://viewer` | Authenticated login, granted scopes, rate-limit status — lets an agent predict a `permission_denied` before attempting a write |
| `github://repos/{owner}/{repo}/labels` (resource template) | Name, color, description of each label — for choosing labels *before* triggering the validation error path |
| `github://repos/{owner}/{repo}/milestones` | Open milestones, for update_issue |

**Deliberately absent:** no `get_capabilities` or `help` tool (that's what `instructions` are for), no raw-passthrough tool, no separate `list_comments`/`lock`/`unlock`/`add_labels`/`remove_labels` tools — each would be a second home for behavior another surface already owns, and divergent duplicates fail silently.
