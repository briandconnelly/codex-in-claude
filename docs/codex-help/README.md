# Captured `codex` CLI help snapshots

One directory per verified `codex` version (e.g. `0.142.0/`), each holding the raw output of the
free local probes:

- `codex.txt` — top-level `codex --help` (the `Commands:` inventory)
- `exec.txt` — `codex exec --help` (the flags the server actually uses)
- `review.txt`, `exec-review.txt` — the native review surfaces we deliberately don't use yet
- `features-list.txt` — `codex features list` (the `--enable`/`--disable` feature flags)

**Why these are committed:** an in-place upgrade (Homebrew, `codex update`) destroys the old binary,
so once you've upgraded you can no longer run the *previous* version's `--help` to diff against.
These snapshots are the only diff source for the semantic review in
[`UPGRADING-CODEX.md`](../UPGRADING-CODEX.md) step 2. Capturing the new version's snapshots is part of
that procedure's lockstep update (step 4).
