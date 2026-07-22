# Captured `codex` CLI help snapshots

One directory per verified `codex` version (e.g. `0.144.1/`), each holding the raw output of the
free local probes:

- `codex.txt` — top-level `codex --help` (the `Commands:` inventory)
- `exec.txt` — `codex exec --help` (the flags the server actually uses)
- `review.txt`, `exec-review.txt` — the native review surfaces we deliberately don't use yet
- `features-list.txt` — `codex features list` (the `--enable`/`--disable` feature flags)

**Why these are committed:** each capture came from the binary that was *actually verified* for that
version — and that binary is gone after an in-place upgrade. A prior `codex` is still retrievable
from npm ([`UPGRADING-CODEX.md`](../UPGRADING-CODEX.md) step 2A), but a retrieved build is a
stand-in, and nothing surviving locally can vouch for it except these files. So their job is to
**sanity-check** it: diff the retrieved binary's help against the matching directory here before
drawing any conclusion from an A/B. A clean diff says the retrieved build presents the same CLI
surface as the one verified last time — good evidence it is the same build, though not proof, and no
evidence at all about surfaces these files don't capture. A mismatch is disqualifying. They are also
the fallback diff source when npm is unreachable.

**What they do not cover:** only the five commands listed above, only as help text. They are not a
record of the whole CLI surface — `codex app-server --help`, the generated app-server JSON schemas,
and every behavior with no CLI surface at all (what auto-loads into context) are diffed live against
a retrieved binary instead. A surface absent here is undiffable from this directory alone, which is
why step 2A leads with the A/B rather than with these files.

Capturing the new version's snapshots is part of that procedure's lockstep update (step 4).
