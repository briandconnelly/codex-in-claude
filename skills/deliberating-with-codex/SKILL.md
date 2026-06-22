---
name: deliberating-with-codex
description: Use when deciding whether—and how—to compose Claude and Codex into a deliberate multi-model pattern instead of a single one-off call. Triggers — "get a second model to judge my draft", "have Claude and Codex both attempt then synthesize", "run a review–revise loop with Codex", "is this worth orchestrating two models", cross-checking a high-stakes design or change, or wanting structured cross-model deliberation. For which single tool to call and the safety contract, see the `collaborating-with-codex` skill.
---

# Deliberating with Codex

`collaborating-with-codex` tells you **which** Codex tool to call and how to stay
safe. This skill is about **composing** those tools with your own work into a
small two-model system — Claude (you) plus Codex — for decisions where one model's
answer isn't enough. It assumes you've read the tool reference; it does not repeat
the per-tool contract or guardrails.

The scope is a deliberate ceiling: **two models, not an N-model panel.** This
plugin's server can only drive the `codex` CLI — it cannot invoke Claude — so the
only source of cross-architecture diversity is *you*, by bringing your own attempt
to the table. Everything below is built from the existing tools; nothing here adds
MCP surface.

## Default: don't orchestrate

**A single `codex_consult` is the default.** Orchestration is opt-in and
exceptional — it multiplies token spend and latency, and every pattern below still
sends your context to OpenAI on each active call. Reach for a pattern only when the
**value/risk gate** clears:

- **Stakes are high** — a hard-to-reverse change, a load-bearing design decision, a
  security- or data-integrity-sensitive diff.
- **A single opinion is genuinely insufficient** — you've already got one take and
  the disagreement (or your own uncertainty) is what's blocking the decision.
- **You can act on the result** — you have the context to verify and synthesize, not
  just collect more text.

If those don't all hold, make one `codex_consult` call (or none) and move on.

## The three patterns

| You have… | and you want… | Pattern | Tool | Codex calls |
|-----------|---------------|---------|------|-------------|
| A draft / diff / design | an independent critique of *it* | **Judge** | `codex_review_changes` (diffs) · `codex_consult` (designs) | 1 |
| A problem, no committed answer | two independent attempts, then merge | **Two-member panel** | `codex_consult` (design) · `codex_delegate` (diff) | 1 |
| A draft you'll iterate | one critique-and-revise pass | **Review–Revise loop** | `codex_review_changes` or `codex_consult` | 1 (≤2 high-risk) |

### 1. Judge — Codex critiques your work

You produce the artifact; Codex evaluates it. Use `codex_review_changes` to judge a
diff (set `scope`/`paths`) and `codex_consult` to judge a design or written
argument.

- **The verdict is signal, not truth.** A `codex_review_changes` `verdict`
  (pass/concerns/fail) and any `codex_consult` finding are claims to verify against
  the code — not a passing grade. `consult`/`review` are read-only and **static**:
  the sandbox blocks the writes a test/build/lint run needs, so a "this breaks X"
  finding was *not* validated by running X. Run the check yourself.
- **Cap: 1 Codex call.** Judge is a single decision point, not a conversation.

### 2. Two-member panel — both attempt, you synthesize

You and Codex each attempt the *same* problem independently, then **you**
synthesize. This is the only pattern that buys real cross-model diversity, and it
buys it only if independence is preserved:

- **Hand Codex a neutral task prompt — never your draft.** The moment Codex sees
  your attempt, it stops being an independent member and the panel silently
  degrades into Judge (correlated failure, false independence). If you want it to
  react to your draft, that's Judge — call it that and use pattern 1.
- **Operational independence:** Codex works from the same task statement you did,
  not your reasoning, partial solution, or conclusion.
- **Split by artifact:** `codex_consult` for a *design* panel (two approaches to
  compare), `codex_delegate` for a *diff* panel (two implementations — Codex's lands
  in a throwaway worktree as a `diff`, never applied).
- **Diff-panel precondition — delegate *before* you draft.** `codex_delegate` seeds
  its worktree from your live tracked state (`HEAD` + uncommitted *tracked* changes),
  so if your own attempt already lives in tracked files, Codex sees it and the panel
  silently degrades into Judge. Run the Codex member first, or keep your draft out
  of the tracked baseline (stash it, or work it on a separate branch) so Codex starts
  from the same clean baseline you did.
