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
2. Treat `rate_limit` as advisory. It is the latest usable quota snapshot emitted by a paid run, not
   a live query. A paid run that emits no usable quota data leaves the previous snapshot, or the
   unknown state, unchanged. Check `status`, `is_stale`, and `as_of` before deciding to spend.
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
| Long-running consult, review, or delegate | matching `_async` tool | [background jobs](references/background-jobs.md) |
| Move the Claude session into a resumable Codex thread | `codex_transfer` | [session transfer](references/transfer.md) |
| Claude and Codex attempt independently, then synthesize | independent two-member attempt | [independent attempt](references/independent-attempt.md) |
| Claude drafts, Codex critiques, Claude revises | declared review–revise | [review–revise](references/review-revise.md) |
| Optional parameters, idempotency, or a tool error | current tool | [options and errors](references/options-and-errors.md) |
| MCP server unavailable | limited read-only CLI fallback | [server-down fallback](references/server-down-fallback.md) |
| None of these, or a Codex call would not change the decision | no call — proceed without Codex | — |

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
- **Privacy:** Do not target a workspace containing secrets you cannot disclose. Every supplied
  prompt and context field is sent to OpenAI raw, and during every active call — including consult —
  Codex may read other files in the resolved workspace. Redaction is best-effort protection for
  gathered diffs and returned output only; it does not protect supplied input or files Codex reads.
- **Verification:** Treat findings, summaries, verdicts, and proposed changes as unverified claims.
  Run the applicable project checks yourself; read-only consult/review is not proof tests ran.
- **Delegation:** Never apply a delegated diff before reviewing it. The plugin does not apply it to
  the live tree. Delegate runs have no network egress, so keep the task self-contained.
- **Retry:** Never loop paid retries. After an ambiguous transport failure, retry the same concrete
  tool with the same arguments and `idempotency_key`; never switch between sync and async expecting
  that key to replay the run.
- **Polling:** Honor `poll_after_ms`, use the same absolute workspace, and fetch the result only after
  `result_available` is true. Do not busy-poll.
- **Independence:** Run Codex before drafting, or keep the Claude draft outside every workspace and
  baseline Codex can inspect. If Codex can see the draft, classify the operation as critique and do
  not claim independence.
- **Git state:** Never stash, commit, switch branches, or create a clean worktree solely to
  manufacture independence unless the user explicitly authorizes it and preservation checks show
  their state will remain safe.
- **Synthesis:** Verify load-bearing disagreements against evidence or project checks. Treat agreement
  as weak evidence because the models may share framing and blind spots; never tally votes or spend
  another call to manufacture confirmation.
- **Recursion:** Do not ask Codex to invoke another agent or create agent-to-agent call chains unless
  the user explicitly requests that architecture.
