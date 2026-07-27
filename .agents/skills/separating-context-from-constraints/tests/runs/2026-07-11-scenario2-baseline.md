# Scenario 2 — Baseline Scoring

Date: 2026-07-11
Run: baseline without the skill.

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| 1 | Accept legitimate inline constraints | FAIL | It demands a dedicated policy or constraints section for the compact description. |
| 2 | Avoid invented R1–R5 findings | FAIL | It invents confirmation, schema, output, and fail-closed requirements. |
| 3 | Report a clean outcome | FAIL | It reports numerous findings. |

Total: 0/3.

## Scored Output

## Audit

The document mixes context, behavioral constraints, input validation, safety policy, and output guarantees inside the `description`.

### Context

- “Archive a Slack channel” clearly states the tool’s purpose.

### Constraints

- “MUST NOT archive a channel with more than one active member without explicit confirmation from the caller” is a safety constraint.
- “Accepts only a `channel_id`, never a channel name” is an input constraint.
- `additionalProperties: false` and `required: ["channel_id"]` are machine-enforced schema constraints.
- The success and error return formats are output-contract constraints, not contextual description.

### Findings

- Move the safety rule into a dedicated policy or constraints section so it is prominent and independently testable.
- Define what counts as an “active member,” including whether bots, guests, deactivated users, and the caller count.
- Define how confirmation is obtained and bound to the specific channel and observed member count.
- The optional `confirmed` boolean is vulnerable to stale or generic confirmation unless the implementation verifies the current membership immediately before archiving.
- State explicitly that `confirmed: true` is required when the channel has two or more active members and ignored or rejected otherwise.
- The claim that only a channel ID is accepted is partly enforced by the schema, but `channel_id` should also have constraints such as `minLength` and an appropriate Slack channel-ID pattern.
- Add a formal output schema instead of leaving `{archived: true, channel_id}` only in prose.
- Define the structured error shape and expected error codes, including invalid ID, channel not found, insufficient permission, confirmation required, and membership lookup failure.
- Specify fail-closed behavior when the active-member count cannot be determined.
- Keep the description focused on purpose, while locating enforceable input, authorization, confirmation, and output requirements in explicit schema or policy fields.
