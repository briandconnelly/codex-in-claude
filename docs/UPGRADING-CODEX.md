# Upgrading the supported `codex` version

The repeatable procedure for incorporating a new OpenAI `codex` CLI release. It pairs a
**mechanical** drift check (`scripts/check_codex_contract.py`, no model call, no spend) with the
**judgment** checks a script can't make — help output proves a flag *exists*, never that its
*semantics* still hold.

- The contract this protects, and *why* each guarantee exists, lives in [`COMPATIBILITY.md`](../COMPATIBILITY.md).
- Cutting a **package** release (PyPI/tag) is a separate concern — see [`docs/RELEASING.md`](RELEASING.md).
  A codex-version bump only triggers a package release if you choose to ship it as one.

The single source of truth for every CLI assumption is `src/codex_in_claude/cli_contract.py`. Most
steps below come down to: probe the new CLI, confirm or update that one file, prove it with tests.

You usually don't have to notice a release yourself: `.github/workflows/codex-release-watch.yml`
runs weekly, and when a new `codex` **minor** appears upstream it opens a `codex-upgrade` tracking
issue pre-filled with this checklist. The watch is no-spend and CLI-free — it only flags the new
minor; everything below still runs locally, where the real authenticated `codex` lives. (A
patch-only bump within the tracked minor is deliberately not flagged; re-run step 1 opportunistically
if you want to refresh the `Verified against` line.)

## 0. Prerequisites

- The new `codex` is installed and authenticated (`codex login`). Everything the steps below
  *verify* runs against this installed binary; the scratch binaries in step 2A exist only to supply
  a comparison baseline.
- `npm` and `jq`, for step 2A's A/B against the previous version. Without `npm` that step falls back
  to the committed snapshots, which cover less.
- Start from a clean branch: `chore/codex-<major>-<minor>` (e.g. `chore/codex-0-142`).
- Unset any `CODEX_IN_CLAUDE_SUPPORTED_VERSIONS` override so you test the built-in set, not your env.

## 1. Run the mechanical drift check (no spend)

```sh
uv run python scripts/check_codex_contract.py
```

It probes `codex --version` and `codex exec --help` (the same free probes the server uses), then
reports against `cli_contract.py`:

- **`FAIL` (exit 1)** — an `ALWAYS_SEND_FLAGS` flag or a `VALID_SANDBOXES` value vanished. A real
  contract break; do not ship until resolved (see step 4).
- **`WARN`** — a `HELP_GATED_FLAGS` flag (e.g. `--model`) is absent (server drops it gracefully), or
  the running version isn't yet in `SUPPORTED_VERSIONS`.
- **`INFO`** — flags codex offers that the contract doesn't consume. Skim for anything newly
  relevant (a new isolation/output flag worth adopting; a new dangerous flag to keep avoiding).
- **exit 2** — couldn't probe (binary missing / timed out / unparseable). Fix the environment first;
  nothing was verified.

This is the mechanical half only. Steps 2–3 are the judgment half the script cannot do — gather the
evidence they read first, in step 2A.

## 2A. Establish comparable old/new evidence (mechanics)

Steps 2–3 are judgment calls, but they are only as good as what you diff against. This section owns
**acquiring** the evidence; step 2 owns **reading** it.

An in-place upgrade (Homebrew, `codex update`) replaces the old binary, but it does not destroy your
access to it: `codex` ships on npm, so **any prior version installs side-by-side** without touching
the global install. Retrieve both versions into scratch prefixes and drive them by absolute path, so
a difference you observe is attributable to the version and not to which binary happened to be first
on `PATH`:

```sh
SCRATCH=$(mktemp -d)
npm install --prefix "$SCRATCH/old" @openai/codex@<old-version> >/dev/null
npm install --prefix "$SCRATCH/new" @openai/codex@<new-version> >/dev/null
OLD="$SCRATCH/old/node_modules/.bin/codex"; NEW="$SCRATCH/new/node_modules/.bin/codex"
"$OLD" --version && "$NEW" --version   # confirm each path is the version you think it is
```

Keep that shell for the rest of step 2A — everything below uses `$SCRATCH`, `$OLD`, and `$NEW`.

**Authenticate the retrieved old binary before trusting it.** Regenerate every capture listed in
[`docs/codex-help/`](codex-help/)'s README from the retrieved binary and diff each against that
version's directory. **All of them must match**; a single mismatch stops the A/B until you have
reconciled it. Authentication is binary-level — a clean diff licenses the surfaces the snapshots
*don't* cover, which is what makes the schema and behavior A/Bs below meaningful. (Why it is needed:
the binary actually verified last time is gone, and those captures are the only surviving evidence
of it.) If no prior snapshot exists — the first time through this practice — you have no
authenticator: review the new surface in absolute terms and do not claim an A/B.

