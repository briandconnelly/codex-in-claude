# Scenario 5 — Baseline Scoring

Date: 2026-07-11
Run: baseline without the skill.

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| 1 | Report a clean outcome | FAIL | It says there is no material mixing but still raises a minor observation. |
| 2 | Avoid manufactured findings | FAIL | It questions whether the CI timing context should become a rule. |
| 3 | Accept the explicit default and override | PASS | It recognizes rule 7 as the condition for rule 6. |
| 4 | Avoid an R5 finding | PASS | It recognizes that precedence is explicit. |

Total: 2/4.

## Scored Output

The document separates context from constraints well.

- The `Context` section contains descriptive background: the audience, purpose, invocation frequency, and workflow timing.
- The `Rules` section contains actionable, testable requirements governing classification, content, and entry count.
- Rules 6 and 7 could otherwise conflict, but rule 7 explicitly defines its condition and precedence.
- The conditional language in rule 7 remains a constraint, not misplaced context, because it directly changes the required output.
- The frontmatter description appropriately states the activation condition.

Minor observation: “after CI has passed” could be interpreted as an invocation precondition.
If strict enforcement matters, it could be moved to the rules; otherwise it is reasonable workflow context as written.

Overall finding: strong separation, with no material context/constraint mixing.