- **Cap: 1 Codex attempt.** Two members total: yours and Codex's.

### 3. Review–Revise loop — one critique pass

You draft → Codex reviews → you revise. This is a **critique step that improves the
work, not a verification step that certifies it.** A clean second pass means Codex
found nothing more to say given your shared framing — not that the work is correct.

- **Cap: 1 Codex call.** A second pass is allowed *only* for an explicitly
  high-risk change and *only* when declared upfront (≤2 total). Anything beyond that
  is the loop the base skill's "do not call Codex in a loop" guardrail forbids — it
  re-creates autocomplete-by-Codex and burns spend without adding independence.
- Caps are counted in **total Codex calls**, deliberately — not in "re-reviews,"
  which is the ambiguous framing that lets a loop creep back in.

## False agreement is weak evidence

Both models are reasoning from **your framing** — the prompt, the file you pointed
at, the way you scoped the problem. When Codex agrees with you, that is at least as
likely to mean *you both inherited the same blind spot* as it is to mean you're
right. Treat agreement as the **absence of a caught error**, not as confirmation.
Disagreement is the more useful outcome: it localizes exactly where the framing or
the facts need checking. Don't run a pattern to manufacture a second "yes."

## Preflight: scope and safety

Before any pattern:

- **Scope the input.** Narrow `paths`/`scope` (review) or trim `extra_context`
  (consult) to what the decision needs. Large bundles are byte-bounded by the plugin
  before the call, but bounding is a backstop, not scoping — a truncated diff yields
  a worse judgment.
- **Preview where you can.** `codex_review_changes` and `codex_delegate` have free
  dry-runs (`codex_dry_run`, `codex_delegate_dry_run`) — use them to confirm scope
  and size before spending. **There is no consult dry-run**, so a Judge- or
  panel-via-`codex_consult` call can't be previewed the same way; rely on manual
  scoping and input bounding instead.
- **Secrets/safety carry over unchanged** from `collaborating-with-codex`: Codex can
  read files during a review/delegate, redaction is defense-in-depth not a
  guarantee, and `delegate` only ever writes inside a throwaway worktree.

## Synthesizing the result

Patterns 1 and 2 leave *you* holding the synthesis. A `codex_consult` or
`codex_delegate` member carries **no verdict**; a `codex_review_changes` Judge
returns a tool `verdict`/`confidence`, but that's *signal toward* your decision, not
the decision — the call is still yours to form. Before you commit:

- **Verify, don't tally.** Check each load-bearing finding against the actual code
  or design. A confident-but-wrong claim from either model survives a vote count;
  it doesn't survive running the test.
- **Name the disagreements.** Where you and Codex diverge, decide explicitly and
  record why — that's where the deliberation earned its cost.
- **Keep the synthesis schema-compatible.** When you ask `codex_consult` to judge or
  to be a panel member, its structured output is the fixed consult shape
  (`summary` + `findings`/`questions`/`assumptions`/`next_steps`, closed schema) —
  there is **no** top-level `analysis`/`consensus`/`contradictions` field, and asking
  for one gets you free-form text inside `summary`, not a guaranteed structure. Put
  named synthesis sections in `summary` and tie concrete issues to evidence in
  `findings`. Do the consensus/contradiction/blind-spot synthesis yourself.
- **Decide and stop.** The output is decision support. Once you can act, act — don't
  spawn another pattern to feel more certain.

## Hard bounds (at a glance)

- Single `codex_consult` is the default; orchestration is opt-in and gated.
- Judge = **1** Codex call. Panel = **1** Codex attempt. Review–Revise = **1**
  (high-risk **≤2**, declared upfront).
- Panel members are independent — Codex gets a neutral prompt, never your draft.
- Findings/verdicts are claims to verify; read-only calls are static, not tests.
- Agreement is weak evidence; disagreement is the useful signal.

See `collaborating-with-codex` for the tool-by-tool contract, the result envelope,
background jobs, the server-down fallback, and the full guardrail list — that skill
stays the reference and guardrail home; this one only composes its tools.
