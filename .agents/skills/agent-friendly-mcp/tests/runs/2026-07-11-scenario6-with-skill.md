# Scenario 6 (Prompts) — with-skill run

- **Date:** 2026-07-11
- **Tree:** `d586ce3` (main, post review-batch #53)
- **Mode:** with-skill (fresh general-purpose subagent; read `SKILL.md` + all seven `references/` files, explicitly skipped `tests/`; 8 tool-uses)
- **Score:** 7/7

## Exact prompt given

Same `open_incident` prompt-design prompt as the baseline (see `2026-07-11-scenario6-baseline.md`), preceded by an instruction to read `agent-friendly-mcp/SKILL.md` and every file under `references/` as authoritative guidance, and **not** to read anything under `tests/`.

## Per-assertion scoring

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| A1 | References follow-on tools/resource by canonical name, does not redefine their contracts | **PASS** | Names all three tools + `incidents://active`; "defines **no** argument shapes, side effects, error codes, or idempotency semantics of its own … references them by name and must not redefine them." |
| A2 | Prerequisites declared (tools, resources, permission/context assumptions) | **PASS** | `_meta.com.incident-mcp/prerequisites` lists `tools`, `resources`, `permissions` (`incident:write`, `responder:page`), a `capabilities` note (completions + fallback), and `context_assumptions` (duplicate-check). |
| A3 | No load-bearing behavior in prompt text (audit; shapes/side-effects/errors in schemas) | **PASS** | Explicitly runs the §5 audit: "Removing the prompt entirely must leave [the tools + resource] fully correct and callable"; "delete `open_incident` and nothing breaks." Side effects/idempotency/errors assigned to the tool schemas. |
| A4 | Only native `PromptArgument` fields (`name`, `title`, `description`, `required`); value-shape guidance in `description` | **PASS** | Arguments use only native fields; "value-shape guidance goes in each `description`, never as `type`/`items`/`default`." Severity value-set explicitly deferred to `incident_create`'s schema. |
| A5 | Convention metadata under a **namespaced** `_meta` key, never top-level `Prompt` fields | **PASS** | "all convention metadata rides under a namespaced `_meta` key so the block is wire-valid" — `com.incident-mcp/when-to-use`, `/prerequisites`, `/expected-followups`, `/notes`; top level is native-only. |
| A6 | Completion for arguments with dynamic value sets where negotiated | **PASS** | "request completion for this argument" (severity) and "Request completion for these values rather than guessing handles" (responders/commander); prerequisites note `completion/complete` gated on `server.capabilities.completions` with a schema-read fallback. |
| A7 | States explicitly the prompt is optional scaffolding; schema-only clients still call every tool correctly | **PASS** | "a prompt is **advisory orchestration scaffolding, not contract**"; "any client that bypasses the prompt still calls the tool"; the delete-and-nothing-breaks test. |

**Result: 7/7.**
The two baseline gaps close — a declared prerequisites block (A2) and completion on dynamic arguments (A6) — and the run actively demonstrates the disciplines the baseline only skirted: namespaced `_meta` for all convention metadata (A5), the explicit "remove every prompt" audit (A3), and the optional-scaffolding framing (A7).
It also correctly pushes `idempotency_key`, the structured error envelope, dispatched-vs-applied paging, and the `incidents://active` index shape into the tool/resource schemas rather than the prompt.

## Full final output

Verbatim final message returned by the with-skill subagent (the 8 tool calls were the skill-file reads it was instructed to perform; no other tool noise):

I have everything I need.
Here's the complete design.

---

# Design: `open_incident` prompt

## 1. Where this sits in the contract (the load-bearing decision)

Per SKILL §5 and the checklist, a prompt is **advisory orchestration scaffolding, not contract**.
So before writing it, I fixed the boundary: this prompt names the three existing tools and one resource and sequences them — it defines **no** argument shapes, side effects, error codes, or idempotency semantics of its own.
Removing the prompt entirely must leave `incident_create`, `incident_add_responder`, `incident_post_update`, and `incidents://active` fully correct and callable (the §5 audit prompt).
Everything below respects that.

Native `Prompt` fields only at the top level (`name`, `title`, `description`, `arguments`, `_meta`); all convention metadata rides under a namespaced `_meta` key so the block is wire-valid (SKILL "Native Fields vs Convention Extensions", ex§5).
Inside `arguments`, only native `PromptArgument` fields appear (`name`, `title`, `description`, `required`) — value-shape guidance goes in each `description`, never as `type`/`items`/`default`, exactly as ex§5 does.

## 2. The prompt definition

```json
{
  "name": "open_incident",
  "title": "Declare a new incident",
  "description": "Run the 'declare a new incident' workflow: create the incident record, page the initial responders, and post the first status update. Use when a user is reporting a live, unmanaged problem and wants an incident opened now. Do NOT use to look up, update, or comment on an incident that already exists (browse `incidents://active` and call `incident_post_update` directly for that), and do NOT use for a post-incident writeup or a hypothetical drill.",
  "arguments": [
    {
      "name": "title",
      "title": "Incident title",
      "description": "Short human-readable incident title, e.g. \"Checkout API returning 500s\". One line; the operator-facing summary, not the full description.",
      "required": true
    },
    {
      "name": "severity",
      "title": "Severity",
      "description": "Severity level for the new incident. The accepted value set is defined by `incident_create`'s inputSchema, not by this prompt — request completion for this argument, or read the enum from that tool's schema, rather than guessing. If the user gave an impact but no level, propose one and confirm before creating.",
      "required": true
    },
    {
      "name": "summary",
      "title": "What is happening",
      "description": "One or two sentences describing observed impact and current symptoms. Becomes the body of the incident record and the first status update.",
      "required": true
    },
    {
      "name": "responders",
      "title": "Responders to page",
      "description": "Optional list of responders to add after creation, by the identifier `incident_add_responder` accepts (user id or on-call handle, per that tool's schema). Omit to create the incident with no responders yet; the agent may then consult the on-call rotation and confirm before paging. Request completion for these values rather than guessing handles.",
      "required": false
    },
    {
      "name": "commander",
      "title": "Incident commander",
      "description": "Optional identifier of the responder to designate as incident commander. Omit if the user has not named one; do not invent a commander.",
      "required": false
    }
  ],
  "_meta": {
    "com.incident-mcp/when-to-use": "The user is reporting a live problem and wants an incident opened right now. Skip this prompt if the incident already exists (act on it directly), if the user only wants to check active incidents (read `incidents://active`), or if they want a retrospective document.",
    "com.incident-mcp/prerequisites": {
      "tools": ["incident_create", "incident_add_responder", "incident_post_update"],
      "resources": ["incidents://active"],
      "permissions": ["incident:write", "responder:page"],
      "capabilities": {
        "completion": "server.capabilities.completions enables completion/complete for the `severity`, `responders`, and `commander` arguments; without it, read the accepted values from each tool's inputSchema and repair from field-level errors instead."
      },
      "context_assumptions": {
        "duplicate_check": "Before creating, read `incidents://active` and confirm no open incident already covers this impact; if one does, prefer updating it via `incident_post_update` over declaring a duplicate."
      }
    },
    "com.incident-mcp/expected-followups": [
      "Read `incidents://active` first and confirm this is not a duplicate of an already-open incident.",
      "Call `incident_create` with `title`, `severity`, and `summary` to open the record; capture the returned incident id from its structured response.",
      "For each entry in `responders` (and `commander` if given), call `incident_add_responder` with that incident id. Confirm before paging when the user did not explicitly name the responder.",
      "Call `incident_post_update` with the incident id and `summary` to publish the initial status update.",
      "Surface the new incident id and its link/permalink back to the user.",
      "On any tool error, branch on the structured error code and follow its `repair` hint — do not re-run `incident_create` blind, or you risk a duplicate incident."
    ],
    "com.incident-mcp/notes": "Advisory scaffolding only. Argument shapes, required fields, severity enum, side effects, idempotency, and error codes are defined by the three tools' schemas and are authoritative; this prompt references them by name and must not redefine them."
  }
}
```

## 3. What belongs in the prompt vs. the tool/resource schemas

**Belongs in the prompt (orchestration only):**
- *When to use / when not to use* — recognizing "declare a new incident" as the matching task and steering away from duplicate-declaration, lookup, and retrospective cases (§5 "state when to use explicitly"; ex§5 `when-to-use`).
- *Ordering* — create → add responders → post update, and read `incidents://active` first to avoid duplicates.
  Sequencing across tools is exactly what a prompt is for; no single tool owns it.
