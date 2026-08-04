# Scenario 1 (Design) — with-skill run (2026-07-28 rebase re-run)

- **Date:** 2026-07-29
- **Tree:** `1b0b743` (main, post #130 rebase + #133 §6 fixes)
- **Mode:** with-skill (fresh general-purpose subagent on `claude-fable-5`; read `SKILL.md` + all eight `references/` files, explicitly forbidden from `tests/` and `decisions/`; 11 tool-uses, all reads)
- **Score:** 10/10

**Erratum, 2026-07-30 (#122).** The A9 evidence cell below originally read "Closed `outputSchema` shown per tool."
That overstated what this run produced: 8 tool definitions appear below and 7 carry an `outputSchema`, with `github_get_capabilities` publishing none.
The sentence was wrong under the assertion in force at the time, independent of any later tightening, so the factual claim is corrected here.
The score is not restated in this file — #122's restatement of it lives in the Results table of `../scenarios.md`, per the policy recorded there.

## Exact prompt given

Same GitHub Issues prompt as the baseline (see `2026-07-29-scenario1-baseline.md`), preceded by an instruction to read `agent-friendly-mcp/SKILL.md` and every file under `references/` as authoritative guidance, and **not** to read anything under `tests/` or `decisions/`.

## Per-assertion scoring

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| A1 | Task-completing granularity, ~4–7 tools, folds justified | **PASS** | 11 endpoints → 7 domain tools + 1 discovery-fallback tool; labels folded into `github_update_issue`; lock/unlock folded into `github_set_issue_lock`; the one split (`github_list_comments`) carries a named exception ("streaming large results"). |
| A2 | `snake_case`, service-prefixed, verb+noun | **PASS** | `github_find_issues`, `github_get_issue`, `github_create_issue`, … throughout. |
| A3 | Closed schemas, disambiguated names, omission semantics, server-applied defaults only | **PASS** | `additionalProperties: false` + declared `$schema` everywhere; `owner`/`repo`/`issue_number` with patterns and "not the node id" notes; every optional states omission semantics, including the absent/`null`/empty-array distinction; defaults marked "(the server applies this default)". |
| A4 | ≥2 error payloads, symbolic codes, field detail, repair naming a callable surface | **PASS** | Three worked tool-result payloads (`issue_locked`, `rate_limited`, `version_conflict`) plus a JSON-RPC carrier example; 12-code catalog; repairs name `github_set_issue_lock` / `github_get_issue` with literal arguments. |
| A5 | Tool errors as `isError: true` results, not JSON-RPC | **PASS** | Stated with the protocol-level exceptions (`-32602`/`-32020`/`-32021` stay native per `[6.tool-errors]`). |
| A6 | Capability summary with negative scope | **PASS** | `does_not` block (no PR management, no label/milestone objects, no deletes/transfers, no push notifications) served via resource, tool, and advisory `instructions`. |
| A7 | Pagination provenance-correct with detail toggle | **PASS** | `has_more`/`next_cursor` explicitly labeled "House pagination convention, not protocol" inside tool payloads (`[8.house-pagination]`); native `nextCursor` reserved for list methods; `detail: summary\|full` toggle is density-only with server-applied default. |
| A8 | Honest annotations | **PASS** | Reads carry only `readOnlyHint: true` (+`[3.annotation-defaults]` omission of the mutation hints); `github_create_issue` `idempotentHint: false` with the `idempotency_key` rationale; `github_update_issue` `destructiveHint: true` justified by body/label replacement; the observable-scope `readOnlyHint` reading is declared in the summary. |
| A9 | `outputSchema` published; success `structuredContent` + `content` fallback | **PASS** | Closed `outputSchema` shown on 7 of the 8 tools defined below, with the pairing prose stated: "success results return `structuredContent` conforming to a published `outputSchema` (closed), with the same JSON serialized in `content[0].text` as fallback". `github_get_capabilities` publishes none — see the erratum above. |
| A10 | **(Scored.)** Error path distinct; `outputSchema` scoped to success, attributed to the skill's reading, or a documented union | **PASS** (caveat) | "`isError: true` results carry the §6 error envelope instead — `outputSchema` scopes to success only, documented per tool (`[3.output-schema-scope]`)"; envelope in `structuredContent` with a text mirror. Caveat: attributes the scoping to the checklist rule by id rather than verbalizing "unsettled spec point" — attribution satisfied, hedge not repeated. |

**Passed:** all (10/10).
Notable beyond the assertions: per-request `_meta` gating and `server/discover` cache hints (`[8.cacheable-results]` matrix with per-surface `ttlMs`/`cacheScope`), an optimistic-concurrency `version`/`expected_version` design with a worked `version_conflict` repair, the `[6.rename]` spellings on the JSON-RPC carrier, and a disclosed blanket `details.value`-omission policy for free-form parameters citing the exact `[6.offending-value]` distinction shipped in #133.

## Full final output

Verbatim final message returned by the with-skill subagent (11 tool-uses, all file reads):

Below is the complete design, produced by walking `design-workflow.md` Steps 0–7 against `contract-checklist.md`. (Step 8/9 eval fixtures are noted at the end as follow-up work, since they require a running server.)

---

# `github-issues-mcp` — agent-facing contract

## 0. Revision target and granularity decisions

**Protocol target** (`[1.spec-revision]`, Step 0): **MCP 2026-07-28 only, no extensions.** No operation here outlives a request/response turn (worst case is a search round-trip), so the `io.modelcontextprotocol/tasks` extension is not negotiated and §7 is N/A. The server advertises `completions` and publishes stateless per-request semantics; cross-call state is limited to opaque cursors and `version` tokens passed as ordinary arguments (`[1.state-handles]`).

**Granularity** (Step 1–3, `[3.task-completing]`, `[3.ap-endpoint-wrapping]`): the 11 REST endpoints express **8 tools**:

| Tool | Endpoints absorbed | Why |
| --- | --- | --- |
| `github_find_issues` | list issues, search issues | The agent task is "which issues…?", not "choose list vs search". One tool with structured filters plus optional free-text `query`; the server routes to the right endpoint internally (`[3.hide-steps]`). |
| `github_get_issue` | get issue, list comments (first page) | "Read an issue" means the issue **and** its discussion; forcing two calls for every read is endpoint mirroring. |
| `github_list_comments` | list comments (continuation) | Split kept — named exception: *streaming large results* (long threads page independently of the issue read). |
| `github_create_issue` | create issue (+ labels/assignees at creation) | One call completes "file an issue", labels included. |
| `github_update_issue` | update issue, add labels, remove labels | Label edits are part of "update an issue"; three label endpoints collapse into replace/add/remove parameters. |
| `github_add_comment` | add comment | Already task-shaped. |
| `github_set_issue_lock` | lock issue, unlock issue | One moderation task with a `locked` boolean; both modes are non-destructive idempotent mutations, so one annotation set is honest (`[3.ap-mode-annotations]` satisfied — no read mode mixed in). |
| `github_get_capabilities` | — | Discovery tool fallback (`[2.instructions-advisory]`, `[4.tool-fallback]`): the capability summary must reach clients that surface neither `instructions` nor resources. |

Two deliberate domain decisions, both declared in the discovery surface:

- **Pull requests are excluded by default.** GitHub's issues API mingles PRs into issue lists; agents burned by this get an `include_pull_requests: false` default instead.
- **Optimistic concurrency** (`[3.version-precondition]`): every read surface returns an opaque content-derived `version` token; `github_update_issue` accepts optional `expected_version` and fails with `version_conflict` on mismatch, repair = re-read.

## 1. Tools with input schemas

Shared conventions, stated once (`[3.param-names]`, `[3.closed-schemas]`, `[3.strict-types]`):

- Every `inputSchema` declares `"$schema": "https://json-schema.org/draft/2020-12/schema"` and `"additionalProperties": false`.
- `owner`, `repo`, `issue_number` are identical wherever they appear; the full property definitions are shown in the first tool and elided as `…common…` afterward.
- Every optional parameter states its omission semantics; `default` appears only where the server actually applies it (`[3.required-optional]`). No parameter accepts `null` — clearing uses explicit empty string/array, documented per field.
- Annotations follow the observable-scope reading of `readOnlyHint`, declared in the capability summary (`[3.document-reading]`); `destructiveHint`/`idempotentHint` are omitted on read-only tools (`[3.annotation-defaults]`).
- Success results return `structuredContent` conforming to a published `outputSchema` (closed), with the same JSON serialized in `content[0].text` as fallback (`[3.output-schema]`). `isError: true` results carry the §6 error envelope instead — `outputSchema` scopes to success only, documented per tool (`[3.output-schema-scope]`).

### `github_find_issues` — read-only

```json
{
  "name": "github_find_issues",
  "description": "Find issues in one GitHub repository by structured filters and/or free-text query.\n\nWhen to use: any 'which issues…' question — listing open issues, filtering by label/assignee/author/state, or searching title, body, and comment text. This single tool covers both the REST list and search endpoints; do not look for a separate search tool.\nPull requests are excluded unless include_pull_requests is true (GitHub's underlying API mingles PRs into issue lists).\nResults are capped at 50 per page; continue with cursor, and narrow with state=, labels=, updated_after=, or query= when truncated is true.\nFree-text query calls draw from a separate, smaller upstream rate budget (~30/min); prefer structured filters when they suffice.\n\nExample: {\"owner\": \"acme\", \"repo\": \"widgets\", \"state\": \"open\", \"labels\": [\"bug\"], \"query\": \"crash on save\"}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo"],
    "properties": {
      "owner": {
        "type": "string",
        "pattern": "^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$",
        "description": "Repository owner login (user or organization), e.g. \"acme\". Not the full \"owner/repo\" slug."
      },
      "repo": {
        "type": "string",
        "pattern": "^[A-Za-z0-9._-]+$",
        "description": "Repository name without the owner, e.g. \"widgets\"."
      },
      "query": {
        "type": "string",
        "minLength": 1,
        "maxLength": 256,
        "description": "Free-text search over issue titles, bodies, and comments. Omitted means filter-only listing (no text match)."
      },
      "state": {
        "type": "string",
        "enum": ["open", "closed", "all"],
        "default": "open",
        "description": "Issue state filter. Omitted means \"open\" (the server applies this default)."
      },
      "labels": {
        "type": "array",
        "items": {"type": "string"},
        "minItems": 1,
        "description": "Return only issues carrying ALL of these label names (AND semantics). Omitted means no label filter."
      },
      "assignee": {
        "type": "string",
        "description": "Assignee login. Omitted means no assignee filter."
      },
      "creator": {
        "type": "string",
        "description": "Issue author login. Omitted means no author filter."
      },
      "updated_after": {
        "type": "string",
        "format": "date-time",
        "description": "RFC3339 UTC instant; only issues updated at or after it. Omitted means no time bound."
      },
      "include_pull_requests": {
        "type": "boolean",
        "default": false,
        "description": "Also return pull requests, which GitHub models as issues. Omitted means false (the server applies this default)."
      },
      "detail": {
        "type": "string",
        "enum": ["summary", "full"],
        "default": "summary",
        "description": "\"summary\" returns number/title/state/labels/assignees/updated_at per issue; \"full\" adds body_preview, author, comment_count, created_at, url, version. Same shape, denser fields — prefer \"summary\" unless the task needs the extra fields. Omitted means \"summary\" (server-applied)."
      },
      "cursor": {
        "type": "string",
        "description": "Opaque continuation token from a previous response's next_cursor. Omitted starts at the first page. Cursors expire after 15 minutes (error code cursor_expired: restart without a cursor)."
      }
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["issues", "has_more"],
    "properties": {
      "issues": {"type": "array", "items": {"type": "object"}},
      "has_more": {"type": "boolean", "description": "House pagination convention, not protocol. True means next_cursor is present."},
      "next_cursor": {"type": "string"},
      "estimated_total": {"type": "integer"},
      "truncated": {"type": "boolean", "description": "True means the result set is capped at 500 matches; paging stops there even if has_more pages remain within the cap."},
      "truncation_hint": {"type": "string"}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": true, "openWorldHint": true}
}
```

Notes: `has_more`/`next_cursor`/`estimated_total` are the documented **house** pagination convention inside the tool's own payload (`[8.house-pagination]`) — never confused with the native `nextCursor` on list methods. `truncated` and `has_more` are distinct signals carried together (`[8.truncation]`). Detail toggle changes field density, never row count; summary fields are a strict subset of full (`[8.detail-orthogonal]`, `[8.detail-row-count]`).

### `github_get_issue` — read-only

```json
{
  "name": "github_get_issue",
  "description": "Read one issue and the first page of its comment thread in a single call.\n\nWhen to use: the agent has an issue number (from github_find_issues, a URL, or the user) and wants the content. Returns the issue plus up to 20 comments; if comments_has_more is true, continue with github_list_comments using comments_cursor.\nThe returned version token is required for safe edits: pass it as expected_version to github_update_issue.\n\nExample: {\"owner\": \"acme\", \"repo\": \"widgets\", \"issue_number\": 1423}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo", "issue_number"],
    "properties": {
      "owner": {"…common…": true},
      "repo": {"…common…": true},
      "issue_number": {
        "type": "integer",
        "minimum": 1,
        "description": "The issue's number within the repository (the N in #N and in /issues/N URLs). Not the global GitHub node id."
      },
      "include_comments": {
        "type": "boolean",
        "default": true,
        "description": "Include the first 20 comments. Omitted means true (server-applied). Set false for a cheaper metadata-only read."
      },
      "detail": {
        "type": "string",
        "enum": ["summary", "full"],
        "default": "summary",
        "description": "\"summary\" caps body and comment bodies at 2000 chars each with a body_truncated flag; \"full\" returns complete text. Omitted means \"summary\" (server-applied)."
      }
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["issue"],
    "properties": {
      "issue": {
        "type": "object",
        "required": ["issue_number", "title", "state", "locked", "labels", "assignees", "created_at", "updated_at", "body", "version", "url"],
        "properties": {
          "issue_number": {"type": "integer"},
          "title": {"type": "string"},
          "state": {"type": "string", "enum": ["open", "closed"]},
          "state_reason": {"type": "string", "enum": ["completed", "not_planned", "reopened"], "description": "Present only when state is closed or the issue was reopened."},
          "locked": {"type": "boolean"},
          "lock_reason": {"type": "string", "description": "Present only when locked is true and a reason was given."},
          "author": {"type": "string"},
          "labels": {"type": "array", "items": {"type": "string"}},
          "assignees": {"type": "array", "items": {"type": "string"}},
          "created_at": {"type": "string", "format": "date-time"},
          "updated_at": {"type": "string", "format": "date-time"},
          "body": {"type": "string"},
          "body_truncated": {"type": "boolean", "description": "Present only in detail=summary when the body was capped; re-read with detail=full for complete text."},
          "version": {"type": "string", "description": "Opaque optimistic-concurrency token for this issue's current content. Pass to github_update_issue as expected_version."},
          "url": {"type": "string", "format": "uri"}
        },
        "additionalProperties": false
      },
      "comments": {"type": "array", "items": {"type": "object"}, "description": "Present when include_comments is true."},
      "comments_has_more": {"type": "boolean"},
      "comments_cursor": {"type": "string", "description": "Pass to github_list_comments to continue the thread."}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": true, "openWorldHint": true}
}
```

Conditional fields (`state_reason`, `lock_reason`, `body_truncated`, the comments trio) document their appearance conditions and are populated exactly then (`[3.advertised-fields]`, `[6.presence]`-style discipline on success payloads).

### `github_list_comments` — read-only

```json
{
  "name": "github_list_comments",
  "description": "Continue reading an issue's comment thread beyond the page github_get_issue returned, or re-read only comments newer than a timestamp.\n\nWhen to use: comments_has_more was true on github_get_issue, or the agent wants recent activity without re-reading the issue.\n\nExample: {\"owner\": \"acme\", \"repo\": \"widgets\", \"issue_number\": 1423, \"cursor\": \"…\"}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo", "issue_number"],
    "properties": {
      "owner": {"…common…": true},
      "repo": {"…common…": true},
      "issue_number": {"…common…": true},
      "cursor": {"type": "string", "description": "Opaque token from comments_cursor or next_cursor. Omitted starts at the oldest comment. Expires after 15 minutes (cursor_expired)."},
      "updated_after": {"type": "string", "format": "date-time", "description": "Only comments created or edited at/after this RFC3339 UTC instant. Omitted means the whole thread."},
      "detail": {"type": "string", "enum": ["summary", "full"], "default": "summary", "description": "\"summary\" caps each comment body at 2000 chars; \"full\" returns complete text. Omitted means \"summary\" (server-applied)."}
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["comments", "has_more"],
    "properties": {
      "comments": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["comment_id", "author", "created_at", "body"],
          "properties": {
            "comment_id": {"type": "integer"},
            "author": {"type": "string"},
            "created_at": {"type": "string", "format": "date-time"},
            "updated_at": {"type": "string", "format": "date-time"},
            "body": {"type": "string"},
            "body_truncated": {"type": "boolean"}
          },
          "additionalProperties": false
        }
      },
      "has_more": {"type": "boolean"},
      "next_cursor": {"type": "string"}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": true, "openWorldHint": true}
}
```

### `github_create_issue` — mutation

```json
{
  "name": "github_create_issue",
  "description": "Create a new issue, optionally with labels and assignees, in one call.\n\nWhen to use: the user wants to file a bug, feature request, or task. Do not use to edit an existing issue (github_update_issue) or to comment (github_add_comment).\nIf a call times out, do not blind-retry: re-send with the same idempotency_key, or check for the issue via github_find_issues (query on the title) before retrying without one.\nLabels named here must already exist in the repository; unknown labels fail the whole call with label_not_found (no partial creation).\n\nExample: {\"owner\": \"acme\", \"repo\": \"widgets\", \"title\": \"Crash on save with empty filename\", \"body\": \"Steps: …\", \"labels\": [\"bug\"], \"idempotency_key\": \"crash-on-save-2026-07-29\"}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo", "title"],
    "properties": {
      "owner": {"…common…": true},
      "repo": {"…common…": true},
      "title": {"type": "string", "minLength": 1, "maxLength": 256, "description": "Issue title."},
      "body": {"type": "string", "maxLength": 65536, "description": "Issue body, GitHub-flavored Markdown. Omitted means an empty body."},
      "labels": {"type": "array", "items": {"type": "string"}, "description": "Label names to apply at creation; each must already exist in the repository. Omitted means no labels."},
      "assignees": {"type": "array", "items": {"type": "string"}, "description": "Logins to assign; each must have repository access. Omitted means unassigned."},
      "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 128, "description": "Optional client-supplied key; repeat calls with the same key within 24 hours (scoped per repository) return the originally created issue instead of creating a duplicate. Omitted means no deduplication — each call creates."}
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["issue_number", "url", "version", "labels", "assignees"],
    "properties": {
      "issue_number": {"type": "integer"},
      "url": {"type": "string", "format": "uri"},
      "version": {"type": "string"},
      "labels": {"type": "array", "items": {"type": "string"}, "description": "Labels as actually applied."},
      "assignees": {"type": "array", "items": {"type": "string"}, "description": "Assignees as actually applied."}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": true}
}
```

`idempotentHint: false` is honest — retrying without the key duplicates — which is exactly why `idempotency_key` exists with a declared window and scope (`[3.idempotency-key]`), and the description names the ambiguous-timeout reconcile path (`[3.dispatched-vs-applied]`).

### `github_update_issue` — mutation

```json
{
  "name": "github_update_issue",
  "description": "Edit an existing issue: title, body, open/closed state, assignees, and labels — including incremental label add/remove — in one call.\n\nWhen to use: any change to an existing issue except commenting (github_add_comment) and locking (github_set_issue_lock). Closing an issue is state=\"closed\" with an optional state_reason.\nPass expected_version (from github_get_issue or github_find_issues detail=full) whenever the edit depends on content you previously read; a mismatch fails with version_conflict — re-read, then re-apply.\nlabels (replace whole set) is mutually exclusive with add_labels/remove_labels (incremental); sending both fails validation.\nEvery field you omit is left unchanged.\n\nExample: {\"owner\": \"acme\", \"repo\": \"widgets\", \"issue_number\": 1423, \"state\": \"closed\", \"state_reason\": \"completed\", \"add_labels\": [\"fixed-in-2.5\"], \"expected_version\": \"v:8c1f…\"}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo", "issue_number"],
    "properties": {
      "owner": {"…common…": true},
      "repo": {"…common…": true},
      "issue_number": {"…common…": true},
      "title": {"type": "string", "minLength": 1, "maxLength": 256, "description": "New title. Omitted means leave unchanged."},
      "body": {"type": "string", "maxLength": 65536, "description": "New body, replacing the old one entirely. Omitted means leave unchanged; empty string \"\" explicitly clears the body. null is not accepted."},
      "state": {"type": "string", "enum": ["open", "closed"], "description": "Omitted means leave unchanged."},
      "state_reason": {"type": "string", "enum": ["completed", "not_planned", "reopened"], "description": "Reason accompanying a state change. Valid only alongside state; \"reopened\" only with state=\"open\". Omitted means GitHub's default reason for the transition."},
      "assignees": {"type": "array", "items": {"type": "string"}, "description": "Replaces the full assignee set. Omitted means leave unchanged; empty array [] explicitly unassigns everyone."},
      "labels": {"type": "array", "items": {"type": "string"}, "description": "Replaces the full label set; mutually exclusive with add_labels/remove_labels. Omitted means leave unchanged; empty array [] removes all labels."},
      "add_labels": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "Labels to add, keeping existing ones; each must exist in the repository. Omitted means add none."},
      "remove_labels": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "Labels to remove; removing a label the issue does not carry is a no-op, not an error. Omitted means remove none."},
      "expected_version": {"type": "string", "description": "Optimistic-concurrency token from a prior read. If the issue changed since, the call fails with version_conflict and no edit is applied. Omitted means last-writer-wins (unguarded)."}
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["issue_number", "state", "labels", "assignees", "version", "url"],
    "properties": {
      "issue_number": {"type": "integer"},
      "state": {"type": "string", "enum": ["open", "closed"]},
      "labels": {"type": "array", "items": {"type": "string"}},
      "assignees": {"type": "array", "items": {"type": "string"}},
      "version": {"type": "string", "description": "The new version token after this edit."},
      "url": {"type": "string", "format": "uri"}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": false, "destructiveHint": true, "idempotentHint": true, "openWorldHint": true}
}
```

`destructiveHint: true` is honest: `body`/`labels` replacement overwrites prior content. `idempotentHint: true` because every parameter is an absolute set (re-applying the same update is a no-op). The three-way absent/`null`/empty distinction is defined per field (`[3.required-optional]`): `null` rejected everywhere, empty string/array is the explicit clearing form.

### `github_add_comment` — mutation

```json
{
  "name": "github_add_comment",
  "description": "Post a comment on an existing issue.\n\nWhen to use: adding discussion, status updates, or answers to an issue thread. Not for editing the issue body (github_update_issue).\nCommenting on a locked issue fails with issue_locked; the error names the unlock repair.\nIf a call times out, re-send with the same idempotency_key, or check the thread via github_list_comments (updated_after= your send time) before retrying without one.\n\nExample: {\"owner\": \"acme\", \"repo\": \"widgets\", \"issue_number\": 1423, \"body\": \"Fixed in 2.5.0, please verify.\"}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo", "issue_number", "body"],
    "properties": {
      "owner": {"…common…": true},
      "repo": {"…common…": true},
      "issue_number": {"…common…": true},
      "body": {"type": "string", "minLength": 1, "maxLength": 65536, "description": "Comment body, GitHub-flavored Markdown."},
      "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 128, "description": "Optional; repeat calls with the same key within 24 hours (scoped per issue) return the original comment instead of posting a duplicate. Omitted means no deduplication."}
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

### `github_set_issue_lock` — mutation

```json
{
  "name": "github_set_issue_lock",
  "description": "Lock or unlock an issue's conversation (moderation).\n\nWhen to use: the user wants to freeze or re-open discussion on an issue. Locking hides the comment box for non-collaborators; it does not close the issue (use github_update_issue state=\"closed\" for that).\nRequires push access to the repository; without it the call fails with insufficient_scope.\n\nExample: {\"owner\": \"acme\", \"repo\": \"widgets\", \"issue_number\": 1423, \"locked\": true, \"lock_reason\": \"resolved\"}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["owner", "repo", "issue_number", "locked"],
    "properties": {
      "owner": {"…common…": true},
      "repo": {"…common…": true},
      "issue_number": {"…common…": true},
      "locked": {"type": "boolean", "description": "true locks the conversation; false unlocks it."},
      "lock_reason": {"type": "string", "enum": ["off-topic", "too heated", "resolved", "spam"], "description": "Reason shown on the lock banner. Valid only when locked is true. Omitted means locked with no stated reason."}
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["issue_number", "locked"],
    "properties": {
      "issue_number": {"type": "integer"},
      "locked": {"type": "boolean"},
      "lock_reason": {"type": "string", "description": "Present only when locked is true and a reason was set."}
    },
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": true, "openWorldHint": true}
}
```

### `github_get_capabilities` — read-only discovery fallback

```json
{
  "name": "github_get_capabilities",
  "description": "Return this server's capability summary: what it does and does not do, auth prerequisites and failure codes, the full per-tool error catalog, rate-limit behavior, and the capability fingerprint.\n\nWhen to use: on first contact with this server, or when an unfamiliar error code needs its catalog entry. Same content as the github://capabilities resource, for clients that do not browse resources.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {},
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": true, "openWorldHint": false}
}
```

## 2. Failure contract

**One envelope, two carriers** (§6): tool semantic failures return `isError: true` with the envelope in `structuredContent` (plus a plain-text mirror in `content` for weak clients); `resources/read` failures return JSON-RPC errors with the identical envelope in `error.data`, renaming only `code`→`machine_code` and `message`→`human_message` (`[6.rename]`). `temporary` and `retry_after_ms` are always present (`retry_after_ms` is `null` unless a delay is known, and always `null` when `temporary: false`); `repair` is a single `{next_step, tool, arguments, alternative}` object, omitted entirely when no corrective path exists (`[6.repair-object]`, `[6.presence]`). Agents branch on the symbolic code, never the message.

**Error code catalog** (full catalog served by `github_get_capabilities` / `github://capabilities`; only repair-critical codes are inlined per tool definition under `_meta` `net.bconnelly.github-issues-mcp/errors` — `[6.document-codes]`):

| Code | `temporary` | Occurs on | Repair direction |
| --- | --- | --- | --- |
| `invalid_field` | false | any tool | field-level `details`, corrected retry |
| `issue_not_found` | false | get/update/comment/lock | `github_find_issues` |
| `repo_not_found` | false | any tool | check `owner`/`repo`; completion on the resource template |
| `label_not_found` | false | create/update | corrected retry; `details.allowed` carries the repo's label names |
| `issue_locked` | false | `github_add_comment` | `github_set_issue_lock` |
| `version_conflict` | false | `github_update_issue` | re-read via `github_get_issue`, re-apply |
| `cursor_expired` | false | find/list_comments | restart without `cursor` |
| `rate_limited` | true | any tool | wait `retry_after_ms` |
| `missing_credential` | false | any tool | operator action; named distinctly per `[1.cred-modes]` |
| `invalid_credential` | false | any tool | operator action |
| `insufficient_scope` | false | mutations | `details.required_scopes` |
| `upstream_unavailable` | true | any tool | wait `retry_after_ms` |

**Worked payload 1 — commenting on a locked issue** (semantic failure with a one-hop repair to a real tool):

```json
{
  "resultType": "complete",
  "isError": true,
  "content": [
    {"type": "text", "text": "Issue acme/widgets#1423 is locked (reason: resolved); comments are disabled. Unlock it with github_set_issue_lock, or escalate to the user."}
  ],
  "structuredContent": {
    "code": "issue_locked",
    "message": "Cannot comment: the issue's conversation is locked.",
    "details": {
      "field": "issue_number",
      "value": 1423,
      "reason": "Conversation locked on 2026-07-12T09:30:00Z with reason \"resolved\"."
    },
    "temporary": false,
    "retry_after_ms": null,
    "repair": {
      "next_step": "unlock_then_retry",
      "tool": "github_set_issue_lock",
      "arguments": {"owner": "acme", "repo": "widgets", "issue_number": 1423, "locked": false},
      "alternative": "If unlocking is not appropriate, report to the user that the thread is locked and ask how to proceed."
    },
    "request_id": "req_01J9GH4MK2",
    "fingerprint": "github-issues-mcp@1.0.0+c41d9a2"
  }
}
```

(`details.value` is emitted because `1423` is a server-visible domain identifier arriving where it is valid in shape — known-safe per `[6.offending-value]`. For free-form parameters like `query` or `body`, the server omits `value` under a blanket policy disclosed in the capability summary.)

**Worked payload 2 — rate limited** (transient, retryable, no repair object because the same call unchanged will succeed):

```json
{
  "resultType": "complete",
  "isError": true,
  "content": [
    {"type": "text", "text": "GitHub secondary rate limit hit; retry the same call after 32 seconds."}
  ],
  "structuredContent": {
    "code": "rate_limited",
    "message": "GitHub API rate limit exceeded for search calls.",
    "temporary": true,
    "retry_after_ms": 32000,
    "rate_limit_remaining": 0,
    "request_id": "req_01J9GH7QX8",
    "fingerprint": "github-issues-mcp@1.0.0+c41d9a2"
  }
}
```

**Worked payload 3 — version conflict on update** (permanent for this call, repair preserves intent by routing through the re-read the correction requires — `[6.repair-intent]`, `[6.repair-callable]`):

```json
{
  "resultType": "complete",
  "isError": true,
  "content": [
    {"type": "text", "text": "Issue acme/widgets#1423 changed since you read it (expected_version mismatch). Re-read with github_get_issue, then re-apply your edit with the fresh version token. No changes were made."}
  ],
  "structuredContent": {
    "code": "version_conflict",
    "message": "The issue was modified after the version you read; the update was not applied.",
    "details": {
      "field": "expected_version",
      "value": "v:8c1f2e90",
      "reason": "Current version is v:aa07d113; the issue was updated at 2026-07-29T14:02:11Z."
    },
    "temporary": false,
    "retry_after_ms": null,
    "repair": {
      "next_step": "reread_then_reapply",
      "tool": "github_get_issue",
      "arguments": {"owner": "acme", "repo": "widgets", "issue_number": 1423, "include_comments": false},
      "alternative": "If overwriting concurrent edits is acceptable, re-call github_update_issue without expected_version."
    },
    "request_id": "req_01J9GHA2ZC",
    "fingerprint": "github-issues-mcp@1.0.0+c41d9a2"
  }
}
```

**JSON-RPC carrier example** — `resources/read` of a deleted issue (same envelope, mandated rename):

```json
{
  "jsonrpc": "2.0",
  "id": "req_01J9GHCC41",
  "error": {
    "code": -32602,
    "message": "Resource not found.",
    "data": {
      "machine_code": "issue_not_found",
      "human_message": "Issue acme/widgets#9999 does not exist or is not visible to the configured token.",
      "details": {"field": "uri", "value": "github://repos/acme/widgets/issues/9999", "reason": "No such issue number in this repository."},
      "temporary": false,
      "retry_after_ms": null,
      "repair": {
        "next_step": "search_then_read",
        "tool": "github_find_issues",
        "arguments": {"owner": "acme", "repo": "widgets", "state": "all"}
      },
      "resource_uri": "github://repos/acme/widgets/issues/9999",
      "request_id": "req_01J9GHCC41",
      "fingerprint": "github-issues-mcp@1.0.0+c41d9a2"
    }
  }
}
```

Protocol-level conditions (missing `_meta`, header mismatch, undeclared capability) stay native JSON-RPC (`-32602`/`-32020`/`-32021`) per `[6.tool-errors]`.

## 3. Discovery surface

**Capability summary** — served three ways so every client class gets it: the `github://capabilities` resource, the `github_get_capabilities` tool (guaranteed-visible fallback), and abbreviated in server `instructions` (advisory only, `[2.instructions-advisory]`):

```json
{
  "server": {
    "name": "github-issues-mcp",
    "version": "1.0.0",
    "fingerprint": "github-issues-mcp@1.0.0+c41d9a2",
    "transport": "streamable-http",
    "protocol_target": {"revision": "2026-07-28", "extensions": []},
    "summary": "Find, read, create, edit, comment on, and lock/unlock GitHub issues in repositories the configured token can see."
  },
  "does": [
    "Find issues by structured filters and free-text search (one tool: github_find_issues).",
    "Read an issue with its comment thread; page long threads.",
    "Create issues with labels and assignees; edit title, body, state, assignees, labels.",
    "Comment on issues; lock and unlock conversations.",
    "Optimistic concurrency on edits via version/expected_version tokens."
  ],
  "does_not": [
    "Manage pull requests (excluded from results by default; include_pull_requests=true only surfaces them read-only in find results).",
    "Create, edit, or delete labels or milestones as repository objects.",
    "Delete issues or comments, transfer issues, or manage repositories, projects, or webhooks.",
    "Push notifications of issue changes (request/response only; re-read with updated_after= for freshness)."
  ],
  "readonly_hint_reading": "observable-scope: readOnlyHint tracks whether a call changes state outliving the response; applied consistently across tools",
  "error_carriers": {
    "tool_errors": "structuredContent (isError: true)",
    "non_tool_rpc_errors": "error.data (code/message renamed machine_code/human_message)"
  },
  "error_value_policy": "details.value is emitted only for server-minted tokens, published enum members, and numeric domain ids; omitted for all free-form string parameters (this blanket omission is the disclosed policy).",
  "prerequisites": {
    "auth": {
      "mode": "server-side GitHub token (fine-grained PAT or GitHub App installation)",
      "required_permissions": {"reads": "issues:read", "mutations": "issues:write"},
      "canonical_server_uri": "https://github-issues-mcp.example.com/mcp",
      "resource_indicator": "https://github-issues-mcp.example.com/mcp"
    },
    "failure_codes": {
      "missing_credential": "no GitHub token configured",
      "invalid_credential": "GitHub rejected the configured token",
      "insufficient_scope": "token lacks issues:write or push access; see details.required_scopes"
    },
    "rate_limits": "GitHub primary limit shared across all tools; free-text query calls draw a separate ~30/min search budget. All limit hits return rate_limited with retry_after_ms and rate_limit_remaining.",
    "state_handles": {
      "cursors": "opaque, integrity-protected, 15-minute lifetime; expiry returns cursor_expired with restart guidance; authorization re-checked on every continuation",
      "version_tokens": "opaque content-derived tokens; never expire, compared on write"
    },
    "negotiated_capabilities": {
      "server": ["capabilities.completions"],
      "client": [],
      "fallbacks": {
        "no_completions": "repo_not_found / label_not_found errors carry allowed values or a repair route via github_find_issues"
      }
    }
  }
}
```

**Compact-baseline stance** (`[2.compact-baseline]`, `[2.progressive-disclosure]`): eight tools is small; the whole serialized `tools/list` stays within a CI-enforced byte budget, so **no** `search_tools`/`describe_tool` layer is added — on preloading hosts it would be pure overhead. Client-independent surface reduction was achieved by consolidation (11 endpoints → 8 tools) instead.

**Resources** (secondary browse surface; every read is also reachable via the self-sufficient tools, `[4.tool-fallback]`):

- `github://capabilities` — the summary above. `mimeType: application/json`.
- Templates via `resources/templates/list`:
  - `github://repos/{owner}/{repo}/issues{?state,labels,updated_after}` — issue index; entries carry native `uri`, `name` (`#1423`), `title`, `description` (one-line body preview), `size`, `annotations.lastModified` — index only, no bodies (`[2.index-not-bodies]`).
  - `github://repos/{owner}/{repo}/issues/{issue_number}` — one issue body (same shape as `github_get_issue`'s `issue` object).
- **Completion** (`[2.completion]`, `[4.template-completion]`): server advertises `completions` in `server/discover`; `completion/complete` resolves `{owner}` and `{repo}` from token-visible repositories. Tool arguments are not completable — that gap is covered by validation errors carrying repair.
- **Cache hints** (`[8.cacheable-results]`): `tools/list` `ttlMs: 3600000, cacheScope: "public"` (catalog is identical across principals); `resources/list`/`resources/read` `ttlMs: 30000, cacheScope: "private"` (visibility varies by token); `server/discover` `ttlMs: 3600000, "public"`. No subscriptions capability is advertised — issues mutate outside any session, so freshness is bounded by the short read `ttlMs` plus `updated_after` re-reads, and that trade-off is stated in `does_not`.

**Versioning** (§9): the fingerprint `github-issues-mcp@<semver>+<hash>` covers tools (descriptions included), resources, templates, completion support, error codes, and the capability summary, with a `covers` array disclosing exactly that; it appears in the capability summary and every error envelope. List ordering is deterministic (alphabetical). Deprecations keep the old tool discoverable with `stability: "deprecated"`, `replaced_by`, and migration text until the next major version.

**Prompts**: none. Every workflow here is one or two tool calls; a prompt would be scaffolding with nothing to orchestrate, and none may be load-bearing anyway (`[5.optional]`).

## 4. Checklist coverage (design done-criteria)

| § | Disposition |
| --- | --- |
| §1 | Distinct name, streamable HTTP declared, auth model + three credential failure codes, state-handle discipline for cursors/version tokens, revision target declared, no roots (not workspace-scoped) |
| §2 | Summary with negative scope on three surfaces; compact baseline, no disclosure layer (justified); completion for template variables |
| §3 | 8 task-completing tools, closed schemas, omission semantics per optional field, honest annotations, idempotency keys, version preconditions, observable-scope `readOnlyHint` reading documented |
| §4 | Two templates + capabilities resource; index-not-bodies; no chunking needed (issue bodies capped at 64 KiB; comment paging covers threads); no subscriptions — declared, `ttlMs` bounds staleness |
| §5 | N/A — no prompts; nothing load-bearing lost |
| §6 | Unified envelope, 12-code catalog, worked payloads on both carriers, disclosed `value`-omission policy |
| §7 | N/A — no operation outlives a turn; tasks extension not negotiated (declared in the summary) |
| §8 | Detail toggles (density-only), house pagination in tool payloads, native hints on list methods, explicit truncation with repair, RFC3339 UTC throughout |
| §9 | Fingerprint published with `covers`, deterministic ordering, deprecation semantics defined |

**Owed follow-up** (workflow Steps 8–9): an eval suite over the Step 1 tasks — cold-start, first-repair (inject `issue_locked` and `version_conflict`, assert one-hop repair), `forced_error` per tool asserting carrier/envelope on the wire, `discovery_size_budget` on serialized `tools/list`, and `host_capture` fixtures per target client — before the contract is declared final.
