# Scenario 1 — focused probe: error-path / `outputSchema` contract-correctness

Date: 2026-07-11
Tree: `a3cd37f`
Run: with-skill, focused error-path probe (Scenario 1 "Design", scored assertion only)
Artifact: inlined verbatim below (produced in the run harness; not a repo file).

## Assertion scored

Scenario 1, **(Scored.)** bullet: "The error path is contract-correct and distinct from the success shape" —
`isError: true` carries the §6 error envelope in `structuredContent` with a `content` fallback;
`outputSchema` scoped to success only, stated as an interpretation;
either a success-only `outputSchema` with the error envelope validated separately OR a success∪error union;
text-only error only as a disclosed degraded mode.

## Verdict: PASS

Evidence, from the artifact's `### ERROR — wire shape` section (all three worked examples: `issue_not_found`, `repo_not_found`, `rate_limited`):

1. **`isError: true` present on every error result**, e.g.:
   ```json
   "result": {
     "isError": true,
     "content": [...],
     "structuredContent": { "code": "issue_not_found", ... }
   }
   ```

2. **Error envelope lives in `structuredContent`, distinct from the success shape**, with a `content` textual fallback kept alongside it in every example (`content[0].text` present in all three). The artifact states this explicitly: "The error envelope travels in `structuredContent`, not `content`. `content[0].text` is a redundant human-readable mirror for weak clients, never the parsed source of truth."

3. **`outputSchema` scoped to success only, and explicitly flagged as an interpretation, not asserted as settled spec**: "`outputSchema` governs success results only (the §3 deliberate reading of the unsettled point)." And again: "None of the three `structuredContent` bodies above would validate against `get_issue`'s success `outputSchema`... that's correct and expected under this skill's scoping position, not a bug; a validating client must branch on `isError` before choosing which schema (success `outputSchema` vs. the documented error envelope) to check against." This matches the accepted "success-only `outputSchema` with the error envelope validated separately" branch (not a union, and the union alternative is not claimed either — no conflict).

4. **Text-only-error framed correctly as a degraded mode, not the default**: "This server never needs the degraded `content[0].text`-only carrier. Its framework places native `structuredContent` on `isError: true` results without limitation, so `error_carriers` in the capability summary names only `structuredContent`/`error.data` — no disclosed degraded mode." The artifact never uses text-only as the primary error carrier in any of the three examples; it correctly treats the text-only path as an optional degraded mode this server doesn't need, and would disclose if it did.

All four sub-requirements of the assertion are satisfied with direct textual and wire-shape evidence.

## Baseline expectation (not a scored run)

Per the Scenario 1 "Expected baseline failures" list (prose-only error descriptions, no output schema), the documented baseline expectation for this specific assertion is that a baseline (no-skill) run's error results either reuse the success `structuredContent` shape (no distinct error envelope) or omit the structured error envelope entirely, falling back to prose-only / `content`-only error reporting with no `outputSchema` scoping discussion at all. No fresh baseline run was executed for this focused probe — this is the expected baseline failure mode, not a scored baseline result.

## Scored artifact (verbatim)

# `github-issues-mcp` — Agent-Facing Contract

Applies `agent-friendly-mcp` (`contract-checklist.md` §1–§9) to a server wrapping the GitHub Issues REST API.
Baseline spec: MCP 2025-11-25 (per `SKILL.md` Spec Baseline).
The 11 source endpoints (list issues, get issue, create issue, update issue, add comment, list comments, lock issue, unlock issue, add labels, remove labels, search issues) are collapsed into 9 task-completing tools; the collapses are justified in Step 1 below.

---

## Step 1 — Task inventory and granularity

| # | Agent task | Tool | Endpoints folded in | Granularity reasoning |
| --- | --- | --- | --- | --- |
| 1 | Browse/triage issues in one repo | `github_issues_list_issues` | list issues | 1:1 with the endpoint; already task-shaped. |
| 2 | Fetch one issue's full state | `github_issues_get_issue` | get issue | 1:1; the focus tool of this document. |
| 3 | Full-text / cross-repo search | `github_issues_search_issues` | search issues | 1:1; distinct query grammar from `list_issues`'s structured filters, so kept separate rather than merged (two genuinely different input shapes — merging would need a mode union, which §3 disfavors). |
| 4 | Open a new issue | `github_issues_create_issue` | create issue | 1:1. |
| 5 | Edit an existing issue's fields/state | `github_issues_update_issue` | update issue | 1:1; GitHub's single PATCH endpoint already covers title/body/state/assignees/milestone as one task. |
| 6 | Comment on an issue | `github_issues_add_comment` | add comment | 1:1. |
| 7 | Read an issue's discussion | `github_issues_list_comments` | list comments | 1:1. |
| 8 | Control whether an issue accepts new comments | `github_issues_set_lock` | lock issue, unlock issue | **Collapsed.** Lock and unlock are the same task ("set the conversation-lock state") expressed as two opposite-direction endpoints. A `locked: boolean` discriminator with per-mode field documentation (`lock_reason` required only when `locked: true`) reads as one task-completing tool rather than two thin mirrors, per the §3 discriminator-over-union guidance — this is a boolean discriminator, not a union, so it stays a simple flat schema. |
| 9 | Manage an issue's labels | `github_issues_update_labels` | add labels, remove labels | **Collapsed.** "Add labels" and "remove labels" are the same task ("change which labels are on this issue"); an agent asked to "swap the `bug` label for `needs-repro`" would otherwise need two calls with no atomicity story. One tool with `add`/`remove` array fields (§3 partial-success semantics apply — see Tool 9 below) serves the task directly. |