If npm is unreachable, fall back to reading the committed snapshots directly — a real, if narrower,
diff source. Every other surface below needs the old binary and has no offline substitute.

Then capture each surface from **both** binaries and compare:

- **Help text and feature flags.** The captures listed in [`docs/codex-help/`](codex-help/)'s README,
  plus `codex app-server --help`, which no snapshot covers today. Compare with a plain `diff`; help
  text needs no canonicalization.
- **App-server protocol schemas.** `codex app-server generate-json-schema --out <dir>` emits the
  entire app-server protocol — the surface `codex_transfer` and the live rate-limit read depend on.
  Generate one directory per binary, continuing from the block above:

  ```sh
  "$OLD" app-server generate-json-schema --out "$SCRATCH/schema-old"
  "$NEW" app-server generate-json-schema --out "$SCRATCH/schema-new"
  ```

  Read the result in two passes, and do not conflate them:
  - *Inventory* — which messages appeared or vanished, i.e. new or dropped protocol methods:

    ```sh
    diff -rq "$SCRATCH/schema-old" "$SCRATCH/schema-new" | grep '^Only in'
    ```

    Keep the `grep`. Unfiltered, `diff -rq` also lists every *differing* file, and those are mostly
    noise: each generated file inlines shared definitions, so one real change reverberates across
    dozens of files (0.144.1 → 0.145.0 listed 64 differing entries for what was, on the consumed
    surface, a single added field — observed 2026-07-21). Do not work through that list; the next
    pass is what reads content.
  - *Content* — diff only the schemas this plugin consumes; `cli_contract.py`'s app-server block
    lists them by path. That is the comparison that decides whether anything we depend on moved.
    Canonicalize both sides first — the generator is **not** byte-deterministic (two runs of the
    *same* binary emit `codex_app_server_protocol.v2.schemas.json` with different key order), so a
    raw diff reports drift that isn't there:

    ```sh
    for f in <the paths listed in cli_contract.py>; do
      printf '%s: ' "$f"
      diff <(jq -S . "$SCRATCH/schema-old/$f") <(jq -S . "$SCRATCH/schema-new/$f") || true
    done
    ```

    A path missing from either side is a finding, not a skip — `jq` will fail on it, so do not
    silence that error.
- **Behavior with no CLI surface at all.** Some upstream changes have no flag and no subcommand —
  what auto-loads into context, and the feature flags that govern it. Run
  [`COMPATIBILITY.md`](../COMPATIBILITY.md) → "Implicit Codex context" → "Re-verifying on a Codex
  upgrade" against **both** binaries and compare the two presence matrices it produces. This A/B is
  what separates "new in this release" from "always true and we never looked" — an absolute-terms run
  cannot tell those apart. That section owns the fixture, the recording rules, and how to read a
  difference; two things are specific to running it twice:
  - Both binaries read the same `$CODEX_HOME`, so keep the temporary global-skill marker in place for
    **both** runs, and remove it only after the last one.
  - Hold everything else constant across the two runs — model, account, fixture, prompt, and flags.

## 2. Manual semantic + surface review (judgment — not automatable)

The script confirms shapes; you confirm meaning. Work from the old/new evidence gathered in step 2A
— primarily an A/B against the previous version's binary, falling back to the committed snapshots
under [`docs/codex-help/`](codex-help/) when npm is unreachable. Then check:

- **Flag semantics unchanged.** A flag the script found may have changed behavior. Spot-check the
  guarantee-bearing ones: does `--sandbox read-only` still block writes? does `workspace-write` still
  block network egress? does `--output-last-message` still receive the final message? does
  `--ignore-rules` still drop every policy source? A semantics change is a guarantee change even
  though the flag name is unchanged — treat it like a removal.
- **Sandbox values** (`read-only`, `workspace-write`, `danger-full-access`) still present and still
  mean the same boundary. Confirm the default paths still never emit `danger-full-access` or any
  `--dangerously-bypass-*`.
- **Implicit context.** Refresh the observations table in [`COMPATIBILITY.md`](../COMPATIBILITY.md) →
  "Implicit Codex context" from the two-binary marker probe you ran in step 2A. `--help` structurally
  cannot see this surface (no flag, no subcommand), so the mechanical drift check above will not
  catch a change to what auto-loads or where it loads from.
