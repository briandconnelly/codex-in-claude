# Scenario 1 (Design) — with-skill run

- **Date:** 2026-07-11
- **Tree:** `d586ce3` (main, post review-batch #53)
- **Mode:** with-skill (fresh general-purpose subagent; read `SKILL.md` + all seven `references/` files, explicitly skipped `tests/`; 8 tool-uses)
- **Score:** 9/9

## Exact prompt given

Same GitHub Issues design prompt as the baseline (see `2026-07-11-scenario1-baseline.md`), preceded by an instruction to read `agent-friendly-mcp/SKILL.md` and every file under `references/` as authoritative guidance, and **not** to read anything under `tests/`.

## Per-assertion scoring

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| A1 | Tools task-completing, 11 endpoints → ~4–7 tools | **PASS** | Collapsed to **7** tools with a per-merge justification table: list **+** search → `github_search_issues`; get **+** list-comments → `github_get_issue`; add **+** remove labels → `github_manage_labels`; lock **+** unlock → `github_set_issue_lock`. Explicitly cites the near-name-collision anti-pattern (ex§10). |
| A2 | Names snake_case, service-prefixed, verb+noun | **PASS** | `github_search_issues`, `github_get_issue`, `github_create_issue`, `github_update_issue`, `github_add_comment`, `github_manage_labels`, `github_set_issue_lock` — `github_` prefix, verb+noun, snake_case. |
| A3 | `additionalProperties:false`, disambiguated names, omission semantics, `default` discipline | **PASS** | `additionalProperties:false` on every schema; `issue_number` "…the #N shown in the UI, not the global node id"; every optional field states "Omitted means …". |
| A4 | ≥2 error payloads: symbolic codes, field detail, repair naming a real surface | **PASS** | Five worked payloads (`not_found`, `search_rate_limited`, `conflict`, `insufficient_scope`, resource JSON-RPC); each `repair` names a real tool + literal arguments. |
| A5 | Errors as tool result errors (`isError:true`), not JSON-RPC | **PASS** | "tool failures → `structuredContent` with `isError: true`; resource … failures → JSON-RPC `error.data`. One envelope, two carriers." Worked in modes A–E. |
| A6 | Capability summary stating what the server does NOT do | **PASS** | Explicit `does_not`: "Touch pull requests, Actions, Projects…", "Delete issues or comments", "Manage repositories, teams…", "real-time webhook/event streams". |
| A7 | Pagination cursor-based + provenance-correct + concise default with `detail` toggle | **PASS** | `has_more`/`next_cursor` explicitly "a **house pagination convention** … not native `nextCursor` (reserved for `tools/list`)"; `detail: "summary"|"full"`; separate `truncated`/`truncation_hint`; opaque cursors with `cursor_expired`. |
| A8 | Annotations present and honest (create not read-only/idempotent) | **PASS** | `github_create_issue` → `idempotentHint:false`; `github_update_issue`/`manage_labels`/`set_issue_lock` → `idempotentHint:true` with a stated "annotation family consistency" rationale. |
| A9 | Tool defs publish `outputSchema`; success = conforming `structuredContent`; `content` fallback | **PASS** | `github_search_issues` carries a full `outputSchema` (issues[], has_more, next_cursor, estimated_total, truncated); prose ties success `structuredContent` to it and keeps text `content` as fallback. *(Note: not all 7 tools show an `outputSchema` block — the flagship read tool does, and the pairing rule is stated; a stricter future scenario could require it on every tool.)* |

**Result: 9/9.**
Every gap the baseline exhibited (A1, A2, A6, A7, A9) is closed, and the skill-specific disciplines (native-vs-convention `_meta` namespacing, one-envelope-two-carriers, version-precondition handles, forward-compat N/A justifications for §4/§5/§7) appear throughout.

## Full final output

Verbatim final message returned by the with-skill subagent (the 8 tool calls were the skill-file reads it was instructed to perform; no other tool noise):

I've read the SKILL and all seven reference files (and skipped `tests/` as instructed).
Here is the complete design.

---

# `github-issues` MCP server — agent-facing contract

Design produced against the skill's contract-checklist, walked §1–§9.
Convention (house) extensions are labeled throughout and ride under namespaced `_meta` keys (`com.github-issues/…`) or documented convention envelopes; everything else is native MCP.

---

## §1 Server-level

- **Name:** `github-issues` (distinctive, no version number, issue-scoped — not `github`, `api`, or `data`).
- **Transport:** streamable HTTP (shared/remote service wrapping api.github.com).
  Declared in the summary so agents know results reference an external system and that returned handles are not filesystem paths.
- **Tool prefix / verbs:** `github_` prefix; `search`/`get`/`create`/`update`/`add`/`manage`/`set` reused consistently. `snake_case`, import-friendly for code-execution clients.
- **Auth:** bearer token (GitHub PAT or App installation token), audience-bound to the canonical server URI.
  Three distinct credential failure codes: `missing_credential`, `invalid_credential`, `insufficient_scope` (with `details.required_scopes`).
- **Scopes:** least-privilege `repo` (or `public_repo` for public-only) and `issues:write`.
  Read-only tools work with read scope; mutations surface `insufficient_scope` separately from missing/invalid.
- **State handles:** pagination `cursor` and the update `version` token are **opaque, integrity-protected** stateless tokens (§3 mode 2), 1-hour lifetime, `cursor_expired` on lapse; authorization re-checked on every continuation.
- **Error carriers (declared up front):** tool failures → `structuredContent` with `isError: true`; resource (`resources/read`) failures → JSON-RPC `error.data`.
  One envelope, two carriers.

### Negotiated capabilities (convention block, native features)

```json
{
  "negotiated_capabilities": {
    "server": ["server.capabilities.completions", "server.capabilities.resources.listChanged"],
    "client": ["client.capabilities.elicitation.url"],
    "fallbacks": {
      "no_completions": "github_search_issues returns candidate repos in invalid-field errors where safe.",
      "no_elicitation": "Missing/expired credential returns insufficient_scope / invalid_credential with the token-setup surface named in the message."
    }
  }
}
```

Roots: **N/A** — no local workspace content; repo scope travels as ordinary `owner`/`repo` tool arguments (this is also the forward-compat hedge).
Tasks: **N/A** — every operation completes within one request/response turn (§7 below).

---

## §2 Discovery surface

### Capability summary (convention surface — expose via `github://capabilities` resource + `instructions`)

```json
{
  "server": {
    "name": "github-issues",
    "version": "1.0.0",
    "fingerprint": "github-issues@1.0.0+c3f1a20",
    "transport": "streamable-http",
    "summary": "Search, read, create, update, comment on, label, and lock GitHub issues across repositories you can access."
  },
  "does": [
    "Find issues by GitHub search syntax or by repo-scoped filters (state, labels, assignee, milestone).",
    "Read one issue with its comment thread (paginated).",
    "Create issues; update title/body/state/assignees/milestone; add comments.",
    "Add or remove labels additively; lock or unlock the conversation."
  ],
  "does_not": [
    "Touch pull requests, Actions, Projects, Discussions, or repo settings.",
    "Delete issues or comments (GitHub has no issue-delete API).",
    "Manage repositories, teams, or org membership.",
    "Provide real-time webhook/event streams (request/response only)."
  ],
  "error_carriers": { "tool_errors": "structuredContent", "non_tool_rpc_errors": "error.data" },
  "prerequisites": {
    "required_scopes": ["repo (or public_repo)", "issues:write for mutations"],
    "auth": { "mode": "bearer", "canonical_server_uri": "https://gh-issues-mcp.example.com/mcp",
              "resource_indicator": "https://gh-issues-mcp.example.com/mcp" },
    "failure_codes": {
      "missing_credential": "no GitHub token configured",
      "invalid_credential": "GitHub rejected the token",
      "insufficient_scope": "token lacks a required scope; see details.required_scopes"
    },
    "rate_limits": "GitHub REST 5000 req/hr, Search API 30 req/min; both surfaced via retry_after_ms + rate_limit_remaining on rate_limited errors."
  }
}
```

### Discovery primitives
- **Capability summary** at resource `github://capabilities` (tool fallback: any tool's inline `_meta` error catalog references it).
- **Resource templates** (`resources/templates/list`) for the read fallback on clients that don't expose resources well:
  - `github://repos/{owner}/{repo}/issues/{issue_number}`
  - `github://repos/{owner}/{repo}/issues/{issue_number}/comments{?cursor}`
- **Completion** (`completion/complete`) on `{owner}` and `{repo}` template variables — these are hard to guess; gated on `server.capabilities.completions`.
  Does **not** cover tool arguments; those repair through normal tool errors.
- **Fingerprint** (`github-issues@1.0.0+c3f1a20`) travels in the summary and every error payload so a caching client can short-circuit re-discovery.
- 7 compact tool definitions — this is the universal baseline; no `search_tools` layer, since 7 task-completing tools don't warrant progressive disclosure.

---

## §3 Tools — 7 task-completing tools (from 11 endpoints)

Granularity decisions (§3 / design-workflow Step 3):

| Endpoint(s) | Tool | Reasoning |
|---|---|---|
| list issues **+** search issues | `github_search_issues` | Both serve one task "find issues"; collapsing avoids the `email_send`/`send_invite` near-name collision (ex§10). Server routes to list vs search endpoint internally. |
| get issue **+** list comments | `github_get_issue` | "Read this issue" is one task; comments returned inline, paginated. |
| create issue | `github_create_issue` | Task-completing: title+body+labels+assignees+milestone in one call. |
| update issue (incl. close/reopen) | `github_update_issue` | Close/reopen is just `state`; no separate tool. Partial-update three-way semantics + version precondition. |
| add comment | `github_add_comment` | Create-style; idempotency key. |
| add labels **+** remove labels | `github_manage_labels` | Additive/subtractive semantics (distinct from update's replace) → one dedicated tool. |
| lock **+** unlock | `github_set_issue_lock` | Boolean discriminator; trivially the same operation. |

### 1. `github_search_issues` (read-only)

```json
{
  "name": "github_search_issues",
  "description": "Find issues either by GitHub search-query syntax (cross-repo) or by repo-scoped filters.\n\nWhen to use: locating issues before reading or updating one. Provide `query` for full-text/qualifier search across repos, OR `owner`+`repo` (with optional filters) to list within one repo. Supplying `owner`+`repo` AND `query` scopes the query to that repo.\n\nUsage notes:\n- `state` omitted defaults to `open`.\n- Results default to `detail:\"summary\"`; pass `detail:\"full\"` for bodies and raw ids.\n- Paginate with the returned `next_cursor` while `has_more` is true.\n\nExample: {\"owner\":\"acme\",\"repo\":\"web\",\"state\":\"open\",\"labels\":[\"bug\"]}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "query": {"type": "string", "minLength": 1, "maxLength": 256,
        "description": "GitHub issues search syntax, e.g. 'is:open label:bug author:alice'. Omit to list a single repo by filters instead."},
      "owner": {"type": "string", "pattern": "^[A-Za-z0-9-]{1,39}$",
        "description": "Repo owner (user or org). Required with `repo` for repo-scoped listing; omit for pure cross-repo `query`."},
      "repo": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,100}$",
        "description": "Repository name only (not owner/name). Requires `owner`."},
      "state": {"type": "string", "enum": ["open", "closed", "all"],
        "description": "Omitted means 'open'."},
      "labels": {"type": "array", "items": {"type": "string"}, "maxItems": 20,
        "description": "AND-match label names. Omitted means no label filter."},
      "assignee": {"type": "string", "description": "Login, '*' (any), or 'none'. Omitted means no assignee filter."},
      "creator": {"type": "string", "description": "Filter by issue author login. Omitted means any."},
      "milestone": {"type": "string", "description": "Milestone title, '*', or 'none'. Omitted means any."},
      "since": {"type": "string", "format": "date-time",
        "description": "Only issues updated at/after this RFC3339 UTC instant. Omitted means no lower bound."},
      "sort": {"type": "string", "enum": ["created", "updated", "comments"], "description": "Omitted means 'created'."},
      "order": {"type": "string", "enum": ["asc", "desc"], "description": "Omitted means 'desc'."},
      "detail": {"type": "string", "enum": ["summary", "full"], "description": "Field density. Omitted means 'summary'."},
      "cursor": {"type": "string", "description": "Opaque pagination token from a prior response's `next_cursor`. Omit for the first page."},
      "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Page size. Omitted means 30."}
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["issues", "has_more"],
    "properties": {
      "issues": {"type": "array", "items": {"type": "object",
        "required": ["number", "title", "state", "repo_full_name"],
        "properties": {
          "number": {"type": "integer"}, "title": {"type": "string"},
          "state": {"type": "string", "enum": ["open", "closed"]},
          "repo_full_name": {"type": "string"},
          "labels": {"type": "array", "items": {"type": "string"}},
          "comment_count": {"type": "integer"}, "updated_at": {"type": "string", "format": "date-time"}
        }, "additionalProperties": true}},
      "has_more": {"type": "boolean"},
      "next_cursor": {"type": "string"},
      "estimated_total": {"type": "integer"},
      "truncated": {"type": "boolean"},
      "truncation_hint": {"type": "string"}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": true, "openWorldHint": true},
  "_meta": {
    "com.github-issues/errors": [
      {"code": "missing_search_scope", "temporary": false, "description": "Neither `query` nor `owner`+`repo` supplied; provide one."},
      {"code": "search_rate_limited", "temporary": true, "description": "Search API 30/min cap hit; honor retry_after_ms."},
      {"code": "cursor_expired", "temporary": false, "description": "Pagination cursor lapsed; restart from the first page."}
    ]
  }
}
```

`has_more`/`next_cursor`/`estimated_total`/`truncated`/`truncation_hint` are a **house pagination convention** (§8), in `structuredContent` under `outputSchema`, not native.
Cross-parameter rule "`query` XOR `owner`+`repo`" is enforced server-side and reported via `details.fields` (§6).

### 2. `github_get_issue` (read-only)

```json
{
  "name": "github_get_issue",
  "description": "Read one issue, optionally with its comment thread.\n\nWhen to use: to inspect an issue before updating, commenting, or labeling it. Returns the issue plus (when `include_comments` is true) a paginated page of comments. Also returns an opaque `version` token — pass it as `expected_version` to github_update_issue for safe concurrent edits.\n\nUsage notes:\n- `include_comments` omitted means false (issue only — cheaper).\n- Page further comments by re-calling with `comment_cursor` from the prior `comments_next_cursor`.\n\nExample: {\"owner\":\"acme\",\"repo\":\"web\",\"issue_number\":412,\"include_comments\":true}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo", "issue_number"],
    "properties": {
      "owner": {"type": "string", "pattern": "^[A-Za-z0-9-]{1,39}$"},
      "repo": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,100}$", "description": "Repository name only."},
      "issue_number": {"type": "integer", "minimum": 1, "description": "Per-repo issue number (the #N shown in the UI), not the global node id."},
      "include_comments": {"type": "boolean", "description": "Omitted means false."},
      "comment_cursor": {"type": "string", "description": "Opaque token from a prior `comments_next_cursor`. Omit for the first comment page."},
      "detail": {"type": "string", "enum": ["summary", "full"], "description": "Field density. Omitted means 'summary'."}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": true, "openWorldHint": true},
  "_meta": {
    "com.github-issues/errors": [
      {"code": "not_found", "temporary": false, "description": "No issue with that number in owner/repo (or no read access)."}
    ]
  }
}
```

Success `structuredContent` carries the issue plus `version` (opaque), and when `include_comments`: `comments`, `comments_has_more`, `comments_next_cursor` (house convention).

### 3. `github_create_issue` (mutating, non-idempotent)

```json
{
  "name": "github_create_issue",
  "description": "Create a new issue in a repository, optionally with labels, assignees, and a milestone.\n\nWhen to use: filing a new issue. Not for commenting on an existing one (use github_add_comment).\n\nUsage notes:\n- Re-sending with the same `idempotency_key` within 10 minutes returns the original issue instead of creating a duplicate. Omit it and each call files a new issue.\n- On a timeout, do NOT blind-retry without an `idempotency_key`; search first or reuse the key.\n\nExample: {\"owner\":\"acme\",\"repo\":\"web\",\"title\":\"Login 500\",\"body\":\"Steps...\",\"labels\":[\"bug\"]}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo", "title"],
    "properties": {
      "owner": {"type": "string", "pattern": "^[A-Za-z0-9-]{1,39}$"},
      "repo": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,100}$"},
      "title": {"type": "string", "minLength": 1, "maxLength": 256},
      "body": {"type": "string", "maxLength": 65536, "description": "Markdown body. Omitted means empty body."},
      "labels": {"type": "array", "items": {"type": "string"}, "maxItems": 100, "description": "Label names to apply. Omitted means none."},
      "assignees": {"type": "array", "items": {"type": "string", "pattern": "^[A-Za-z0-9-]{1,39}$"}, "maxItems": 10, "description": "Assignee logins. Omitted means unassigned."},
      "milestone": {"type": "integer", "minimum": 1, "description": "Milestone number. Omitted means none."},
      "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 128, "description": "Optional dedup key; re-sends within 10 min return the original issue. Omitted means no dedup."}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": true},
  "_meta": {
    "com.github-issues/errors": [
      {"code": "invalid_field", "temporary": false, "description": "A label/assignee/milestone does not exist in the repo; see details.field."},
      {"code": "insufficient_scope", "temporary": false, "description": "Token lacks issues:write; see details.required_scopes."}
    ]
  }
}
```

`idempotentHint: false` because a keyless re-send creates a duplicate — which is exactly why `idempotency_key` exists (§3 mutation-safety).

### 4. `github_update_issue` (mutating, idempotent, version-guarded)

```json
{
  "name": "github_update_issue",
  "description": "Update fields on an existing issue, including closing or reopening it.\n\nWhen to use: change title, body, state, assignees, or milestone. Closing = set state:\"closed\" (optionally with state_reason). Reopening = state:\"open\". For labels use github_manage_labels; for a comment use github_add_comment.\n\nPartial-update semantics (per field): OMIT a field to leave it unchanged; send null to CLEAR it (body, milestone, assignees); an empty array clears assignees. `title` cannot be cleared.\n\nConcurrency: pass `expected_version` (the `version` from github_get_issue). If the issue changed since, the call fails with `conflict` — re-read to get a fresh version, then reapply.\n\nExample: {\"owner\":\"acme\",\"repo\":\"web\",\"issue_number\":412,\"state\":\"closed\",\"state_reason\":\"completed\"}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo", "issue_number"],
    "properties": {
      "owner": {"type": "string", "pattern": "^[A-Za-z0-9-]{1,39}$"},
      "repo": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,100}$"},
      "issue_number": {"type": "integer", "minimum": 1},
      "title": {"type": "string", "minLength": 1, "maxLength": 256, "description": "Omit to leave unchanged. Cannot be cleared."},
      "body": {"type": ["string", "null"], "maxLength": 65536, "description": "Omit=unchanged; null=clear to empty; string=replace."},
      "state": {"type": "string", "enum": ["open", "closed"], "description": "Omit to leave unchanged."},
      "state_reason": {"type": ["string", "null"], "enum": ["completed", "not_planned", "reopened", null], "description": "Only meaningful with `state`. Omit to leave unchanged."},
      "assignees": {"type": ["array", "null"], "items": {"type": "string", "pattern": "^[A-Za-z0-9-]{1,39}$"}, "maxItems": 10, "description": "Omit=unchanged; [] or null=clear all; array=replace the full set."},
      "milestone": {"type": ["integer", "null"], "minimum": 1, "description": "Omit=unchanged; null=clear."},
      "expected_version": {"type": "string", "description": "Opaque token from github_get_issue for optimistic locking. Omitted means no precondition (last-write-wins)."}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": true, "openWorldHint": true},
  "_meta": {
    "com.github-issues/errors": [
      {"code": "not_found", "temporary": false, "description": "Issue does not exist or no write access."},
      {"code": "conflict", "temporary": false, "description": "expected_version stale; re-read then reapply."},
      {"code": "invalid_field", "temporary": false, "description": "Bad assignee/milestone/state_reason; see details.field."}
    ]
  }
}
```

`idempotentHint: true` — PATCH-to-a-target-state is idempotent for identical args (closing an already-closed issue is a no-op); `destructiveHint: false` because close is reversible via reopen.
Version precondition is a **content-derived opaque token** (§3) since GitHub issues lack a simple version counter — server computes it over a canonicalized `(updated_at, etag)`.

### 5. `github_add_comment` (mutating, non-idempotent)

```json
{
  "name": "github_add_comment",
  "description": "Post a comment to an existing issue.\n\nWhen to use: adding a reply/note to an issue. Not for editing the issue body (use github_update_issue).\n\nUsage notes:\n- Re-sending with the same `idempotency_key` within 10 minutes returns the original comment; omit it and each call posts a new comment.\n\nExample: {\"owner\":\"acme\",\"repo\":\"web\",\"issue_number\":412,\"body\":\"Reproduced on staging.\"}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo", "issue_number", "body"],
    "properties": {
      "owner": {"type": "string", "pattern": "^[A-Za-z0-9-]{1,39}$"},
      "repo": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,100}$"},
      "issue_number": {"type": "integer", "minimum": 1},
      "body": {"type": "string", "minLength": 1, "maxLength": 65536, "description": "Markdown comment body."},
      "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 128, "description": "Optional dedup key; re-sends within 10 min return the original comment. Omitted means no dedup."}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": true},
  "_meta": {
    "com.github-issues/errors": [
      {"code": "not_found", "temporary": false, "description": "Issue not found or no write access."},
      {"code": "locked", "temporary": false, "description": "Conversation is locked; unlock via github_set_issue_lock first."}
    ]
  }
}
```

### 6. `github_manage_labels` (mutating, idempotent — additive/subtractive)

```json
{
  "name": "github_manage_labels",
  "description": "Add and/or remove labels on an issue without replacing the whole set.\n\nWhen to use: attach or detach specific labels. This is additive/subtractive — labels you don't name are untouched (unlike github_update_issue, which has no label field). To replace the entire label set, remove the unwanted ones and add the wanted ones in one call.\n\nSemantics: `add` labels already present are a no-op; `remove` labels already absent are a no-op — so retries are safe. At least one of `add`/`remove` is required.\n\nExample: {\"owner\":\"acme\",\"repo\":\"web\",\"issue_number\":412,\"add\":[\"bug\"],\"remove\":[\"needs-triage\"]}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo", "issue_number"],
    "properties": {
      "owner": {"type": "string", "pattern": "^[A-Za-z0-9-]{1,39}$"},
      "repo": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,100}$"},
      "issue_number": {"type": "integer", "minimum": 1},
      "add": {"type": "array", "items": {"type": "string"}, "maxItems": 100, "description": "Label names to attach. Omitted means add nothing."},
      "remove": {"type": "array", "items": {"type": "string"}, "maxItems": 100, "description": "Label names to detach. Omitted means remove nothing."}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": true, "openWorldHint": true},
  "_meta": {
    "com.github-issues/errors": [
      {"code": "not_found", "temporary": false, "description": "Issue not found or no write access."},
      {"code": "no_label_op", "temporary": false, "description": "Neither `add` nor `remove` supplied."},
      {"code": "invalid_field", "temporary": false, "description": "A label name in `add` does not exist in the repo; see details.field."}
    ]
  }
}
```

`idempotentHint: true` — add-existing / remove-absent are no-ops, so retry after a timeout is safe. "At least one of add/remove" is a cross-parameter constraint → `details.fields`.

### 7. `github_set_issue_lock` (mutating, idempotent)

```json
{
  "name": "github_set_issue_lock",
  "description": "Lock or unlock an issue's conversation.\n\nWhen to use: moderate a heated or resolved thread. Set locked:true to lock (optionally with lock_reason), locked:false to unlock.\n\nSemantics: locking an already-locked issue (or unlocking an unlocked one) is a no-op, so retries are safe. `lock_reason` is only used when locked:true.\n\nExample: {\"owner\":\"acme\",\"repo\":\"web\",\"issue_number\":412,\"locked\":true,\"lock_reason\":\"resolved\"}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo", "issue_number", "locked"],
    "properties": {
      "owner": {"type": "string", "pattern": "^[A-Za-z0-9-]{1,39}$"},
      "repo": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,100}$"},
      "issue_number": {"type": "integer", "minimum": 1},
      "locked": {"type": "boolean", "description": "true=lock, false=unlock."},
      "lock_reason": {"type": "string", "enum": ["off-topic", "too heated", "resolved", "spam"], "description": "Only applied when locked:true. Omitted means lock with no stated reason."}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": true, "openWorldHint": true},
  "_meta": {
    "com.github-issues/errors": [
      {"code": "not_found", "temporary": false, "description": "Issue not found or no write access."},
      {"code": "insufficient_scope", "temporary": false, "description": "Token lacks triage/write permission; see details.required_scopes."}
    ]
  }
}
```

**Annotation family consistency (§3):** all four mutators share `readOnlyHint:false, destructiveHint:false, openWorldHint:true`; `idempotentHint` differs deliberately — `true` for update/manage_labels/set_lock (state-convergent), `false` for create/add_comment (each call produces a new record).
The reason is stated in each description, per the sibling-consistency rule.

---

## §6 Failure recovery — worked payloads

One envelope, two carriers.
Codes are stable symbolic strings; agents branch on `code`, never on prose.
Repair hints name real callable surfaces with literally callable arguments.

### Failure mode A — `not_found` (tool-result error, `github_get_issue`)

```json
{
  "isError": true,
  "content": [
    {"type": "text", "text": "Issue #99999 not found in acme/web. Confirm the number with github_search_issues, or check the owner/repo."}
  ],
  "structuredContent": {
    "code": "not_found",
    "message": "No issue #99999 in acme/web (or no read access).",
    "details": {"field": "issue_number", "value": 99999, "reason": "Highest issue number in acme/web is 4127."},
    "temporary": false,
    "retry_after_ms": null,
    "repair": {
      "next_step": "search_then_read",
      "tool": "github_search_issues",
      "arguments": {"owner": "acme", "repo": "web", "state": "all"},
      "alternative": "Verify owner/repo spelling; the repo or issue may be private and out of the token's scope."
    },
    "request_id": "req_01JB7Q2M9ABCDEF",
    "fingerprint": "github-issues@1.0.0+c3f1a20"
  }
}
```

### Failure mode B — `rate_limited` (tool-result error, `github_search_issues`)

The GitHub-critical mode. `temporary:true`, a concrete `retry_after_ms`, and `rate_limit_remaining` let the agent wait rather than repair.
No `repair` object (the same unchanged call succeeds after the delay), per the retry-vs-repair invariants.

```json
{
  "isError": true,
  "content": [
    {"type": "text", "text": "GitHub Search API rate limit reached (0 of 30/min remaining). Retry in ~24s."}
  ],
  "structuredContent": {
    "code": "search_rate_limited",
    "message": "Search API rate limit exhausted for this window.",
    "temporary": true,
    "retry_after_ms": 24000,
    "rate_limit_remaining": 0,
    "request_id": "req_01JB7Q40REST9ZZ",
    "fingerprint": "github-issues@1.0.0+c3f1a20"
  }
}
```

### Failure mode C — `conflict` (tool-result error, `github_update_issue`, version precondition)

`temporary:false` (retrying the same stale call won't help) but a `repair` exists (a *different* corrective path — re-read then reapply), showing retryability and repairability are independent.

```json
{
  "isError": true,
  "content": [
    {"type": "text", "text": "Issue #412 changed since you read it. Re-read with github_get_issue to get a fresh version token, then re-apply your update."}
  ],
  "structuredContent": {
    "code": "conflict",
    "message": "expected_version does not match the current issue state.",
    "details": {"field": "expected_version", "reason": "Issue was updated at 2026-07-11T14:02:10Z after your read."},
    "temporary": false,
    "retry_after_ms": null,
    "repair": {
      "next_step": "reread_then_reapply",
      "tool": "github_get_issue",
      "arguments": {"owner": "acme", "repo": "web", "issue_number": 412},
      "alternative": "Re-call github_update_issue without expected_version to force last-write-wins (may clobber the concurrent change)."
    },
    "request_id": "req_01JB7Q55CONFLICT",
    "fingerprint": "github-issues@1.0.0+c3f1a20"
  }
}
```

### Failure mode D — `insufficient_scope` (tool-result error, `github_create_issue`)

Distinguished from missing/invalid credential; carries the code-specific `required_scopes` detail key alongside `reason`.

```json
{
  "isError": true,
  "content": [
    {"type": "text", "text": "Token cannot write issues in acme/web. Grant the 'issues:write' (or 'repo') scope and reconnect."}
  ],
  "structuredContent": {
    "code": "insufficient_scope",
    "message": "Configured token lacks a required scope.",
    "details": {"field": "owner", "reason": "Token is read-only for acme/web.", "required_scopes": ["repo"]},
    "temporary": false,
    "retry_after_ms": null,
    "request_id": "req_01JB7Q66SCOPE",
    "fingerprint": "github-issues@1.0.0+c3f1a20"
  }
}
```

### Failure mode E — resource read (JSON-RPC error carrier)

Reading `github://repos/acme/web/issues/412` after the issue was transferred/deleted.
Same envelope, only `code`→`machine_code` and `message`→`human_message` renamed to avoid shadowing native JSON-RPC keys.