Net: 11 endpoints → 9 tools. Two collapses, both justified by "same task, opposite/combined direction," not by speculation — matching the §3 anti-pattern warning against endpoint-shaped tools while not force-collapsing tools whose input shapes genuinely differ (`list_issues` vs `search_issues`).

Prerequisites per task: a GitHub credential with `repo` (or `public_repo` for public-only) scope; `owner`/`repo` context on every call (no implicit "current repo" — see §1 below); no long-lived server-side session.

Optional MCP capabilities used: none of roots, elicitation, resource subscriptions, or tasks are required for correctness (see §7 and the Not-Applicable section). `server.capabilities.completions` is used opportunistically for label-name completion (see Discovery).

---

## Server capability summary (§1 / §2)

Exposed via a resource (`github-issues://capability-summary`) and mirrored in server `instructions` for clients that don't browse resources.

```json
{
  "server": {
    "name": "github-issues-mcp",
    "version": "1.0.0",
    "fingerprint": "github-issues-mcp@1.0.0+a1b2c3",
    "transport": "streamable-http",
    "summary": "Read and manage GitHub Issues (not pull requests, not repository administration) for repositories the configured credential can access."
  },
  "does": [
    "List, search, fetch, create, and update issues.",
    "Read and post issue comments.",
    "Lock/unlock issue conversations.",
    "Add/remove labels on an issue."
  ],
  "does_not": [
    "Pull requests, branches, commits, or repository settings — use a separate PR/repo-admin server.",
    "Create or delete labels/milestones at the repository level (only attach/detach existing labels to an issue).",
    "Delete issues (GitHub's API does not support this for non-admins) or delete comments.",
    "Cross-organization bulk operations; every call is scoped to one owner/repo."
  ],
  "error_carriers": {
    "tool_errors": "structuredContent",
    "non_tool_rpc_errors": "error.data"
  },
  "prerequisites": {
    "repo_scope": "Every tool requires explicit owner and repo arguments; there is no implicit \"current repository.\"",
    "required_scopes": ["repo (or public_repo for public-only access)"],
    "negotiated_capabilities": {
      "server": ["server.capabilities.completions"],
      "client": [],
      "fallbacks": {
        "no_completions": "label add/remove arguments fall back to plain string validation with an invalid_field repair naming github_issues_list_issues as the source of valid label names."
      }
    },
    "auth": {
      "mode": "HTTP bearer (GitHub OAuth or PAT)",
      "http_variant": {
        "canonical_server_uri": "https://github-issues-mcp.example.com/mcp",
        "resource_indicator": "https://github-issues-mcp.example.com/mcp"
      }
    },
    "failure_codes": {
      "missing_credential": "no bearer token presented",
      "invalid_credential": "GitHub rejected the token",
      "insufficient_scope": "token lacks repo scope for the target repository; see details.required_scopes"
    }
  }
}
```

Discovery mechanism choice: 9 compact tool definitions is well under any preloading client's budget, so no `search_tools`/`describe_tool` layer is added — per §2, progressive disclosure is a client-dependent optimization, not owed when the catalog is already small; adding it here would be extra tools and round trips with no disclosure benefit on a preloading client. Marked not-applicable with this one-line justification per Done Criteria.

---

## Tool list (§3)

All schemas use `"$schema": "https://json-schema.org/draft/2020-12/schema"` and `additionalProperties: false` (omitted below for brevity where unchanged). All tool names are `snake_case`, prefixed `github_issues_`, verb+noun.

### 1. `github_issues_list_issues`

```json
{
  "name": "github_issues_list_issues",
  "description": "List issues in one repository, filtered by state/labels/assignee. When to use: browsing or triaging a repo's issue backlog with structured filters. Not for keyword/full-text queries or cross-repo search — use github_issues_search_issues. Not for a single known issue — use github_issues_get_issue. Example: {\"owner\": \"octocat\", \"repo\": \"hello-world\", \"state\": \"open\"}.",
  "inputSchema": {
    "type": "object",
    "required": ["owner", "repo"],
    "properties": {
      "owner": {"type": "string", "description": "Repository owner login (user or organization)."},
      "repo": {"type": "string", "description": "Repository name, without the owner prefix."},
      "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open", "description": "Omitted means \"open\" (the server applies this default)."},
      "labels": {"type": "array", "items": {"type": "string"}, "maxItems": 20, "description": "Return only issues carrying every label listed. Omit for no label filter."},
      "assignee": {"type": "string", "description": "GitHub login. Omit for no assignee filter."},
      "sort": {"type": "string", "enum": ["created", "updated", "comments"], "default": "created"},
      "direction": {"type": "string", "enum": ["asc", "desc"], "default": "desc"},
      "since": {"type": "string", "format": "date-time", "description": "RFC3339 timestamp; only issues updated at or after this time. Omit for no lower bound."},
      "detail": {"type": "string", "enum": ["summary", "full"], "default": "summary"},
      "cursor": {"type": "string", "description": "Opaque pagination cursor from a prior response's next_cursor. Omit to start from the first page."}
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "type": "object",
    "required": ["items", "has_more"],
    "properties": {
      "items": {"type": "array", "items": {"type": "object"}},
      "has_more": {"type": "boolean"},
      "next_cursor": {"type": "string"},
      "estimated_total": {"type": "integer"},
      "truncated": {"type": "boolean"},
      "truncation_hint": {"type": "string"}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": true, "openWorldHint": true}
}
```

Pagination is a house convention in `structuredContent` (§8) — `list_issues` is a `tools/call` result, not a native list method, so `has_more`/`next_cursor` are correct here, distinct from native `nextCursor` on `tools/list` itself.

### 2. `github_issues_get_issue`