- **New capabilities worth adopting or explicitly avoiding.** Don't stop at the script's flag `INFO`
  list — it only sees `codex exec` flags. Also scan the `Commands:` section of `codex --help` for new
  **subcommands** and run `codex features list` for new **feature flags** (the `--enable`/`--disable`
  surface). A release's most relevant new surface often lives there rather than in `codex exec`'s
  flags — e.g. 0.142 added the `features` subcommand and a native `codex exec review --output-schema`.
  Adopting any of these is a separate, deliberate change — not part of a version bump.
- **Model catalog fallback.** `cli_contract.py`'s `KNOWN_MODEL_SLUGS` is a bundled fallback copied
  from a specific CLI's `$CODEX_HOME/models_cache.json`, meant to stay in lockstep with
  `SUPPORTED_VERSIONS`. Diff its **slug set** (not the volatile `client_version`/`fetched_at`) against
  the new CLI's live cache. If slugs changed, update the tuple; either way refresh the provenance
  comment's re-verified date. While in the cache, also confirm the reasoning-effort discovery
  fields still hold their pinned shape: `default_reasoning_level` a string,
  `supported_reasoning_levels` a list of `{effort, …}` objects (the parser degrades to `None`
  on drift — silent for agents, so record a shape change in `CHANGELOG.md`).
