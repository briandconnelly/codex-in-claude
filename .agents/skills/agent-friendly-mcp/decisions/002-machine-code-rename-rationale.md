# 002 — The `machine_code`/`human_message` rename: real rationale, and whether disclosure waives it

Decided 2026-07-29.
Prompted by issue #131, which was surfaced by an independent evaluation of an agent-friendly-mcp audit of a real server (codex-in-claude).

## Question

The §6 one-envelope table justified the JSON-RPC-side `code`→`machine_code` / `message`→`human_message` rename with "to avoid shadowing native `code`/`message`."
That rationale is checkably false: the envelope rides inside `error.data`, where nothing collides with the native `error.code`/`error.message`.
A rule whose stated reason is false invites disclosed deviations — codex-in-claude ships one, keeping `code`/`message` inside `error.data` and correctly noting that nesting already avoids any collision.
Two questions follow: what does the rename actually buy, and is a *disclosed* decline of it conformant?

## Real rationale

The rename serves two readers, neither of which is a key-collision concern:

- **Serialized-object disambiguation.** An agent reading the flattened error JSON sees `"code"` twice — the numeric JSON-RPC code on `error` and the symbolic string inside `error.data` — and grabbing "the code" can land on either.
  `machine_code` makes the branch key unambiguous for the least-capable realistic reader: a model looking at the payload, not a structural parser.
- **Cross-server parser portability.** Every server following the skill spells the branch key identically, so an agent-side error parser written once works everywhere.

## Positions on disclosed decline

*Disclosed decline is conformant.* A server that names the deviation and its spelling in its capability summary (as codex-in-claude does) keeps its own two carriers more uniform than the mandated spelling.
Rejected: disclosure repairs neither reader above.
The model reading a failure payload may not have the capability summary in context at that moment, and per-server spellings are exactly the vocabulary variance the one-envelope rule exists to eliminate.
§6's only disclosure-sanctioned deviation, `[6.degraded-carrier]`, covers a framework that *cannot* comply; a preferred spelling is not an inability.

*Rename mandatory regardless of disclosure.* Adopted, as `[6.rename]` — the single home for both the rationale and the ruling; the table notes, the envelope intro, `[6.resource-errors]`, and `examples.md` §6 now cite it instead of paraphrasing.

## Verification

Baseline probe (3 reps, neutral conformance-verdict prompt on the old text): all 3 ruled the disclosed decline nonconformant — the "only permitted divergence" phrasing already bound — but all 3 repeated the false shadowing rationale in their audit reasoning, one embellishing it into a collision claim.
Fix probe (3 reps on the new text, same prompt): all 3 ruled nonconformant citing `[6.rename]`, all 3 reproduced the real rationale, none repeated the shadowing claim.
Over-correction control (2 reps): a tool-result error carrying `code`/`message` in `structuredContent` — both reps ruled it conformant, correctly scoping the rename to the JSON-RPC carrier.
