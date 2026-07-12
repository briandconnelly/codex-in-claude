# Scenario 1 (Design) — baseline run

- **Date:** 2026-07-11
- **Tree:** `d586ce3` (main, post review-batch #53)
- **Mode:** baseline (fresh general-purpose subagent, prompt only, instructed not to read the repo/skill; 0 tool-uses confirms no skill access)
- **Score:** 4/9

## Exact prompt given

> Design the agent-facing MCP server contract for a service wrapping the GitHub Issues REST API.
> The underlying API has these endpoints: list issues, get issue, create issue, update issue, add comment, list comments, lock issue, unlock issue, add labels, remove labels, search issues.
> Produce: the tool list with input schemas, the error shape for at least two failure modes, and whatever discovery surface you think agents need.

(Wrapped with a guardrail forbidding reading any repo file, especially under `agent-friendly-mcp/`.)

## Per-assertion scoring

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| A1 | Tools task-completing, 11 endpoints → ~4–7 tools | **FAIL** | Produced 11 endpoint-mirroring tools (`issues_list`, `issue_get`, `issue_create`, `issue_update`, `comment_add`, `comments_list`, `issue_lock`, `issue_unlock`, `labels_add`, `labels_remove`, `issues_search`) plus 1 more — one-per-endpoint; lock/unlock and labels_add/remove kept separate rather than folded into update. |
| A2 | Names snake_case, service-prefixed, verb+noun | **FAIL** | snake_case yes, but no service prefix (`issue_get`, not `github_get_issue`) and noun_verb ordering, not verb+noun. |
| A3 | `additionalProperties:false`, disambiguated names, omission semantics, `default` discipline | **PASS** | `additionalProperties:false` on every schema; `issue_number` not `issue`; enums with server-applied `default`s; "Omit to leave labels untouched"; nullable cursor semantics. |
| A4 | ≥2 error payloads: symbolic codes, field detail, repair naming a real surface | **PASS** | `ISSUE_NOT_FOUND` (remediation names `issues_list`/`issues_search`), `RATE_LIMITED` (retry_after), `VALIDATION_FAILED` (field-level `invalid_values`), `FORBIDDEN`. |
| A5 | Errors as tool result errors (`isError:true`), not JSON-RPC | **PASS** | "The MCP tool result sets `isError: true`; the structured payload is …". |
| A6 | Capability summary stating what the server does NOT do | **FAIL** | `server_capabilities` tool lists limits/scopes but states no explicit negative scope ("this server does NOT …"). |
| A7 | Pagination cursor-based + provenance-correct + concise default with `detail` toggle | **FAIL** | Cursor-based `page_info` present, but no native (`nextCursor`) vs house (`has_more`) provenance distinction and no concise/detailed response toggle. |
| A8 | Annotations present and honest (create not read-only/idempotent) | **PASS** | `issue_create` → `readOnlyHint:false, destructiveHint:false, idempotentHint:false`; reads marked `readOnlyHint:true, idempotentHint:true`. |
| A9 | Tool defs publish `outputSchema`; success = conforming `structuredContent`; `content` fallback | **FAIL** | Prose claims "Tools declare an `outputSchema` and return `structuredContent`," but no tool definition actually includes an `outputSchema` block. |

**Passed:** A3, A4, A5, A8 (4/9).
Baseline confirms the scenario is not trivially passable and isolates the skill-sensitive gaps: granularity (A1), naming convention (A2), negative scope (A6), pagination provenance/detail toggle (A7), and shown `outputSchema` (A9).

## Full final output

Verbatim final message returned by the baseline subagent (no tool noise to elide — the run made 0 tool calls):

# Agent-Facing MCP Server Contract: GitHub Issues

This is a design produced from knowledge of MCP and API design — it wraps the eleven listed REST endpoints but shapes them for an LLM agent consumer, not as a 1:1 REST mirror.

---

## 1. Design principles applied

- **Task-named tools, not endpoint-named.** Agents match tools by intent.
  Names use a `noun_verb` grouping (`issue_get`, `comments_list`) so related tools cluster in the tool list.
- **One obvious way to address a repo.** Every tool takes `owner` + `repo` as separate strings (never a pre-joined `"owner/repo"` the agent has to assemble and mis-assemble). `issue_number` is the human-visible number, never the opaque internal `id` — agents see issue numbers in URLs and text.
- **Annotations tell the agent what's safe.** Each tool carries MCP `annotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`) so a host can gate writes without parsing descriptions.
- **Pagination is explicit and bounded.** List/search tools return a `page_info` object with an opaque `next_cursor`; the server caps `per_page` so an agent can't accidentally pull 10k issues into context.
- **Structured output.** Tools declare an `outputSchema` and return `structuredContent`, plus a short human-readable `text` summary.
  Agents get typed fields; humans get a glanceable line.
- **Errors are data, not prose.** A single typed error envelope with a machine `code`, a `retryable` flag, and a `remediation` hint the agent can act on.

---

## 2. Shared conventions

**Repo addressing** (mixed into every tool's schema):

```json
"owner": { "type": "string", "description": "Repository owner (user or org login), e.g. 'octocat'." },
"repo":  { "type": "string", "description": "Repository name only, without owner, e.g. 'hello-world'." }
```

**Pagination envelope** returned by every list/search tool:

```json
"page_info": {
  "type": "object",
  "properties": {
    "returned":    { "type": "integer", "description": "Number of items in this response." },
    "has_more":    { "type": "boolean" },
    "next_cursor": { "type": ["string", "null"], "description": "Opaque token; pass as 'cursor' to fetch the next page. null when has_more is false." }
  },
  "required": ["returned", "has_more", "next_cursor"]
}
```

Cursor-based (not raw page numbers) so the agent never does arithmetic; `per_page` accepted but clamped to `[1, 100]`, default `30`.

---

## 3. Tool list

### 3.1 `issues_list` — list issues in a repo
Annotations: `readOnlyHint: true`, `idempotentHint: true`.

```json
{
  "name": "issues_list",
  "description": "List issues in a repository, filtered by state, labels, assignee, or milestone. Pull requests are excluded. For free-text or cross-repo queries use issues_search instead.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "owner": { "type": "string" },
      "repo":  { "type": "string" },
      "state": { "type": "string", "enum": ["open", "closed", "all"], "default": "open" },
      "labels": { "type": "array", "items": { "type": "string" }, "description": "Only issues carrying ALL of these label names." },
      "assignee": { "type": "string", "description": "Login of the assignee, or '*' for any, or 'none' for unassigned." },
      "creator": { "type": "string" },
      "milestone": { "type": "string", "description": "Milestone number as string, '*' for any, or 'none'." },
      "sort": { "type": "string", "enum": ["created", "updated", "comments"], "default": "created" },
      "direction": { "type": "string", "enum": ["asc", "desc"], "default": "desc" },
      "since": { "type": "string", "format": "date-time", "description": "Only issues updated at or after this ISO-8601 timestamp." },
      "per_page": { "type": "integer", "minimum": 1, "maximum": 100, "default": 30 },
      "cursor": { "type": ["string", "null"], "description": "Opaque page cursor from a previous page_info.next_cursor." }
    },
    "required": ["owner", "repo"],
    "additionalProperties": false
  }
}
```

### 3.2 `issue_get` — get a single issue
Annotations: `readOnlyHint: true`, `idempotentHint: true`.

```json
{
  "name": "issue_get",
  "inputSchema": {
    "type": "object",
    "properties": {
      "owner": { "type": "string" },
      "repo":  { "type": "string" },
      "issue_number": { "type": "integer", "minimum": 1, "description": "Human-visible issue number, not the internal id." }
    },
    "required": ["owner", "repo", "issue_number"],
    "additionalProperties": false
  }
}
```

### 3.3 `issue_create`
Annotations: `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: false`.

```json
{
  "name": "issue_create",
  "description": "Open a new issue. Creating is not idempotent — calling twice creates two issues.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "owner": { "type": "string" },
      "repo":  { "type": "string" },
      "title": { "type": "string", "minLength": 1 },
      "body":  { "type": "string", "description": "Markdown body." },
      "assignees": { "type": "array", "items": { "type": "string" } },
      "labels": { "type": "array", "items": { "type": "string" } },
      "milestone": { "type": "integer", "description": "Milestone number to associate." }
    },
    "required": ["owner", "repo", "title"],
    "additionalProperties": false
  }
}
```

### 3.4 `issue_update`
Annotations: `readOnlyHint: false`, `destructiveHint: false` (edits are recoverable), `idempotentHint: true` (same payload → same end state).

```json
{
  "name": "issue_update",
  "description": "Modify fields of an existing issue. Only the fields you provide are changed. To change state, use 'state' with optional 'state_reason'. This does NOT add/remove individual labels incrementally — providing 'labels' REPLACES the full set; use labels_add / labels_remove for incremental changes.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "owner": { "type": "string" },
      "repo":  { "type": "string" },
      "issue_number": { "type": "integer", "minimum": 1 },
      "title": { "type": "string" },
      "body":  { "type": "string" },
      "state": { "type": "string", "enum": ["open", "closed"] },
      "state_reason": { "type": "string", "enum": ["completed", "not_planned", "reopened"] },
      "assignees": { "type": "array", "items": { "type": "string" }, "description": "Replaces the assignee set." },
      "labels": { "type": "array", "items": { "type": "string" }, "description": "Replaces the entire label set. Omit to leave labels untouched." },
      "milestone": { "type": ["integer", "null"], "description": "Milestone number, or null to clear." }
    },
    "required": ["owner", "repo", "issue_number"],
    "additionalProperties": false
  }
}
```

### 3.5 `comment_add`
Annotations: `readOnlyHint: false`, `idempotentHint: false`.

```json
{
  "name": "comment_add",
  "inputSchema": {
    "type": "object",
    "properties": {
      "owner": { "type": "string" },
      "repo":  { "type": "string" },
      "issue_number": { "type": "integer", "minimum": 1 },
      "body": { "type": "string", "minLength": 1, "description": "Markdown comment body." }
    },
    "required": ["owner", "repo", "issue_number", "body"],
    "additionalProperties": false
  }
}
```

### 3.6 `comments_list`
Annotations: `readOnlyHint: true`, `idempotentHint: true`.

```json
{
  "name": "comments_list",
  "inputSchema": {
    "type": "object",
    "properties": {
      "owner": { "type": "string" },
      "repo":  { "type": "string" },
      "issue_number": { "type": "integer", "minimum": 1 },
      "since": { "type": "string", "format": "date-time" },
      "per_page": { "type": "integer", "minimum": 1, "maximum": 100, "default": 30 },
      "cursor": { "type": ["string", "null"] }
    },
    "required": ["owner", "repo", "issue_number"],
    "additionalProperties": false
  }
}
```

### 3.7 `issue_lock`
Annotations: `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: true`.

```json
{
  "name": "issue_lock",
  "description": "Lock an issue's conversation. Idempotent: locking an already-locked issue with the same reason succeeds.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "owner": { "type": "string" },
      "repo":  { "type": "string" },
      "issue_number": { "type": "integer", "minimum": 1 },
      "lock_reason": { "type": "string", "enum": ["off-topic", "too heated", "resolved", "spam"] }
    },
    "required": ["owner", "repo", "issue_number"],
    "additionalProperties": false
  }
}
```

### 3.8 `issue_unlock`
Annotations: `readOnlyHint: false`, `idempotentHint: true`.
Same shape minus `lock_reason`.

```json
{
  "name": "issue_unlock",
  "inputSchema": {
    "type": "object",
    "properties": {
      "owner": { "type": "string" },
      "repo":  { "type": "string" },
      "issue_number": { "type": "integer", "minimum": 1 }
    },
    "required": ["owner", "repo", "issue_number"],
    "additionalProperties": false
  }
}
```

### 3.9 `labels_add` — incremental add
Annotations: `readOnlyHint: false`, `idempotentHint: true` (re-adding an existing label is a no-op).

```json
{
  "name": "labels_add",
  "description": "Add one or more labels to an issue WITHOUT removing existing labels. Use this rather than issue_update when you only want to add.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "owner": { "type": "string" },
      "repo":  { "type": "string" },
      "issue_number": { "type": "integer", "minimum": 1 },
      "labels": { "type": "array", "items": { "type": "string" }, "minItems": 1 }
    },
    "required": ["owner", "repo", "issue_number", "labels"],
    "additionalProperties": false
  }
}
```

### 3.10 `labels_remove` — incremental remove
Annotations: `readOnlyHint: false`, `idempotentHint: true`.

```json
{
  "name": "labels_remove",
  "description": "Remove specific labels from an issue. Removing a label that is not present succeeds as a no-op (per label).",
  "inputSchema": {
    "type": "object",
    "properties": {
      "owner": { "type": "string" },
      "repo":  { "type": "string" },
      "issue_number": { "type": "integer", "minimum": 1 },
      "labels": { "type": "array", "items": { "type": "string" }, "minItems": 1, "description": "Label names to remove." }
    },
    "required": ["owner", "repo", "issue_number", "labels"],
    "additionalProperties": false
  }
}
```

### 3.11 `issues_search` — cross-repo query
Annotations: `readOnlyHint: true`, `idempotentHint: true`, `openWorldHint: true`.

```json
{
  "name": "issues_search",
  "description": "Search issues across one or many repositories using GitHub's issue search qualifiers (e.g. 'repo:octocat/hello-world is:open label:bug author:jane'). Use for free-text or cross-repo lookups. For a plain listing of one repo's issues, prefer issues_list (cheaper, higher rate limit).",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": { "type": "string", "minLength": 1, "description": "Full search query including qualifiers. The server appends 'type:issue' automatically to exclude pull requests." },
      "sort": { "type": "string", "enum": ["created", "updated", "comments", "reactions", "best-match"], "default": "best-match" },
      "order": { "type": "string", "enum": ["asc", "desc"], "default": "desc" },
      "per_page": { "type": "integer", "minimum": 1, "maximum": 100, "default": 30 },
      "cursor": { "type": ["string", "null"] }
    },
    "required": ["query"],
    "additionalProperties": false
  }
}
```

**Design note on labels:** I kept `labels_add`/`labels_remove` as separate incremental tools (matching the listed endpoints) and explicitly documented in `issue_update` that its `labels` field is a *full replace*.
This is the single most common agent footgun with this API — an agent reads "update issue, set labels" and silently wipes existing labels.
The cross-references in the descriptions steer it to the incremental tools.

---

## 4. Result envelope & error shape

### Success (structuredContent for `issue_get`, abbreviated)

```json
{
  "issue": {
    "number": 1347,
    "title": "Found a bug",
    "state": "open",
    "state_reason": null,
    "locked": false,
    "labels": ["bug", "triage"],
    "assignees": ["octocat"],
    "milestone": { "number": 3, "title": "v2" },
    "comments": 2,
    "author": "monalisa",
    "created_at": "2026-06-01T12:00:00Z",
    "updated_at": "2026-06-02T09:30:00Z",
    "html_url": "https://github.com/octocat/hello-world/issues/1347",
    "body": "It broke."
  }
}
```

### Error envelope (all tools, uniform)

The MCP tool result sets `isError: true`; the structured payload is:

```json
{
  "error": {
    "code": "string, stable machine identifier",
    "message": "one-line human summary",
    "http_status": 0,
    "retryable": false,
    "retry_after_seconds": null,
    "remediation": "concrete next action the agent can take",
    "details": {}
  }
}
```

**Failure mode A — issue does not exist (404):**

```json
{
  "error": {
    "code": "ISSUE_NOT_FOUND",
    "message": "No issue #99999 in octocat/hello-world.",
    "http_status": 404,
    "retryable": false,
    "retry_after_seconds": null,
    "remediation": "Verify the issue_number with issues_list or issues_search. Note: a 404 can also mean the token lacks read access to a private repo — check the repository is spelled correctly and accessible.",
    "details": { "owner": "octocat", "repo": "hello-world", "issue_number": 99999 }
  }
}
```

**Failure mode B — rate limited / secondary limit (403 or 429):**

```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "GitHub API rate limit exceeded.",
    "http_status": 429,
    "retryable": true,
    "retry_after_seconds": 47,
    "remediation": "Wait retry_after_seconds before retrying. If this recurs, batch reads via issues_search instead of many issue_get calls.",
    "details": { "limit": 5000, "remaining": 0, "resets_at": "2026-07-11T14:05:00Z", "resource": "core" }
  }
}
```

**Failure mode C — validation (422), included because agents produce malformed writes:**

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "Cannot create issue: 'assignees' contains a login that is not a repository collaborator.",
    "http_status": 422,
    "retryable": false,
    "retry_after_seconds": null,
    "remediation": "Remove or correct the invalid assignee. Only users with push access can be assigned.",
    "details": { "field": "assignees", "invalid_values": ["ghost-user"] }
  }
}
```

**Failure mode D — permission (403 non-rate-limit):** `code: "FORBIDDEN"`, `retryable: false`, remediation pointing at token scopes (`issues:write`).
Distinguished from `RATE_LIMITED` so the agent doesn't back off and retry a permission error forever.

The key agent-facing property: `retryable` + `retry_after_seconds` let the agent decide back-off vs. give-up without regex-matching prose, and `code` is stable so prompt/tool logic can branch on it.

---

## 5. Discovery surface

An agent needs three things beyond the tool list: (a) what the server can do and its limits, (b) reference data (valid labels, assignees) so it doesn't guess, and (c) worked examples.
I expose these as **MCP resources** plus one **capabilities tool**.

### 5.1 Server capabilities (MCP `initialize` + a tool)

`serverInfo` advertises name/version and `capabilities.tools.listChanged`.
Plus a zero-arg tool for runtime facts an agent should check before planning bulk work:

```json
{
  "name": "server_capabilities",
  "description": "Return this server's limits and current auth context. Call before bulk operations.",
  "inputSchema": { "type": "object", "properties": {}, "additionalProperties": false }
}
```

Returns:

```json
{
  "authenticated_as": "octocat-bot",
  "token_scopes": ["repo", "issues:write"],
  "rate_limit": { "core": { "remaining": 4980, "resets_at": "2026-07-11T14:05:00Z" },
                  "search": { "remaining": 28, "resets_at": "2026-07-11T14:01:00Z" } },
  "max_per_page": 100,
  "search_excludes_pull_requests": true,
  "supported_lock_reasons": ["off-topic", "too heated", "resolved", "spam"]
}
```

Surfacing the **separate, much lower search rate limit** here is what steers agents to prefer `issues_list` over `issues_search`.

### 5.2 Resources (MCP `resources/list` + templated reads)

| URI | Purpose |
|---|---|
| `github-issues://schema/issue` | Canonical field reference for the issue object the tools return. |
| `github-issues://{owner}/{repo}/labels` | The repo's defined labels (name, color, description) — so the agent picks real labels for `labels_add` instead of inventing them. |
| `github-issues://{owner}/{repo}/assignable-users` | Logins that can actually be assigned — prevents the 422 in failure mode C. |
| `github-issues://{owner}/{repo}/milestones` | Milestone numbers ↔ titles, so the agent can pass the numeric `milestone` field. |
| `github-issues://help/search-qualifiers` | Cheat-sheet of supported search qualifiers with examples. |

Resource templates are declared via `resources/templates/list` so a host can offer `{owner}/{repo}` completion.

### 5.3 Prompts (MCP `prompts/list`) — optional but high-value

Pre-built workflows an agent (or user) can invoke:

- `triage_issue` — given an issue number, fetch it, suggest labels from the repo's real label set, and draft a comment.
- `find_duplicates` — search for issues similar to a title/body before `issue_create`.

These encode the *correct tool sequence* (read labels resource → `issue_get` → `labels_add`) so the agent doesn't have to rediscover it.

---

### Summary of the deliverable

- **11 tools** named by intent, each with a strict JSON `inputSchema` (`additionalProperties: false`, enums, bounds) and MCP safety annotations; labels handled with explicit add/remove tools plus a loud "replace" warning on `issue_update`.
- **Uniform typed error envelope** with stable `code`, `retryable`, `retry_after_seconds`, and actionable `remediation`, worked through four failure modes (404 not-found, 429 rate-limit, 422 validation, 403 permission).
- **Discovery surface**: a `server_capabilities` tool (limits + the search-vs-core rate distinction), five resources (issue schema, repo labels, assignable users, milestones, search-qualifier help) exposing the reference data agents otherwise hallucinate, and two workflow prompts encoding correct tool sequences.
