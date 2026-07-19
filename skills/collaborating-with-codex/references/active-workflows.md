# Active workflows

Use this reference after selecting consult, review, delegate, or a dry-run. The live tool schema and
`codex_capabilities` remain authoritative for exact parameter names, accepted values, defaults, and
result fields.

## Consult

Use `codex_consult` for a focused question, design critique, or one-off second opinion. Pass only the
question and context needed for the decision. For repo-grounded work, pass an absolute
`workspace_root`; the read-only Codex process can inspect files anywhere in that resolved workspace,
including files not copied into the prompt. For a high-reasoning-effort or broad repo-grounded consult
that can exceed the synchronous deadline, prefer `codex_consult_async` (see
[background jobs](background-jobs.md)); a sync deadline expiry loses the paid run.

Consult returns the shared active-result fields but no `verdict`, `confidence`, or `diff`.

## Review

Use `codex_review_changes` when the review target is represented in git. Select the appropriate
working-tree, branch, or commit scope and narrow paths when useful. Run `codex_dry_run` first when
scope, truncation, redaction, or repository selection is uncertain. A dry-run previews input; it
does not prove that redaction catches every secret. For a multi-file or whole-branch review that can
exceed the synchronous deadline, prefer `codex_review_changes_async`; a sync deadline expiry loses
the paid run.

Review returns the shared active-result fields plus `verdict` and `confidence`. Both are claims to
check against each finding's evidence and the actual code.

## Delegate

Use `codex_delegate` for a self-contained implementation task in a repository with at least one
commit. `codex_delegate_dry_run` previews the seeded baseline and prompt size without creating a
worktree or spending. The real run creates a throwaway worktree seeded from `HEAD` plus replayable
uncommitted tracked changes; untracked files are not copied. For a substantial or multi-file task
that can exceed the synchronous deadline, prefer `codex_delegate_async`; a sync deadline expiry
loses the paid run.

Delegate returns the shared active-result fields plus a proposed `diff`. The plugin never applies
that diff to the live tree. Inspect it, validate it against the task, and apply changes yourself only
when correct. The delegate sandbox blocks network egress, so installs, remote git operations, `gh`,
`curl`, and publishing must happen outside the delegated task.

## Result discrimination

Discriminate results in the order given in SKILL.md → Reading results: branch on `ok`, then on the
concrete tool or originating job kind, then read only that success type's fields.

Completed consult, review, and delegate results share `summary`, `findings`, `questions`,
`assumptions`, `next_steps`, `raw_response`, and `meta`. Other tools do not inherit that success
shape. Discovery, status, model catalog, dry-run, transfer, async-start, and job-lifecycle results
each have their own schema.

Use `detail="summary"` for the normal compact active result and `detail="full"` only when raw model
text is needed for diagnosis. Treat the structured fields as authoritative in either case.
