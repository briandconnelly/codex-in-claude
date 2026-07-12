# Approval Laundering — Worked Mitigations

The agent runs as the human's OS user and therefore holds **both identities**: it can author a PR as the bot and approve it as the human from a non-opted-in session.
GitHub's block on author self-approval does not close this — the author (bot) and the approver (human) are different accounts.
These mitigations are ordered strongest first; apply them together where practical.

## Structural

Remove approval capability from the human credentials resident on the agent's machine.
Re-auth the personal `gh` with a fine-grained PAT that lacks Pull requests write (or log it out entirely; agent sessions use the bot's `GH_TOKEN` regardless), and approve in the browser.
The agent then cannot run `gh pr review --approve` as the human; it would have to drive the browser or steal its session, a far louder escalation.
Personal pushes are unaffected — they ride the SSH key, not `gh`.

## Server-side

The agent-friendly-github §2 controls close the adjacent vectors.
The human-only-approvals required check keeps the bot's own approval from counting on anyone's PR.
`require_last_push_approval` (small-team/org profiles) closes approve-then-push.
Neither can tell an agent-driven human approval from a real one — that is what the structural mitigation is for.

## Detection

On org repos, correlate `pull_request.create` for the App's PR with `pull_request_review.submit` by the App's operator, especially within seconds of PR creation.
Do not assume the organization audit-log web interface contains every Git transport event or local action.

## Procedural

Never approve a bot-authored PR from inside an agent session.
Approvals happen in the GitHub UI or a personal terminal, after reading the diff.