The focus tool — full contract given its own section below.

### 3. `github_issues_search_issues`

```json
{
  "name": "github_issues_search_issues",
  "description": "Full-text/qualifier search across issues using GitHub search syntax (e.g. \"is:open label:bug\"). When to use: keyword search or cross-repo queries. Not for simple state/label filtering within one known repo — use github_issues_list_issues, which is cheaper and has structured filters. Example: {\"query\": \"repo:octocat/hello-world is:open flash\"}.",
  "inputSchema": {
    "type": "object",
    "required": ["query"],
    "properties": {
      "query": {"type": "string", "minLength": 1, "maxLength": 512, "description": "GitHub issue-search query string. Scope to a repo with repo:owner/name inside the query; the owner/repo parameters below are a convenience that gets appended, not a replacement."},
      "owner": {"type": "string", "description": "Optional convenience scoping, appended to query as repo:owner/repo if repo is also set. Omit to search across every repo the credential can see."},
      "repo": {"type": "string"},
      "sort": {"type": "string", "enum": ["created", "updated", "comments", "relevance"], "default": "relevance"},
      "detail": {"type": "string", "enum": ["summary", "full"], "default": "summary"},
      "cursor": {"type": "string"}
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "type": "object",
    "required": ["items", "has_more"],
    "properties": {
      "items": {"type": "array", "items": {"type": "object"}},
      "has_more": {"type": "boolean"},
      "next_cursor": {"type": "string"},
      "estimated_total": {"type": "integer"}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": true, "openWorldHint": true}
}
```

### 4. `github_issues_create_issue`

```json
{
  "name": "github_issues_create_issue",
  "description": "Open a new issue in a repository. When to use: the agent has composed a title (and optionally body/labels/assignees) and a target repo. Example: {\"owner\": \"octocat\", \"repo\": \"hello-world\", \"title\": \"Camera flash is too bright\"}.",
  "inputSchema": {
    "type": "object",
    "required": ["owner", "repo", "title"],
    "properties": {
      "owner": {"type": "string"},
      "repo": {"type": "string"},
      "title": {"type": "string", "minLength": 1, "maxLength": 256},
      "body": {"type": "string", "maxLength": 65536, "description": "Markdown body. Omit for an empty body."},
      "labels": {"type": "array", "items": {"type": "string"}, "maxItems": 20, "description": "Label names to attach at creation. Omit for no labels; unknown label names fail with invalid_field naming the offending entry."},
      "assignees": {"type": "array", "items": {"type": "string"}, "maxItems": 10, "description": "GitHub logins to assign. Omit for no assignees."},
      "milestone": {"type": "integer", "description": "Milestone number. Omit for no milestone."},
      "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 128, "description": "Re-sends with the same key within 10 minutes are deduplicated to one created issue. Omit for no deduplication — each call creates a new issue."}
    },
    "additionalProperties": false
  },
  "outputSchema": {"$ref": "#/get_issue_output"},
  "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": true},
  "_meta": {
    "com.github-issues-mcp/errors": [
      {"code": "repo_not_found", "temporary": false, "description": "owner/repo does not exist or is not visible to the credential."},
      {"code": "validation_failed", "temporary": false, "description": "A field failed GitHub's validation (e.g. unknown label, unknown assignee, title too long)."},
      {"code": "insufficient_scope", "temporary": false, "description": "Credential lacks write access to Issues on this repository."},
      {"code": "rate_limited", "temporary": true, "description": "GitHub API rate limit hit; honor retry_after_ms."}
    ]
  }
}
```

`idempotentHint: false` is honest: re-posting creates a second issue, which is exactly why `idempotency_key` exists (§3 unsafe-mutation rule), mirroring `slack_send_message` in `examples.md` §1.
Output reuses the `get_issue` success shape (documented below) since "the newly created issue" is the same object shape as "an issue read back" — same schema, no duplication.

### 5. `github_issues_update_issue`

```json
{
  "name": "github_issues_update_issue",
  "description": "Partially update an existing issue: title, body, state, assignees, and/or milestone. Omitted fields are left unchanged. When to use: the agent has one or more fields to change on a known issue_number. Example: {\"owner\": \"octocat\", \"repo\": \"hello-world\", \"issue_number\": 42, \"state\": \"closed\"}.",
  "inputSchema": {
    "type": "object",
    "required": ["owner", "repo", "issue_number"],
    "properties": {
      "owner": {"type": "string"},
      "repo": {"type": "string"},
      "issue_number": {"type": "integer", "minimum": 1},
      "title": {"type": "string", "minLength": 1, "maxLength": 256, "description": "Absent: title unchanged."},
      "body": {"type": "string", "maxLength": 65536, "description": "Absent: body unchanged. Empty string clears the body."},
      "state": {"type": "string", "enum": ["open", "closed"], "description": "Absent: state unchanged."},
      "assignees": {"type": "array", "items": {"type": "string"}, "maxItems": 10, "description": "Absent: assignees unchanged. Empty array clears all assignees. Non-empty array replaces the full assignee set (not a merge)."},
      "milestone": {"type": ["integer", "null"], "description": "Absent: milestone unchanged. Explicit null clears the milestone. A number sets/replaces it."},
      "if_match": {"type": "string", "description": "Optional version token from a prior github_issues_get_issue call's `version` field. When present, the update is rejected with a conflict error if the issue changed since that read."}
    },
    "additionalProperties": false
  },
  "outputSchema": {"$ref": "#/get_issue_output"},
  "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": true, "openWorldHint": true},
  "_meta": {
    "com.github-issues-mcp/errors": [
      {"code": "issue_not_found", "temporary": false, "description": "No issue with this number in owner/repo."},
      {"code": "conflict", "temporary": false, "description": "if_match did not match the issue's current version; re-read with github_issues_get_issue and reapply."},
      {"code": "validation_failed", "temporary": false, "description": "A field failed validation (e.g. unknown assignee login)."}
    ]
  }
}
```

