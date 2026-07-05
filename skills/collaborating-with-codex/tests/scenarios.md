# Test Scenarios for collaborating-with-codex

Behavioral test scenarios for this skill, following the baseline/with-skill methodology used by `.agents/skills/agent-friendly-mcp/tests/scenarios.md`: run each scenario with a fresh subagent that does NOT have the new wording (baseline), then with the new wording (treatment), and compare against the assertions.
A baseline run that already satisfies every assertion means the scenario is too easy for that model — the treatment run then only confirms *no regression*, which is still the ship gate (#221: "if the new wording performs worse than baseline on any scenario, fix the wording before opening the PR").
An assertion the treatment run misses is a finding against the skill, not against the agent.

## How to run

1. **Baseline:** dispatch a fresh subagent with the OLD wording (the pre-restructure description / Knobs paragraph) and the scenario prompt. Record which assertions its output satisfies.
2. **Treatment:** dispatch a fresh subagent with the NEW wording and the same prompt.
3. **Score:** every assertion is pass/fail with a one-line evidence pointer into the transcript. Record results in the table at the bottom.

Scenarios 1 and 2 target the two wording changes that alter agent-facing behavior signals (the trimmed frontmatter description; the retry rule extracted out of the Knobs run-on paragraph), so they are run baseline-vs-new. Scenarios 3 and 4 target behaviors the restructure moved but did not reword materially (the `workspace_root` guardrail; the delegated-diff review rule); they are run once against the restructured text to confirm the move preserved the behavior.

## Scenario 1: Trigger accuracy (description test)

Tests the trimmed frontmatter `description`: does an agent still load the skill on a self-initiated decision point, and correctly NOT load it on an unrelated mechanical task?

**Prompt:** Present the agent with the skill's `description` field and two independent situations; ask LOAD or SKIP for each.

- SITUATION A (must LOAD): mid-debugging, a second consecutive fix for the same bug just failed, about to attempt a third — no one mentioned Codex.
- SITUATION B (must SKIP): "Add a docstring to `parse_config` and rename it to `load_config` across the repo" — a straightforward mechanical edit.

**Assertions (treatment run must satisfy):**

- [ ] A → LOAD (matches the "second consecutive fix failed, before attempt three" self-initiated trigger, which the trimmed description preserves).
- [ ] B → SKIP (no viable-approach fork, no failed debugging, no risk — no trigger fires; the description does not over-fire on ordinary edits).

**Expected baseline failures:** none guaranteed — the OLD (longer) description carries the same triggers, so the baseline is expected to pass too. The point of the treatment run is to prove the trim did not drop the trigger or start over-firing.

## Scenario 2: Idempotent retry (Knobs retry-rule test)

Tests the retry rule after it was extracted from the ~180-word Knobs run-on paragraph into its own bolded bullet: after a transport drop mid-`codex_consult`, does the agent retry the SAME tool with the SAME `idempotency_key`, and refuse to switch to `codex_consult_async` with that key?

**Prompt:** Give the agent the Knobs section (OLD run-on paragraph for baseline, NEW bulleted form for treatment). Situation: `codex_consult(question=..., idempotency_key="k1")` dropped with "Connection closed" mid-call; you want to retry without paying twice; a teammate suggests switching to `codex_consult_async` with `idempotency_key="k1"` to background it.

**Assertions (treatment run must satisfy):**

- [ ] Retries `codex_consult` (the SAME concrete tool), not a different tool.
- [ ] Passes the same `idempotency_key` (`k1`) so the existing run replays instead of paying for a duplicate.
- [ ] Declines the async switch: the key is tool-scoped, so a sync call's key never replays via the `_async` variant.

**Expected baseline failures:** the rule is present in the OLD text too (just buried), so a capable model may still extract it; the treatment run proves surfacing it as a bolded bullet does not regress the behavior.

## Scenario 3: Workspace selection (workspace_root guardrail test)

Tests the `workspace_root` guardrail (as corrected by #220 and moved into Guardrails by #221): optional for a pure-Q&A consult, absolute path for a repo-grounded call.

**Prompt:** Give the agent the `workspace_root` guardrail bullet and two situations.

- SITUATION A: a general design question with no repo reference ("what are the tradeoffs between optimistic and pessimistic locking?") via `codex_consult`.
- SITUATION B: review the uncommitted changes in the repo at an absolute path via `codex_review_changes`.

**Assertions (treatment run must satisfy):**

- [ ] A → `workspace_root` OPTIONAL (pure Q&A that needs no codebase).
- [ ] B → PASS an absolute `workspace_root` (repo-grounded review targets a specific repository).

## Scenario 4: Delegated-diff handling (decider-rule test)

Tests the split guardrail "Never apply a delegated diff without reviewing it": does the agent review a returned diff before applying, and know the plugin never auto-applied it to the working tree?

**Prompt:** Give the agent the `codex_delegate` bullet and the split guardrail. Situation: `codex_delegate` returned a plausible-looking `diff` and the task list is long.

**Assertions (treatment run must satisfy):**

- [ ] ACTION → REVIEW_FIRST (does not auto-apply on a glance-plausible diff).
- [ ] Knows the diff was NOT applied to the working tree by the plugin (delegate returns a proposal in an isolated worktree).

## Results

| Date | Scenario | Run | Assertions passed | Notes |
| --- | --- | --- | --- | --- |
| 2026-07-05 | 1 (trigger accuracy) | baseline (OLD description) | 2/2 | A→LOAD ("second consecutive fix failed, consult before attempt three"); B→SKIP ("mechanical edit, no second opinion needed"). |
| 2026-07-05 | 1 (trigger accuracy) | treatment (NEW description) | 2/2 | A→LOAD ("matches failed-second-fix, before-attempt-three trigger"); B→SKIP ("trivial mechanical edit, no trigger"). No regression — the trim kept the trigger and did not start over-firing. |
| 2026-07-05 | 2 (idempotent retry) | baseline (OLD Knobs paragraph) | 3/3 | TOOL=`codex_consult`; KEY=`k1`; async switch = NO ("key is tool-scoped; async won't replay the sync run"). The buried rule was still recovered by this model. |
| 2026-07-05 | 2 (idempotent retry) | treatment (NEW retry bullet) | 3/3 | TOOL=`codex_consult`; KEY=`k1`; async switch = NO ("key is tool-scoped; async won't replay sync's run"). No regression — extracting the rule to a bolded bullet preserved correct behavior. |
| 2026-07-05 | 3 (workspace selection) | treatment (restructured text) | 2/2 | A→OPTIONAL ("pure Q&A, no codebase referenced"); B→PASS_ABSOLUTE ("repo-grounded review needs target path"). |
| 2026-07-05 | 4 (delegated-diff handling) | treatment (restructured text) | 2/2 | ACTION→REVIEW_FIRST ("skill mandates reviewing every delegated diff before applying"); plugin auto-apply = NO ("delegate returns diff, never auto-applies"). |

**Methodology note:** the baseline runs for scenarios 1 and 2 already passed, i.e. these scenarios are low-difficulty for the model used and confirm *no regression* rather than demonstrating that the new wording rescues a failing baseline. That satisfies #221's ship gate (no regression); a future pass could tighten scenarios 1–2 (e.g. adversarial distractors, a weaker model, or a longer decoy context that pressures the buried baseline rule) to make the improvement observable rather than only the non-regression.
