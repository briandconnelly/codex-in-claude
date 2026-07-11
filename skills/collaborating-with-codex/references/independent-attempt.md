# Independent two-member attempt

Use this pattern to obtain one Claude attempt and one Codex attempt from the same neutral problem,
then synthesize them.

## Order of work

1. Declare the pattern and its cap of one paid Codex call.
2. Establish the neutral task, shared facts, acceptance criteria, and workspace Codex may inspect.
3. Preserve independence by running Codex before drafting. If Claude must draft first, keep that
   draft outside the resolved workspace and every baseline the selected tool will receive.
4. Use consult for an independent design/answer or delegate for an independent implementation.
5. Produce Claude's attempt without conditioning it on Codex's answer.
6. Compare assumptions, evidence, tradeoffs, and failures; verify the load-bearing differences and
   synthesize one decision.

Consult can read tracked and untracked files in its resolved workspace. Delegate works from the
seeded worktree baseline. If either route can see Claude's draft, independence is already lost;
reclassify the call as ordinary critique and follow the one-call collaboration rules.

Do not alter git state solely to hide a draft. Stashing, committing, switching branches, or creating
another clean worktree requires explicit user authorization plus checks that all current state is
preserved and recoverable.

Agreement is only weak support because both attempts may inherit the same task framing. Spend the
synthesis effort on disagreements, differing assumptions, and tests that can distinguish the
approaches.