This is the three-way absent/null/empty-list omission discipline the checklist requires for partial-update tools (§3): `title`/`body`/`state` are absent-means-unchanged; `milestone` distinguishes absent (unchanged) from `null` (clear) from a number (set); `assignees` distinguishes absent (unchanged) from `[]` (clear) from a populated array (replace).
`if_match` + `conflict` implements the §3 read-modify-write version-precondition rule, using the content-derived `version` token `get_issue` returns (documented below) since GitHub issues have no native version counter exposed to this contract.

### 6. `github_issues_add_comment`

```json
{
  "name": "github_issues_add_comment",
  "description": "Post a new comment on an issue. When to use: the agent has composed comment text and a target issue_number. Example: {\"owner\": \"octocat\", \"repo\": \"hello-world\", \"issue_number\": 42, \"body\": \"Reproduced on 2.1.0.\"}.",
  "inputSchema": {
    "type": "object",
    "required": ["owner", "repo", "issue_number", "body"],
    "properties": {
      "owner": {"type": "string"},
      "repo": {"type": "string"},
      "issue_number": {"type": "integer", "minimum": 1},
      "body": {"type": "string", "minLength": 1, "maxLength": 65536},
      "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 128, "description": "Re-sends with the same key within 10 minutes are deduplicated to one posted comment. Omit for no deduplication."}
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "type": "object",
    "required": ["comment_id", "issue_number", "body", "author", "created_at", "html_url"],
    "properties": {
      "comment_id": {"type": "integer"},
      "issue_number": {"type": "integer"},
      "body": {"type": "string"},
      "author": {"type": "object", "properties": {"login": {"type": "string"}, "id": {"type": "integer"}}},
      "created_at": {"type": "string", "format": "date-time"},
      "html_url": {"type": "string", "format": "uri"}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": true},
  "_meta": {
    "com.github-issues-mcp/errors": [
      {"code": "issue_not_found", "temporary": false, "description": "No issue with this number in owner/repo."},
      {"code": "issue_locked", "temporary": false, "description": "Issue conversation is locked; non-collaborators cannot comment. See repair for the unlock path (collaborators only)."},
      {"code": "rate_limited", "temporary": true, "description": "GitHub API rate limit hit."}
    ]
  }
}
```

### 7. `github_issues_list_comments`

```json
{
  "name": "github_issues_list_comments",
  "description": "List comments on an issue, oldest first, paginated. When to use: the agent needs the discussion thread for a known issue_number. Example: {\"owner\": \"octocat\", \"repo\": \"hello-world\", \"issue_number\": 42}.",
  "inputSchema": {
    "type": "object",
    "required": ["owner", "repo", "issue_number"],
    "properties": {
      "owner": {"type": "string"},
      "repo": {"type": "string"},
      "issue_number": {"type": "integer", "minimum": 1},
      "since": {"type": "string", "format": "date-time", "description": "Only comments created at or after this time. Omit for no lower bound."},
      "cursor": {"type": "string"}
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "type": "object",
    "required": ["items", "has_more"],
    "properties": {
      "items": {"type": "array", "items": {"type": "object"}},
      "has_more": {"type": "boolean"},
      "next_cursor": {"type": "string"}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": true, "openWorldHint": true}
}
```

### 8. `github_issues_set_lock`

```json
{
  "name": "github_issues_set_lock",
  "description": "Lock or unlock an issue's conversation. Locking blocks new comments from non-collaborators; unlocking re-opens it. When to use: moderating a heated, off-topic, resolved, or spam thread. Example: {\"owner\": \"octocat\", \"repo\": \"hello-world\", \"issue_number\": 42, \"locked\": true, \"lock_reason\": \"resolved\"}.",
  "inputSchema": {
    "type": "object",
    "required": ["owner", "repo", "issue_number", "locked"],
    "properties": {
      "owner": {"type": "string"},
      "repo": {"type": "string"},
      "issue_number": {"type": "integer", "minimum": 1},
      "locked": {"type": "boolean", "description": "true to lock, false to unlock. Discriminates the two modes documented below."},
      "lock_reason": {"type": "string", "enum": ["off-topic", "too heated", "resolved", "spam"], "description": "Required when locked is true. Must be absent when locked is false — unlocking never carries a reason."}
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "type": "object",
    "required": ["issue_number", "locked"],
    "properties": {
      "issue_number": {"type": "integer"},
      "locked": {"type": "boolean"},
      "lock_reason": {"type": "string", "description": "Present only when locked is true."}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": true, "openWorldHint": true},
  "_meta": {
    "com.github-issues-mcp/errors": [
      {"code": "issue_not_found", "temporary": false, "description": "No issue with this number in owner/repo."},
      {"code": "validation_failed", "temporary": false, "description": "lock_reason missing while locked=true, or present while locked=false — see details.fields."}
    ]
  }
}
```

`idempotentHint: true`: re-locking an already-locked issue (with the same reason) converges to the same state, unlike `add_comment`'s always-appends semantics.

### 9. `github_issues_update_labels`