- *Prerequisites in one place* — which tools, which resource, which permissions, which optional capability (`completions`) and its fallback (§5 "list prerequisites"; §1 negotiated-capability declaration).
- *Follow-on tools by name* — `incident_create`, `incident_add_responder`, `incident_post_update` referenced, never redefined (§5 "reference expected follow-on tools by name").
- *Confirmation-before-paging judgment* — soft guidance the agent applies; it is not enforcement.

**Belongs in the tool schemas (authoritative contract, §3):**
- The real argument shapes and required/optional discipline: `incident_create`'s exact fields, the **severity enum** (the prompt only points at it — the value set lives in `incident_create.inputSchema`), disambiguated id parameter names, `additionalProperties: false`.
- **Side effects and idempotency** via honest annotations: all three are mutating (`readOnlyHint: false`). `incident_create` should carry an `idempotency_key` with a declared dedup window (§3 "give unsafe mutations an idempotency key") — creating a duplicate incident on a timeout retry is exactly the costly case.
  That safety net cannot live in prompt prose, because any client that bypasses the prompt still calls the tool.
- **The structured error envelope** (§6): `not_found` for a bad incident id, `invalid_field` for a bad severity with the allowed values, `insufficient_scope` vs missing credential, each with a `repair` object.
  The prompt's "follow the repair hint" line is worthless unless the tools actually emit these.
- **Dispatched-vs-applied** for the paging call (§3): if `incident_add_responder` dispatches a page that may not be confirmed synchronously, its result carries `command_sent` / `status` / `follow_up` — a tool-level contract, not prompt text.
- Which handle/identifier format `incident_add_responder` accepts, and completion for `severity`/`responders`/`commander` via `completion/complete` (§2/§5) — the prompt advertises that completion exists; the server implements it.

**Belongs in the resource schema (§4):**
- `incidents://active` as a stable, hierarchical URI returning an **index** (id, title, severity, status, `lastModified`) sufficient to triage for duplicates without fetching full bodies.
  If it can change during a long incident session, it should support `resources/subscribe` + `notifications/resources/updated`.
  The prompt merely tells the agent to read it first; the resource defines what a read returns.

**The test I held it to:** delete `open_incident` and nothing breaks — the three tools and the resource remain independently correct, discoverable, and safe to call.
That is the §5 line between scaffolding and contract, and this design stays on the scaffolding side of it.