- **Reasoning-effort config key.** The `reasoning_effort` controls ride
  `-c model_reasoning_effort=…` (`MODEL_REASONING_EFFORT_CONFIG_KEY`) — a config key `--help`
  cannot advertise, so the mechanical drift check can't see it, and a key rename/removal drifts
  **silently** (codex tolerates unknown `-c` keys as junk): this manual step is the only guard.
  Re-verify per COMPATIBILITY.md's reasoning-effort section, then refresh that section's verified
  dates. Probe 1 —
  `codex exec --json --ignore-user-config --ephemeral --skip-git-repo-check -c 'model_reasoning_effort="bogus"' -c model=bogus-model-xyz - <<< "hi"`
  (the inner quotes mirror the plugin's TOML-string-encoded transport — see COMPATIBILITY.md)
  — confirms the run is not rejected at parse (the `-c` route still exists); it **cannot** prove
  the key is still read, because a tolerated junk key produces the same backend bogus-model error.
  Probe 2 — the same invocation with a **valid** model — is the check that proves the key is still
  applied: it must fail with both bracketed marker fields, `[reasoning.effort]` and
  `[ReasoningEffortParam]` (`REASONING_EFFORT_REJECTION_MARKERS` — the classifier requires all of
  them in `[…]` form); note it spends a trivial request. Also check
  `codex exec --help` for a new dedicated effort flag worth adopting.
- **Structured output.** Run a small live `codex exec --output-schema <file>` and confirm the final
  message still conforms to the strict-mode schema in `schemas.py`. (Reminder, already in
  `COMPATIBILITY.md`: native `codex review --output-schema` is **not** honored for the final message
  — `codex_review_changes` must keep using `codex exec` with a diff we gather ourselves. Re-confirm
  this hasn't regressed before considering the native review subcommand.)
- **Failure classification.** Trigger the no-spend parser failures (an unknown flag, an invalid
  `--sandbox` value) and confirm they still match `CONTRACT_DRIFT_STDERR_PATTERNS`. If you can safely
  observe new auth / rate-limit wording, reconcile it against `AUTH_FAILURE_PATTERNS` /
  `RATE_LIMIT_PATTERNS`. **Only add signatures from real observed output** — never guess phrasings.
- **JSONL event shape.** Inspect a representative success and failure `--json` stream for token
  usage, session id, and error text. Parsing is tolerant, so degraded metadata won't crash a run —
  but if usage/session metadata silently disappears, that's a conscious call to record in
  `CHANGELOG.md`, not something to ignore.

## 3. Decide: replace vs. add the supported minor

`SUPPORTED_VERSIONS` is `{(major, minor)}` and is **advisory only** — an untracked version warns in
`codex_status` but never blocks.

- **Replace** the old minor when you've verified only the new one and intend to track a single
  current codex minor (the project's default — matches the single "Verified against" line).
- **Add** (keep both) only when you have *actually verified* both and want to support both paths.
  Don't keep an unverified old minor just to silence a warning.
- A **patch-only** codex bump within the same minor needs no set change; you may still refresh the
  `Verified against` line after re-running step 1.

## 4. Update `cli_contract.py` + files in lockstep

For a normal (non-breaking) codex minor bump. **Start by grepping the whole repo for the old
literal** — `grep -rFn <old-minor> src tests docs *.md` (e.g. `grep -rFn 0.141 …` for a 0.141 → 0.142
bump; `-F` keeps the `.` a literal, not a regex wildcard) — and reconcile every hit. The table names
the usual ones, but treat the grep as authoritative: a stale enumerated list *will* miss a file (e.g.
`tests/test_check_codex_contract.py`'s `VERSION` was nearly missed this way).

| File | What changes |
|------|--------------|
| `src/codex_in_claude/cli_contract.py` | `SUPPORTED_VERSIONS`; the `Verified against …` / `0.x` comments; the `KNOWN_MODEL_SLUGS` provenance comment; any flag, sandbox, signature, or event-marker drift found in step 2 |
| Test version literals | Bump the literals that represent **the supported/current version** — `test_config.py`, `test_coverage_extra.py`, `test_codex.py`, `test_server.py`, and `test_check_codex_contract.py`'s `VERSION`. **Leave deliberate logic fixtures alone:** `test_check_codex_release.py` exercises the watcher's "new vs. tracked" logic with arbitrary versions, and `test_codex_models.py` uses a synthetic cache fixture — neither is the supported-set, so flipping them is wrong. |
| `docs/codex-help/<new-version>/` | Commit fresh `--help` + `features list` snapshots for the new version — captured from the binary you actually verified, so the *next* upgrade can authenticate its npm-retrieved stand-in against them (step 2A) |
| `COMPATIBILITY.md` | the `Verified against` line; any changed policy |
| `README.md` | only if user-facing compatibility text changes (it carries no pinned literal otherwise) |
| `CHANGELOG.md` | an entry under `## [Unreleased]` |

Do **not** touch the package-release version set (`pyproject.toml`, `.claude-plugin/plugin.json`,
`.mcp.json` pin) here — those move only when cutting a release per `docs/RELEASING.md`.

## 5. `FINGERPRINT` and breaking changes

A codex upgrade usually changes nothing an MCP client can observe. Adding or replacing a verified
codex minor, refreshing advisory warnings, adding signatures for *existing* error codes, and
test/doc updates all leave the plugin's discovered surface byte-identical — no `FINGERPRINT` change.

A codex change forces a bump only when you **propagate it to our surface**: when it produces an
externally observable change to a category in `FINGERPRINT_COVERS` (`src/codex_in_claude/schemas.py`).
Then, in the same commit, bump `FINGERPRINT`, regenerate the manifest snapshot, update the tests
that pin the old value (the `FINGERPRINT` assertions and `EXPECTED_MANIFEST_HASH` — the failures
name themselves), and note it in `CHANGELOG.md`.

Whether that same change is *also* **breaking** is a separate question, and most surface changes are
not. Don't infer one from the other — `AGENTS.md` → Versioning carries the decision table for both,
including the guarantee-weakening cases (sandbox/isolation, `--output-last-message`,
structured-output enforcement) that a codex upgrade is most likely to trip.

## 6. Verify before shipping

Run the fast contract-adjacent suites first, then [the gate](../AGENTS.md#tooling), then the two
codex-specific checks below:

```sh
uv run pytest tests/test_cli_contract.py tests/test_preflight.py tests/test_codex.py tests/test_config.py
uv run python scripts/check_codex_contract.py   # mechanical drift check is green
uv run pytest -m integration --no-cov           # LIVE — hits the real codex CLI (spends tokens)
```

The integration suite is the proof the contract holds end-to-end against the newly installed codex.
It is opt-in (excluded by default) and **not run in CI** — CI has no authenticated codex — so run it
locally as the final gate.

## Gotchas this procedure guards against

- A flag stays in `--help` but its **semantics change** — caught only by step 2, never the script.
- A guarantee flag disappears and someone is tempted to move it to `HELP_GATED_FLAGS` to "fix" the
  failure. Don't — that silently drops a guarantee. ALWAYS_SEND failures fail loud by design.
- A help **formatting** change causes a false parser negative (the `WARN`/`FAIL` is about the parser,
  not necessarily the CLI). Confirm against the raw `--help` text.
- An **stderr phrasing** change makes a genuine contract break classify as `nonzero_exit`, or a broad
  pattern (`429`, `invalid value`) masks a more specific cause. Reconcile signatures in order.
- JSONL moves error text to a different field, or token-usage keys change — degrades metadata
  silently under tolerant parsing.
- A surface nobody thought to snapshot reads as "unchanged" because it was never looked at. The
  committed captures cover only what we chose to capture; step 2A's A/B against the previous binary
  is what lets you ask a question the snapshots don't already answer.
- A long-lived MCP server caches `codex exec --help` for `HELP_CACHE_TTL_SECONDS`; after an in-place
  upgrade it re-probes only once the TTL lapses. Restart the server (or wait it out) when validating.
- `codex login status` output may include account-identifying details — don't paste it into commits,
  issues, or PRs.