```json
{
  "name": "github_issues_update_labels",
  "description": "Add and/or remove labels on an issue in one call. When to use: changing an issue's label set, including swapping one label for another. At least one of add/remove must be non-empty. Labels must already exist on the repository — this tool attaches/detaches, it does not create repository-level labels. Example: {\"owner\": \"octocat\", \"repo\": \"hello-world\", \"issue_number\": 42, \"add\": [\"needs-repro\"], \"remove\": [\"bug\"]}.",
  "inputSchema": {
    "type": "object",
    "required": ["owner", "repo", "issue_number"],
    "properties": {
      "owner": {"type": "string"},
      "repo": {"type": "string"},
      "issue_number": {"type": "integer", "minimum": 1},
      "add": {"type": "array", "items": {"type": "string"}, "maxItems": 20, "description": "Label names to attach. Omit or empty for none."},
      "remove": {"type": "array", "items": {"type": "string"}, "maxItems": 20, "description": "Label names to detach. Omit or empty for none."}
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "type": "object",
    "required": ["issue_number", "results", "labels"],
    "properties": {
      "issue_number": {"type": "integer"},
      "results": {
        "type": "array",
        "description": "Per-item outcome; this operation is per-item, not atomic.",
        "items": {
          "type": "object",
          "required": ["label", "action", "status"],
          "properties": {
            "label": {"type": "string"},
            "action": {"type": "string", "enum": ["add", "remove"]},
            "status": {"type": "string", "enum": ["applied", "failed"]},
            "code": {"type": "string", "description": "Present only when status is failed; a stable symbolic code (e.g. label_not_found)."},
            "retryable": {"type": "boolean", "description": "Present only when status is failed."}
          }
        }
      },
      "labels": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "color": {"type": "string"}}}, "description": "Full resulting label set on the issue after this call."}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": true, "openWorldHint": true},
  "_meta": {
    "com.github-issues-mcp/errors": [
      {"code": "issue_not_found", "temporary": false, "description": "No issue with this number in owner/repo."},
      {"code": "validation_failed", "temporary": false, "description": "Neither add nor remove contained an entry — see details.fields."}
    ]
  }
}
```

Per-item `results` implements the §3 partial-success rule: a bad label name in `remove` doesn't fail the whole call, the item carries its own `code`/`retryable`, and a retry can resend just the failed items (`add`/`remove` accept the same label-name strings, so the failed subset is directly re-callable).
`validation_failed` (neither array populated) is the *whole-call* failure mode, distinct from a per-item `label_not_found` inside `results`.

---

## `github_issues_get_issue` — full output contract

This is the read tool the task calls out for special attention. Both the success and error wire shapes are given in full, per the §3 `outputSchema`-scoping rule and the §6 unified error envelope.

### Tool definition

```json
{
  "name": "github_issues_get_issue",
  "title": "Get issue",
  "description": "Fetch a single issue by number within one repository, including its current status, metadata, and body. When to use: the agent already has owner, repo, and issue_number — from github_issues_list_issues, github_issues_search_issues, or a reference the user gave directly. Not for browsing or filtering — use github_issues_list_issues. Not for keyword search — use github_issues_search_issues. Use detail: \"full\" only when the complete body text or reaction counts are needed; default \"summary\" is sufficient for status checks and triage. Example: {\"owner\": \"octocat\", \"repo\": \"hello-world\", \"issue_number\": 42}.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo", "issue_number"],
    "properties": {
      "owner": {
        "type": "string",
        "description": "Repository owner login (user or organization). Not the display name."
      },
      "repo": {
        "type": "string",
        "description": "Repository name, without the owner prefix (not \"owner/repo\")."
      },
      "issue_number": {
        "type": "integer",
        "minimum": 1,
        "description": "The issue's number as shown in the GitHub UI (the \"#42\" number), not its internal node id or database id."
      },
      "detail": {
        "type": "string",
        "enum": ["summary", "full"],
        "default": "summary",
        "description": "summary (default, applied by the server when omitted) returns core fields plus a fixed-length body_preview. full adds the complete body, reaction counts, and the full milestone object. Field density changes between modes; the response shape and every summary-mode field stay the same in full mode — see outputSchema for exactly which fields are conditional."
      }
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
      "number", "title", "state", "locked", "html_url", "author",
      "assignees", "labels", "comments_count", "created_at", "updated_at",
      "body_preview", "version"
    ],
    "properties": {
      "number": {"type": "integer"},
      "title": {"type": "string"},
      "state": {"type": "string", "enum": ["open", "closed"]},
      "state_reason": {
        "type": "string",
        "enum": ["completed", "not_planned", "reopened"],
        "description": "Present only when state is closed and GitHub recorded a reason; absent otherwise."
      },
      "locked": {"type": "boolean"},
      "lock_reason": {
        "type": "string",
        "enum": ["off-topic", "too heated", "resolved", "spam"],
        "description": "Present only when locked is true; absent otherwise."
      },
      "html_url": {"type": "string", "format": "uri"},
      "author": {
        "type": "object",
        "required": ["login", "id"],
        "properties": {"login": {"type": "string"}, "id": {"type": "integer"}}
      },
      "assignees": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["login", "id"],
          "properties": {"login": {"type": "string"}, "id": {"type": "integer"}}
        }
      },
      "labels": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["name", "color"],
          "properties": {"name": {"type": "string"}, "color": {"type": "string"}}
        }
      },
      "milestone": {
        "type": "object",
        "description": "Present only when the issue has a milestone assigned; absent otherwise.",
        "properties": {
          "number": {"type": "integer"},
          "title": {"type": "string"},
          "due_on": {"type": ["string", "null"], "format": "date-time"}
        }
      },
      "comments_count": {"type": "integer"},
      "created_at": {"type": "string", "format": "date-time"},
      "updated_at": {"type": "string", "format": "date-time"},
      "closed_at": {
        "type": "string",
        "format": "date-time",
        "description": "Present only when state is closed; absent otherwise."
      },
      "body_preview": {
        "type": "string",
        "maxLength": 300,
        "description": "First 300 characters of the issue body. Always present in both detail modes — this is the concise field detail:\"full\" adds to, never replaces."
      },
      "body": {
        "type": "string",
        "description": "Complete raw markdown body. Present only when detail is \"full\"."
      },
      "reactions": {
        "type": "object",
        "description": "Present only when detail is \"full\".",
        "properties": {
          "+1": {"type": "integer"}, "-1": {"type": "integer"},
          "laugh": {"type": "integer"}, "hooray": {"type": "integer"},
          "confused": {"type": "integer"}, "heart": {"type": "integer"},
          "rocket": {"type": "integer"}, "eyes": {"type": "integer"},
          "total_count": {"type": "integer"}
        }
      },
      "version": {
        "type": "string",
        "description": "Opaque content-derived token (hash of updated_at + node id + label/assignee set). Always present. Pass back as if_match on github_issues_update_issue to guard against a concurrent edit between this read and that write."
      }
    },
    "additionalProperties": false
  },
  "annotations": {
    "readOnlyHint": true,
    "openWorldHint": true
  },
  "_meta": {
    "com.github-issues-mcp/errors": [
      {"code": "issue_not_found", "temporary": false, "description": "No issue with this number in owner/repo (or it's a pull request, or the credential can't see it). Repair: search_issues or list_issues."},
      {"code": "repo_not_found", "temporary": false, "description": "owner/repo does not exist or is not visible to the credential."},
      {"code": "insufficient_scope", "temporary": false, "description": "Credential lacks read access to Issues on this repository."},
      {"code": "rate_limited", "temporary": true, "description": "GitHub API rate limit hit; honor retry_after_ms."}
    ]
  }
}
```

