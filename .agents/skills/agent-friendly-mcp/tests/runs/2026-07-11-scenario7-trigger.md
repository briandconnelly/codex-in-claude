# Scenario 7 — Triggering (activation test)

- **Date:** 2026-07-11
- **Tree:** `a3cd37f`
- **Method used:** labeled description-classifier **proxy** (not the host's real selector — this harness exposes no observable skill-load event to instrument). Three independent blind classifier trials (model: Haiku 4.5), each given the same 8 prompts against the repo's 7-skill catalog in a **different randomized order**, blind to the expected answers.
- **Catalog (distractors):** `agent-friendly-mcp` plus `agent-friendly-cli`, `agent-friendly-docs`, `agent-friendly-github`, `agent-bot-identity`, `bundle-uv-mcp-server` (close MCP-packaging distractor), `separating-context-from-constraints`.

## Per-case trigger rate

| Case | Prompt (abbrev.) | Expected | Trial A | Trial B | Trial C | Rate |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | Design MCP server contract for a billing API | fire | mcp | mcp | mcp | 3/3 fire |
| T2 | Review internal Python of an MCP server for style | quiet | none | none | none | 3/3 quiet |
| T3 | Harden input schemas on `deploy`/`rollback` MCP tools | fire | mcp | mcp | mcp | 3/3 fire |
| T4 | Add one optional field to an already agent-friendly server | quiet | none | none | none | 3/3 quiet |
| T5 | Design recovery + progress contract for a 5-min render task | fire | mcp | mcp | mcp | 3/3 fire |
| T6 | Design an operator dashboard for MCP request volume | quiet | none | none | none | 3/3 quiet |
| T7 | Write client code calling `incidents://active` on someone else's server | quiet | none | none | none | 3/3 quiet |
| T8 | "agents keep picking the wrong tool out of the 60 we expose" | fire | mcp | mcp | mcp | 3/3 fire |

## Scoring

- **Trigger recall (T1, T3, T5, T8):** 4/4 cases fired — **12/12 trials.** PASS.
- **Trigger precision (T2, T4, T6, T7):** 4/4 cases stayed quiet — **12/12 trials.** PASS.
- **Recorded:** proxy method (not host selector); model Haiku 4.5; 3 trials/case; randomized catalog order per trial.

## Notes

- The two anticipated description weaknesses did **not** appear:
  - T8, a symptom prompt with no literal "MCP server" phrase, fired in every trial — the description's symptom list ("agents picking the wrong tool from many candidates") carried the match.
  - T2 (internal-code review) and T7 (consuming an existing server as a client) stayed quiet in every trial — no over-match on the bare keyword "MCP".
- Limitation: this is a classifier **proxy**, not the host's real dispatcher, and does not prove the host loads the skill in a live session; it measures whether the `description` is discriminative at the exclusion boundaries. Re-run against a host-native skill-load signal when one is available.
- The `bundle-uv-mcp-server` distractor was present but not stress-tested by a packaging-shaped prompt; a future trial could add one (e.g. "package our MCP server as an .mcpb bundle") to probe that boundary.
