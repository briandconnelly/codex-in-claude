# Declared review–revise

Use this pattern when Claude will create an artifact, Codex will critique it, and Claude will revise
it. The critique improves the work; it does not certify correctness.

## Default pass

1. Before the active call, declare the review–revise pattern and a one-call cap.
2. Draft the artifact and run relevant local checks.
3. Select review for git changes or consult for a design or other non-diff artifact. Scope the call
   to the decision and run a free dry-run where available.
4. Verify each material finding, revise accepted issues, and record why disputed findings were
   declined.
5. Run the project's checks and stop.

## Optional high-risk second pass

A second paid critique is allowed only when the work was explicitly classified as high risk before
the first call and the declared cap was two total calls. Use the second call on the revised artifact,
verify any new findings, rerun checks, and stop after that call.

Do not add a second pass because the first result was reassuring, inconvenient, or inconclusive.
Do not turn the sequence into an open-ended conversation. A clean critique means only that Codex
reported no issue under the shared scope and framing.