`destructiveHint`/`idempotentHint` are omitted, not defaulted-and-hidden: per the §3 annotation rule they're meaningful only when `readOnlyHint` is `false`, so asserting them here would claim semantics the protocol doesn't assign to a read-only tool.
`body_preview` staying present in `full` mode (rather than being replaced by `body`) is the same discipline as `examples.md` §2's `preview`/`text` split — concise fields are a strict subset of detailed fields, never renamed between modes.
`version` is populated on every read specifically so `update_issue`'s `if_match` precondition (§3 read-modify-write rule) has a real value to consume; it is not conditional.

### SUCCESS — wire shape

Request:

```json
{
  "jsonrpc": "2.0",
  "id": "req_100",
  "method": "tools/call",
  "params": {
    "name": "github_issues_get_issue",
    "arguments": {"owner": "octocat", "repo": "hello-world", "issue_number": 42}
  }
}
```

Response (`detail` omitted → `summary`):

```json
{
  "jsonrpc": "2.0",
  "id": "req_100",
  "result": {
    "isError": false,
    "content": [
      {"type": "text", "text": "Issue #42 \"Camera flash is too bright\" — open, 3 comments, no milestone. Opened by octocat on 2026-06-02."}
    ],
    "structuredContent": {
      "number": 42,
      "title": "Camera flash is too bright",
      "state": "open",
      "locked": false,
      "html_url": "https://github.com/octocat/hello-world/issues/42",
      "author": {"login": "octocat", "id": 583231},
      "assignees": [],
      "labels": [{"name": "bug", "color": "d73a4a"}],
      "comments_count": 3,
      "created_at": "2026-06-02T14:03:11Z",
      "updated_at": "2026-07-09T08:12:44Z",
      "body_preview": "When taking a photo in low light the flash overexposes the subject. Steps to reproduce: 1) open camera 2) enable flash…",
      "version": "gh_v1.a1b2c3d4e5f6"
    }
  }
}
```

Notes on carrier placement, mapped directly to the §3/native-wire-shapes rules:

- **`structuredContent` is the authoritative, machine-parsed contract.** It validates against the `outputSchema` above. `milestone`, `closed_at`, `state_reason`, `lock_reason`, `body`, and `reactions` are all correctly absent here — the issue is open, unlocked, has no milestone, and `detail` was `summary` — per the "advertise only fields you populate" rule; none of them are sent as `null` placeholders.
- **`content` is a human-rendering fallback**, not the contract — a compatibility text block for clients that don't surface `structuredContent`. An agent should never parse `content[0].text`; the same information lives structured above.
- **`isError` is explicitly `false`.** Shown explicitly here for contrast with the error shape below, even though a client may treat an omitted `isError` as falsy.
- No `_meta` is needed on this result; nothing here is auxiliary metadata a model shouldn't see — everything is declared domain output, so it stays in `structuredContent`.

`detail: "full"` response differs only by adding fields — `body_preview` stays, `body` and `reactions` are added:

```json
{
  "structuredContent": {
    "number": 42,
    "title": "Camera flash is too bright",
    "state": "open",
    "locked": false,
    "html_url": "https://github.com/octocat/hello-world/issues/42",
    "author": {"login": "octocat", "id": 583231},
    "assignees": [],
    "labels": [{"name": "bug", "color": "d73a4a"}],
    "comments_count": 3,
    "created_at": "2026-06-02T14:03:11Z",
    "updated_at": "2026-07-09T08:12:44Z",
    "body_preview": "When taking a photo in low light the flash overexposes the subject. Steps to reproduce: 1) open camera 2) enable flash…",
    "body": "When taking a photo in low light the flash overexposes the subject.\n\nSteps to reproduce:\n1. Open camera\n2. Enable flash\n3. Take photo indoors\n\nExpected: balanced exposure. Actual: subject is blown out.",
    "reactions": {"+1": 4, "-1": 0, "laugh": 0, "hooray": 0, "confused": 0, "heart": 1, "rocket": 0, "eyes": 2, "total_count": 7},
    "version": "gh_v1.a1b2c3d4e5f6"
  }
}
```

