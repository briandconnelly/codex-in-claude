# Independent two-member attempt

Use this pattern to obtain one Claude attempt and one Codex attempt from the same neutral problem,
then synthesize them. Independence must be observable in the transcript, not asserted: neither
member's attempt may be visible to the other before that other member's attempt is finalized.

## Order of work

1. Declare the pattern and its cap of one paid Codex call.
2. Establish the neutral task, shared facts, acceptance criteria, and workspace Codex may inspect.
3. Start Codex's attempt with the matching `_async` tool — `codex_consult_async` for an independent
   design or answer, `codex_delegate_async` for an independent implementation. The start is the one
   paid call; do not poll for the result yet.
4. Produce and finalize Claude's attempt in full before any call that can return Codex's answer.
   Keep the draft outside the resolved workspace and outside every baseline or path the selected
   tool received; hold it off disk entirely when it is small enough to keep in context. That
   removes Codex's pointer to the draft — it is not a read boundary, and a running job can still
   read files elsewhere.
5. Only after Claude's attempt is finalized, poll and fetch Codex's result per the background-jobs
   reference.
6. Check the returned output for distinctive content of Claude's draft before comparing. This is
   the one channel that can *prove* contact after the fact, and it costs nothing.
7. Compare assumptions, evidence, tradeoffs, and failures; verify the load-bearing differences and
   synthesize one decision. If the draft was persisted anywhere on disk while the job ran, say in
   the synthesis that independence rests on Codex having had no pointer to it, not on a read
   boundary.

If only the sync tool is available, finalize Claude's attempt before making the call. The reverse
order cannot be repaired by intent: once Codex's answer is in context, everything drafted afterward
is conditioned on it, and "I did not condition on it" is neither enforceable nor observable.

Consult can read tracked and untracked files in its resolved workspace, and is not confined to it —
read-only bounds writes, not reads. Delegate works from the seeded worktree baseline; the sandbox
bounds its writes (the worktree plus the OS temp roots `/tmp` and `$TMPDIR`, per codex's
workspace-write default), never its reads. So placing the draft elsewhere on disk removes Codex's
pointer to it but does not put it out of reach.

That distinction sets what you may claim. "Codex did not see the draft" is not available: the result
contract carries no read audit, and the framing you wrote into the shared task reaches both members
regardless. The claim the transcript *can* support is that Codex had no pointer to the draft and its
output shows no sign of it. So reclassify the call as ordinary critique — and follow the one-call
collaboration rules — when the draft was supplied to Codex or named to it; when it was persisted
inside the resolved workspace, the seeded baseline, or a path passed to any call for this job; when
Codex's answer entered context before Claude's attempt was finalized; or when the returned output
carries distinctive content of the draft.

Do not alter git state solely to hide a draft. Stashing, committing, switching branches, or creating
another clean worktree requires explicit user authorization plus checks that all current state is
preserved and recoverable.

Agreement is only weak support because both attempts may inherit the same task framing. Spend the
synthesis effort on disagreements, differing assumptions, and tests that can distinguish the
approaches.