```json
{
  "jsonrpc": "2.0",
  "id": "req_read_412",
  "error": {
    "code": -32002,
    "message": "Resource not found.",
    "data": {
      "machine_code": "not_found",
      "human_message": "Issue #412 in acme/web was deleted or transferred.",
      "details": {"field": "uri", "value": "github://repos/acme/web/issues/412", "reason": "Issue no longer resolvable."},
      "temporary": false,
      "retry_after_ms": null,
      "repair": {
        "next_step": "search_replacement",
        "tool": "github_search_issues",
        "arguments": {"owner": "acme", "repo": "web", "state": "all"},
        "alternative": "The issue may have been transferred to another repo; search by title there."
      },
      "resource_uri": "github://repos/acme/web/issues/412",
      "request_id": "req_read_412",
      "fingerprint": "github-issues@1.0.0+c3f1a20"
    }
  }
}
```

Branch on `machine_code`, not the numeric `-32002` (a likely migration point per the Spec Baseline).

---

## §7 Long-running operations — N/A (justified)

Every operation completes within one request/response turn (single REST call, or a bounded paginated fetch).
No progress, cancellation, or task augmentation needed. `execution.taskSupport` is omitted; `server.capabilities.tasks` not advertised.
This is the forward-compat-safe default. (If a future bulk-export tool were added, it would follow §7 with a native task lifecycle plus a labeled fallback.)

