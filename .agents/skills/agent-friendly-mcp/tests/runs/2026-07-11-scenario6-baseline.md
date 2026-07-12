# Scenario 6 (Prompts) — baseline run

- **Date:** 2026-07-11
- **Tree:** `d586ce3` (main, post review-batch #53)
- **Mode:** baseline (fresh general-purpose subagent, prompt only, forbidden from reading the repo/skill; 0 tool-uses confirms no skill access)
- **Score:** 5/7

## Exact prompt given

> Design a reusable MCP prompt named `open_incident` for a server that wraps an incident-management service.
> Its tools already exist: `incident_create`, `incident_add_responder`, `incident_post_update`, plus a resource `incidents://active`.
> The prompt should help an agent run the "declare a new incident" workflow.
> Produce: the prompt definition (name, arguments, and any orchestration text), and a short note on what belongs in the prompt versus what belongs in the tool and resource schemas.

(Wrapped with a guardrail forbidding reading any repo file, especially under `agent-friendly-mcp/`.)

## Per-assertion scoring

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| A1 | References follow-on tools/resource by canonical name, does not redefine their contracts | **PASS** | Workflow names `incident_create`, `incident_add_responder`, `incident_post_update`, `incidents://active`; "What belongs where" keeps field structure/enums in the tool schemas, not the prompt. |
| A2 | Prerequisites declared (which tools, resources, permission/context assumptions) | **FAIL** | No prerequisites declaration — tools are named inside the workflow prose, but there is no explicit list of required tools/resources or permission/context assumptions the prompt relies on. |
| A3 | No load-bearing behavior in prompt text (audit; shapes/side-effects/errors in schemas) | **PASS** | "prompts encode the workflow …; schemas encode the contract"; return shape "must actually be in the tool's output schema"; per-tool hints "let the agent reason about a tool even outside this workflow." |
| A4 | Only native `PromptArgument` fields (`name`, `title`, `description`, `required`); value-shape guidance in `description` | **PASS** | Arguments use only `name`/`description`/`required`; the severity enum and value shapes are described in prose, no `type`/`items`/`default` keys. |
| A5 | Convention metadata under a **namespaced** `_meta` key, never top-level `Prompt` fields | **PASS** (by omission) | Uses only native `title`/`description`/`arguments`; folds when-to-use into `description`; adds no top-level convention fields. It does not add prerequisites/expected-followups at all (hence A2), so it never demonstrates the `_meta` pattern — but it also never violates it. |
| A6 | Completion for arguments with dynamic value sets where negotiated | **FAIL** | No `completion/complete` offered for `affected_service`/`reporter` (values the agent should not guess). |
| A7 | States explicitly the prompt is optional scaffolding; schema-only clients still call every tool correctly | **PASS** | "field structure … so it holds for every caller, including ones not using this prompt"; the whole "What belongs where" test — data-constraints in schemas so they hold "for every caller." |

**Passed:** A1, A3, A4, A5, A7 (5/7).
A strong baseline — the workflow-vs-contract split is articulated well, and it correctly avoids non-native argument keys and load-bearing prompt behavior.
The skill-sensitive gaps are the two §5 discovery/declaration disciplines: a declared prerequisites block (A2) and completion on dynamic arguments (A6).

## Full final output

Verbatim final message returned by the baseline subagent (no tool noise to elide — the run made 0 tool calls):

# MCP Prompt Design: `open_incident`

## The prompt definition

An MCP prompt is a server-defined, user-controlled template.
The server advertises it in `prompts/list` with a name, description, and an `arguments` array; the client fetches the filled-in version via `prompts/get` with argument values.
The result is a list of `messages` that get injected into the conversation to steer the agent.
The prompt itself contains **no tool calls** — it produces guidance text; the agent decides which tools to invoke.

### `prompts/list` entry

```json
{
  "name": "open_incident",
  "title": "Declare a new incident",
  "description": "Run the declare-a-new-incident workflow: create the incident record, page the right responders, and post the initial status update. Use this when a user reports a live outage or degradation and wants an incident opened.",
  "arguments": [
    {
      "name": "summary",
      "description": "One-line description of what is broken, from the user's perspective (e.g. 'Checkout API returning 5xx for ~30% of requests').",
      "required": true
    },
    {
      "name": "severity",
      "description": "Proposed severity: SEV1 (critical/total outage), SEV2 (major degradation), SEV3 (minor). If unsure, leave blank and the workflow will help you infer it.",
      "required": false
    },
    {
      "name": "affected_service",
      "description": "Name of the primary service or component believed to be impacted.",
      "required": false
    },
    {
      "name": "reporter",
      "description": "Who reported the incident (person, alert source, or 'automation').",
      "required": false
    }
  ]
}
```

### `prompts/get` result (orchestration text)

This is what the server returns when the client requests the prompt with arguments bound.
Note the templated argument substitution (`{{summary}}`, etc.) — the server fills these in before returning.

```json
{
  "description": "Declare a new incident: create, staff, and announce.",
  "messages": [
    {
      "role": "user",
      "content": {
        "type": "text",
        "text": "You are helping an on-call engineer declare a new incident using the incident-management server. Run the workflow below. Do not fabricate values — if a required detail is missing, ask the user one concise question before proceeding.\n\nReported incident:\n- Summary: {{summary}}\n- Proposed severity: {{severity}}\n- Affected service: {{affected_service}}\n- Reporter: {{reporter}}\n\nWorkflow:\n\n1. Check for duplicates first. Read the `incidents://active` resource and compare open incidents against this summary and affected service. If one plausibly already covers this, STOP and tell the user which existing incident it is, and ask whether to add to it (via `incident_post_update`) instead of opening a new one.\n\n2. Settle severity. If `severity` was not provided or is uncertain, propose one to the user and confirm before creating. SEV1 = critical/total outage or data loss; SEV2 = major degradation with a viable workaround; SEV3 = minor/localized. Do not silently default.\n\n3. Create the incident with `incident_create`, passing the summary, agreed severity, and affected service. Capture the returned incident ID — every later step references it.\n\n4. Staff it. Based on the affected service and severity, call `incident_add_responder` for the appropriate on-call role(s): at minimum an incident commander for SEV1/SEV2. If you don't know who owns the service, ask the user rather than guessing an identity.\n\n5. Post the opening update with `incident_post_update` on the new incident ID: state that the incident is declared, its severity, current known impact, and that investigation is beginning. Keep it factual — no speculation about root cause.\n\n6. Report back to the user: the incident ID, severity, who was paged, and a link or reference if the create call returned one. List any step you skipped and why.\n\nOperate one step at a time. Confirm success of each tool call before moving to the next, and surface any error rather than continuing past it."
      }
    }
  ]
}
```

(If the server supports it, wrap the tool names in an `assistant` priming turn or leave a second message stubbing the expected first action — but keeping it to a single `user` message is the most portable, since not all clients render multi-turn prompt results well.)

---

## What belongs where

The dividing principle: **prompts encode the workflow (order, judgment, when-to-stop); schemas encode the contract (shape, validation, enumerations).** Don't duplicate one in the other.

**Belongs in the prompt (`open_incident`):**
- **Sequencing and orchestration** — dedupe-check *before* create, create *before* responder/update, because IDs flow forward.
  Tool schemas can't express "call B only after A."
- **Judgment and gating** — inferring severity, confirming with the user, stopping on a likely duplicate, refusing to invent responder identities.
  This is soft policy, not machine-validatable structure.
- **Cross-tool/resource choreography** — the fact that `incidents://active` should be consulted first and that all three tools compose into one human intent ("declare an incident").
- **Tone/content norms for free text** — "factual, no root-cause speculation" in the update.
  A schema can require a `message` string but can't enforce that it's non-speculative.
- **Sensible defaults for missing args** — surfaced as questions to the user, not baked into data.

**Belongs in the tool schemas (`incident_create`, etc.):**
- **Field structure and types** — `summary: string`, `severity: enum[SEV1,SEV2,SEV3]`, `affected_service`, `incident_id` on the follow-up calls.
- **Required vs optional per call**, min/max lengths, formats, and the authoritative **severity enum** (the prompt describes what each level *means*; the schema enforces the allowed *values*).
- **Per-tool `description`/annotations** — e.g. `incident_add_responder` is idempotent-ish, `incident_post_update` mutates an existing record; read-only/destructive hints.
  These let the agent reason about a tool even outside this workflow.
- **Return shape** — the incident ID and any URL the prompt tells the agent to capture must actually be in the tool's output schema.

**Belongs in the resource (`incidents://active`):**
- The **live list of open incidents** and their fields (id, summary, service, severity, status).
  The prompt *tells the agent to read it* for dedupe; the resource *defines what's in it*.
  Keep the dedupe heuristic in the prompt and the data in the resource.

The test for each rule: if it constrains the *data*, it goes in a schema (so it holds for every caller, including ones not using this prompt); if it constrains the *process or judgment*, it goes in the prompt.
