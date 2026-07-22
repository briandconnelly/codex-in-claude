---
name: collaborating-with-codex
description: >-
  Use whenever Claude Code should call or compose with Codex: an ordinary consult, code review,
  delegated implementation, transfer, async run, independent two-model attempt, or declared
  review–revise workflow. Trigger on requests such as “ask Codex,” “get a second opinion,” “have
  Codex review this,” “delegate this to Codex,” “have both models attempt this,” or “run
  review–revise,” and at
  self-initiated decision points: choosing a hard-to-reverse approach, after two failed fixes,
  before declaring risky work complete, or when an independent implementation would help. Route to
  the matching reference and compose with applicable process skills.
---

# Collaborating with Codex

Use this skill as the router and shared safety contract for every Codex workflow. Retain
responsibility for the work, and compose this guidance with applicable planning, debugging, review,
and verification skills instead of replacing them.

## Shared workflow

1. Call `codex_status` before a paid call. Proceed only when both `ready: true` and
   `extra_args_valid: true`. If either is false, stop and surface the corresponding readiness or
   operator-configuration detail.
2. Treat `rate_limit` as advisory. `codex_status` reads it live from the Codex app-server (no model
   spend), so it is current when `ready: true`. Decide spend from it: proceed on `available`; defer
   non-urgent calls on `limited` or `exhausted`; treat `unknown` (the live read could not complete,
   or only a stale snapshot was available — `is_stale`/`as_of`), `unavailable` (this codex/account
   exposes no quota data), or `home_unverified: true` as uncertainty — neither permission nor denial.
   The account reports only the windows that currently bind it, so `primary` (shorter/rolling) or
   `secondary` (longer) may be null. Read `note` for plain-language caveats before relying on it.
3. Select one route below and load only its needed reference. Use a free dry-run when one exists.
4. Declare the paid-call cap before the first active call, then stay within it.
5. Branch on `ok`, then on the concrete tool/result type. Verify claims before acting.

## Route the request

| Situation | Tool or workflow | Read |
| --- | --- | --- |
| One answer, design critique, or second opinion | `codex_consult` | [active workflows](references/active-workflows.md) |
| Stuck mid-debugging or choosing between viable approaches | `codex_consult` | [active workflows](references/active-workflows.md) |
| Review changes already represented in git | `codex_review_changes` | [active workflows](references/active-workflows.md) |
| Proposed implementation diff from an isolated worktree | `codex_delegate` | [active workflows](references/active-workflows.md) |
| A consult, review, or delegate that can exceed the synchronous deadline — high-reasoning-effort or broad repo-grounded work, a multi-file or whole-branch review, or a substantial implementation task | matching `_async` tool | [background jobs](references/background-jobs.md) |
| Move the Claude session into a resumable Codex thread | `codex_transfer` | [session transfer](references/transfer.md) |
| Claude and Codex attempt independently, then synthesize | independent two-member attempt | [independent attempt](references/independent-attempt.md) |
| Claude drafts, Codex critiques, Claude revises | declared review–revise | [review–revise](references/review-revise.md) |
| Optional parameters, idempotency, or a tool error | current tool | [options and errors](references/options-and-errors.md) |
| MCP server unavailable | limited read-only CLI fallback | [server-down fallback](references/server-down-fallback.md) |
| None of these, or a Codex call would not change the decision | no call — proceed without Codex | — |

When a request matches both a sync row and the async row, prefer the matching `_async` tool: a
sync call whose deadline expires (built-in default 300s) is terminated and its partial paid work is
lost, whereas the async job runs to a separately configured deadline (built-in default 1800s). The
sync tool is for focused work that finishes well inside the deadline.

Use `codex_dry_run` or `codex_delegate_dry_run` to preview review or delegate scope. Use
`codex_capabilities`, `codex_status`, and `codex_models` for current schemas, defaults, readiness,
and model discovery. A subset of these tools is also exposed to users as `/codex:*` slash commands.