### ERROR — wire shape

`outputSchema` governs success results only (the §3 deliberate reading of the unsettled point). An `isError: true` result carries the §6 unified error envelope in `structuredContent` instead — a different, documented shape, never validated against the success `outputSchema` above. Three failure modes are worked in full (the task asks for at least two).

**(a) `issue_not_found`** — the issue number doesn't exist, is a PR, or isn't visible:

Request: `{"owner": "octocat", "repo": "hello-world", "issue_number": 99999}`

```json
{
  "jsonrpc": "2.0",
  "id": "req_101",
  "result": {
    "isError": true,
    "content": [
      {"type": "text", "text": "Issue #99999 not found in octocat/hello-world. It may not exist, may be a pull request, or the credential may lack access. Verify the number with github_issues_search_issues or github_issues_list_issues."}
    ],
    "structuredContent": {
      "code": "issue_not_found",
      "message": "No issue #99999 in octocat/hello-world.",
      "details": {
        "field": "issue_number",
        "value": 99999,
        "reason": "No issue with this number exists in the repository, it is a pull request, or the credential cannot see it."
      },
      "temporary": false,
      "retry_after_ms": null,
      "repair": {
        "next_step": "search_then_retry",
        "tool": "github_issues_search_issues",
        "arguments": {"query": "repo:octocat/hello-world"},
        "alternative": "Browse the github-issues://octocat/hello-world/issues resource index or call github_issues_list_issues to find a valid issue_number."
      },
      "request_id": "req_01J8Y6VZC3K9N7QABCDEF",
      "fingerprint": "github-issues-mcp@1.0.0+a1b2c3"
    }
  }
}
```

**(b) `repo_not_found`** — distinguishes a bad `repo` from a bad `issue_number`, a different `details.field`, and — deliberately — no `repair`:

Request: `{"owner": "octocat", "repo": "hello-wrld", "issue_number": 42}`

```json
{
  "jsonrpc": "2.0",
  "id": "req_102",
  "result": {
    "isError": true,
    "content": [
      {"type": "text", "text": "No repository 'hello-wrld' under owner 'octocat', or it is private and the credential can't see it. This server has no repository-lookup tool; verify the owner/repo spelling with the user or another source."}
    ],
    "structuredContent": {
      "code": "repo_not_found",
      "message": "No repository octocat/hello-wrld visible to this credential.",
      "details": {
        "field": "repo",
        "value": "hello-wrld",
        "reason": "Repository does not exist under this owner, or is private and the credential lacks access."
      },
      "temporary": false,
      "retry_after_ms": null,
      "request_id": "req_01J8Y6W1D4L0P8RBCDEFG",
      "fingerprint": "github-issues-mcp@1.0.0+a1b2c3"
    }
  }
}
```

`repair` is omitted entirely (never `null`, never `{}`) because this server has no repository-search or repository-listing tool to route to — per the §6 rule that `repair` is dropped rather than faked when no real corrective call exists. The `content` text still gives the agent the human-actionable next step in prose, since there's no machine one to offer.

**(c) `rate_limited`** — transient, with a retry delay and no field-level `details`:

```json
{
  "jsonrpc": "2.0",
  "id": "req_103",
  "result": {
    "isError": true,
    "content": [
      {"type": "text", "text": "GitHub API rate limit exceeded. Retry after 42 seconds."}
    ],
    "structuredContent": {
      "code": "rate_limited",
      "message": "GitHub API rate limit exceeded.",
      "temporary": true,
      "retry_after_ms": 42000,
      "rate_limit_remaining": 0,
      "request_id": "req_01J8Y6WK5N2R9TVCDEFGH",
      "fingerprint": "github-issues-mcp@1.0.0+a1b2c3"
    }
  }
}
```

No `details` (no single offending field — the whole call was throttled) and no `repair` (waiting is the only corrective action, and `temporary: true` + `retry_after_ms` already say that; per §6, "a `temporary: true` one may carry none").

What every error example holds in common, called out explicitly since this is the crux of the task:

- **`isError: true` sits at the top level of the `tools/call` result**, a native field — never inside `structuredContent`.
- **The error envelope travels in `structuredContent`, not `content`. `content[0].text` is a redundant human-readable mirror for weak clients, never the parsed source of truth.**
- **`outputSchema` does not govern these payloads.** None of the three `structuredContent` bodies above would validate against `get_issue`'s success `outputSchema` (no `number`, `title`, etc.) — that's correct and expected under this skill's scoping position, not a bug; a validating client must branch on `isError` before choosing which schema (success `outputSchema` vs. the documented error envelope) to check against.
- **This server never needs the degraded `content[0].text`-only carrier.** Its framework places native `structuredContent` on `isError: true` results without limitation, so `error_carriers` in the capability summary names only `structuredContent`/`error.data` — no disclosed degraded mode.
- **`code` is the branch key**, `temporary`/`retry_after_ms` govern retry behavior independent of `repair`, and `repair` (when present) names a real, already-callable `tools/list` entry with literal arguments — never a placeholder.

---

## Error taxonomy summary (§6)

Full per-tool catalogs live in each tool's `_meta.com.github-issues-mcp/errors` (inline, kept to repair-critical codes per the §2/§6 cost-placement rule); the on-demand full catalog — including less common codes like `insufficient_scope`, `invalid_field`, and `capability_not_negotiated` — is served by the `github-issues://error-catalog` resource, not repeated in every `tools/list` entry.