## §8 Token efficiency

- `detail: "summary" | "full"` on the two read tools; concise fields are a strict subset of full (never renamed between modes).
- **House pagination convention** (`has_more`/`next_cursor`/`estimated_total`) lives in `structuredContent` under `outputSchema`, labeled as convention — not native `nextCursor` (which is reserved for `tools/list` etc.).
- Explicit `truncated` + `truncation_hint` on search (GitHub Search caps at 1000 results) — distinct from `has_more`.
- Cursors are opaque state handles with declared 1-hour lifetime and a `cursor_expired` restart error.
- Wire values locale-independent: RFC3339 UTC timestamps, integer issue/milestone numbers.

## §9 Versioning

- **Fingerprint** `github-issues@1.0.0+c3f1a20` in the summary and every error payload; `covers` = tools, resource templates, error codes, summary.
  Justified: a remote HTTP service whose clients may cache/pin the surface.
- Native `notifications/tools/list_changed` + `notifications/resources/list_changed` advertised; deterministic tool ordering.
- Error codes are part of the versioned surface — adding a code is additive; renaming/re-meaning one bumps the fingerprint.
  Tool renames use the remove-plus-add alias-window discipline.

---

## Done-criteria coverage

| § | Answer |
|---|---|
| §1 Server-level | Name, HTTP transport, bearer auth with 3 credential failure modes, opaque cursor/version handles, error carriers declared. |
| §2 Discovery | Capability summary (resource + instructions), 7 compact defs, resource templates + owner/repo completion, fingerprint. |
| §3 Tools | 7 task-completing tools from 11 endpoints; strict closed schemas; honest, family-consistent annotations; idempotency keys; version precondition; additive labels. |
| §4 Resources | Read-fallback templates for issues/comments; JSON-RPC error carrier. (Primary read path is the tool `github_get_issue`; templates serve resource-capable clients.) |
| §5 Prompts | None — no prompt is load-bearing for correctness. N/A. |
| §6 Failure recovery | 5 worked payloads (4 tool-result + 1 JSON-RPC); one envelope, two carriers; retry-vs-repair invariants honored. |
| §7 Long-running | N/A — all ops single-turn; justified. |
| §8 Token efficiency | detail toggle, house pagination convention, explicit truncation, opaque cursors. |
| §9 Versioning | Fingerprint + native list-changed + deterministic ordering + versioned error codes. |

The headline design choice: collapse GitHub's 11 issue endpoints into **7 task-completing tools** — the list/search merge and the get-issue/list-comments merge remove the two near-name collisions that would otherwise cost agents wrong first calls, while `manage_labels` and `set_issue_lock` consolidate the additive-label and lock/unlock endpoint pairs behind their natural task and boolean discriminator.