Route a one-call critique or “judge my draft” request as ordinary consult or review. A single
consult — or no call — is the default; composition is opt-in and exceptional. Select a composed
workflow only when the user requested it or the task already declares it, and the value/risk gate
clears: the stakes are high (a hard-to-reverse, load-bearing, or security-sensitive decision), a
single opinion is genuinely insufficient, and you can verify and synthesize the outputs. If the
gate fails, make one call or none and move on.

## Reading results

- Branch on `ok` first. On `ok: false`, branch on `error.code` and use the machine-readable
  `error.repair`; do not infer recovery from prose or retry blindly.
- On `ok: true`, branch on the concrete tool or result type before reading fields. Completed
  consult, review, and delegate results share active-result fields; only review has
  `verdict`/`confidence`, and only delegate has `diff`.
- Discovery, dry-run, transfer, async-start, and job-lifecycle tools have tool-specific success
  schemas. A result fetched with `codex_job_result` or `codex_job_consume_result` matches the
  originating consult, review, or delegate tool.
- Treat live tool schemas and `codex_capabilities` as authoritative for exact inputs, outputs, error
  codes, and defaults.

## Data exposure

Facts to weigh before any active call:

- Every supplied prompt and context field is sent to OpenAI raw.
- During every active call — including consult — Codex may read other files in the resolved
  workspace.
- Codex auto-loads the workspace's `AGENTS.md` and `.agents/skills/` skills, and discovers your
  user-global skills under `$CODEX_HOME/skills/` from outside the workspace, even if the prompt
  never mentions them; the isolation flags do not suppress any of this.
- Redaction is best-effort protection for gathered diffs and returned output only. It never protects
  supplied input, auto-loaded context, or files Codex reads.

## Binding rules

- **Spend — one call per decision point:** Make one active call per ordinary decision point. Each
  async start counts as an active call, and never start both the sync and async forms for the same
  work.
- **Spend — workflow caps:** An independent-attempt workflow gets one Codex call. A declared
  review–revise workflow gets one call by default, and at most two only when high risk and the
  two-call cap were declared before call one (see the independent-attempt and review–revise
  references).
- **Workspace:** Pass an absolute `workspace_root` for every repo-grounded call, including consult,
  dry-run, and job-lifecycle calls. Omit it only for a pure question that needs no workspace.
- **Privacy:** Do not make an active call when the supplied prompt, the supplied context, any file
  Codex may inspect in the resolved workspace, or your user-global skills under
  `$CODEX_HOME/skills/` contain something you cannot disclose (see Data exposure). Changing the
  workspace does not exclude those skills.
- **Verification:** Treat findings, summaries, verdicts, and proposed changes as unverified claims.
  Run the applicable project checks yourself; read-only consult/review is not proof tests ran.
- **Delegation:** Never apply a delegated diff before reviewing it. The plugin does not apply it to
  the live tree. Delegate runs have no network egress, so keep the task self-contained.
- **Retry:** Never loop paid retries. After an ambiguous transport failure, retry the same concrete
  tool with the same arguments and `idempotency_key`; never switch between sync and async expecting
  that key to replay the run.
- **Polling — pacing:** Wait at least the current `poll_after_ms` between job-status calls; never
  busy-poll.
- **Polling — workspace:** Pass the same absolute workspace to every lifecycle call for a job.
- **Polling — fetch:** Fetch a job's result only after `result_available` is true.
- **Independence — ordering:** Finalize Claude's attempt before Codex's answer enters context:
  start the `_async` call and draft before fetching, or draft before a sync call.
- **Independence — draft placement:** Keep the Claude draft outside every workspace and baseline
  Codex can inspect.
- **Independence — reclassification:** If Codex can see the draft, or Codex's answer arrived before
  Claude's attempt was finalized, classify the operation as critique and do not claim independence.
- **Git state:** Never stash, commit, switch branches, or create a clean worktree solely to
  manufacture independence unless the user explicitly authorizes it and preservation checks show
  their state will remain safe.
- **Synthesis:** Verify load-bearing disagreements against evidence or project checks. Treat agreement
  as weak evidence because the models may share framing and blind spots; never tally votes or spend
  another call to manufacture confirmation.
- **Recursion:** Do not ask Codex to invoke another agent or create agent-to-agent call chains unless
  the user explicitly requests that architecture.