| Code | Temporary | Typical tools | Repair |
| --- | --- | --- | --- |
| `issue_not_found` | false | get_issue, update_issue, add_comment, list_comments, set_lock, update_labels | `search_issues` or `list_issues` |
| `repo_not_found` | false | all tools | none (no repo-lookup tool in scope) |
| `validation_failed` | false | create_issue, update_issue, set_lock, update_labels | field-level `details`, no separate lookup needed |
| `conflict` | false | update_issue (`if_match` mismatch) | re-read via `get_issue`, reapply with fresh `version` |
| `issue_locked` | false | add_comment | `set_lock` (collaborators only) or wait for a maintainer |
| `insufficient_scope` | false | all mutating tools | none machine-actionable; surfaces `details.required_scopes` |
| `rate_limited` | true | all tools | none; back off `retry_after_ms` |

Every code follows the §6 unified envelope: resource-surface failures (below) carry the identical fields renamed only `code`→`machine_code`, `message`→`human_message`, per the "one envelope, two carriers" rule — there is no separate resource-error vocabulary.

---

## Resource surface (§4)

Tool fallback exists for everything (§4's "self-sufficient from `tools/list` alone" rule), so resources are a convenience layer for citation and browsing, not a required path.

- `github-issues://{owner}/{repo}/issues` — index of open issues (summary metadata: `uri`, `name`, `title`, `description`, `annotations.lastModified`). Mirrors `list_issues` with `state=open`.
- `github-issues://{owner}/{repo}/issues/{issue_number}` — single-issue body, same fields as `get_issue` `detail=full`.
- `github-issues://{owner}/{repo}/issues/{issue_number}/comments` — comment thread, chunked by comment-id ranges for long threads (same pattern as `examples.md` §4: stable chunk ids, `next_chunk_uri` in-band).
- `github-issues://capability-summary` — the capability summary above.
- `github-issues://error-catalog` — full per-tool error catalog, referenced from tool `_meta` and the summary's `error_carriers`.

`resources/templates/list` publishes `{owner}/{repo}` and `{issue_number}` as template variables; `completion/complete` is implemented for `{owner}`/`{repo}` (gated on `server.capabilities.completions`) since repository names are dynamic and hard to guess — MCP completion does not cover tool arguments, so `list_issues`/`get_issue`'s own `owner`/`repo` fields still validate and repair normally as tool errors, per the §4/§2 completion rule.

Resource-read failures (e.g. `resources/read` on a deleted issue) use JSON-RPC errors, not tool-result errors — same envelope, `error.data`, renamed keys, exactly as `examples.md` §6 models it:

```json
{
  "jsonrpc": "2.0",
  "id": "req_104",
  "error": {
    "code": -32002,
    "message": "Resource not found.",
    "data": {
      "machine_code": "issue_not_found",
      "human_message": "No issue #99999 in octocat/hello-world.",
      "details": {"field": "uri", "value": "github-issues://octocat/hello-world/issues/99999", "reason": "No issue with this number exists, or it is a pull request."},
      "temporary": false,
      "retry_after_ms": null,
      "repair": {
        "next_step": "search_then_retry",
        "tool": "github_issues_search_issues",
        "arguments": {"query": "repo:octocat/hello-world"}
      },
      "resource_uri": "github-issues://octocat/hello-world/issues/99999",
      "request_id": "req_01J8Y6X9F1M3S5UCDEFGH",
      "fingerprint": "github-issues-mcp@1.0.0+a1b2c3"
    }
  }
}
```

Branch on `machine_code`, not the numeric `-32002` — that numeric code is a likely migration point under the 2026-07-28 RC (folded into `-32602`), per `SKILL.md`'s Spec Baseline.

Subscriptions (§4/§9): not offered. Issues can change from any GitHub actor at any time and an agent session is typically short-lived per task; `annotations.lastModified` plus `version` (for `update_issue`'s `if_match`) covers staleness detection without a persistent subscription. Marked not-applicable with this justification.

---

## Long-running operations (§7)

Every tool here completes within a normal GitHub REST round trip; none crosses into progress/task territory. `server.capabilities.tasks.requests.tools.call` is not advertised, and no tool declares `execution.taskSupport`. Marked not-applicable with this one-line justification, per Done Criteria.
Pagination for `list_issues`/`search_issues`/`list_comments` is handled by the §8 house `has_more`/`next_cursor` convention in `structuredContent` (documented per-tool above), not by task augmentation — a paginated walk is not a long-running operation in the §7 sense.

---

## Versioning (§9)

`fingerprint` (`github-issues-mcp@1.0.0+a1b2c3`, shown throughout) covers tools, the resource catalog/templates, completion support, error codes, and the capability summary, and rides in every response's correlation fields and the summary resource. Deprecation, if a tool is ever renamed or split, follows the standard remove-plus-add sweep (old name kept with a `deprecation` pointer through the documented window, every `repair.tool` reference updated atomically) — no server-specific exception is taken here.

---

## Not-applicable, with justification (Done Criteria)

- **Roots (§1):** this server has no local filesystem or workspace surface — every scope is `owner`/`repo` tool arguments, already expressible without roots and unaffected by roots' RC deprecation.
- **Elicitation (§6):** no tool needs mid-call user input or a sensitive-credential exchange; auth is a bearer token supplied once at connection time, not per-call.
- **Tasks (§7):** covered above — no operation outlives a normal request/response turn.
- **Resource subscriptions (§4/§9):** covered above — `lastModified` + `version` substitute for the session lifetimes this server targets.
- **Progressive disclosure / `search_tools` (§2):** covered above — 9 compact tool definitions fit comfortably in a preloading client's budget; adding a discovery layer would cost more than it saves.
