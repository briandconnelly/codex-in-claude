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

**`$NEW` is the installed binary**, not a second npm copy — it is the one the contract check and the
integration suite verify, so it must be the one you draw conclusions about. Only `$OLD` comes from
npm:

```sh
SCRATCH=$(mktemp -d)
npm install --prefix "$SCRATCH/old" @openai/codex@<old-version> >/dev/null
OLD="$SCRATCH/old/node_modules/.bin/codex"
NEW=$(command -v codex)                # the installed, authenticated binary from step 0
"$OLD" --version; "$NEW" --version     # confirm each path is the version you think it is
```

Keep that shell for the rest of step 2A — everything below uses `$SCRATCH`, `$OLD`, and `$NEW`.

That leaves one uncontrolled variable: `$OLD` and `$NEW` come from different distribution channels,
so a difference could in principle be packaging rather than version. To rule it out, install the
**new** version from npm too and confirm it matches the installed one:

```bash
npm install --prefix "$SCRATCH/new" @openai/codex@<new-version> >/dev/null
diff <("$SCRATCH/new/node_modules/.bin/codex" --help) <("$NEW" --help)
```

A clean diff retires the concern for this pair. (It was clean for `0.145.0` on macOS, observed
2026-07-21.) If it is *not* clean, the channels differ and every cross-channel comparison below is
suspect — investigate before continuing.

**Sanity-check the retrieved old binary before trusting it.** Every capture it produces must match
the committed ones for that version; **a single mismatch stops the A/B** until you have reconciled
it. Capture from both binaries and check `$OLD` against the snapshot in one pass:

```sh
capture() {  # $1 = binary, $2 = output dir
  mkdir -p "$2"
  "$1" --help              > "$2/codex.txt"          2>&1
  "$1" exec --help         > "$2/exec.txt"           2>&1
  "$1" review --help       > "$2/review.txt"         2>&1
  "$1" exec review --help  > "$2/exec-review.txt"    2>&1
  "$1" features list       > "$2/features-list.txt"  2>&1
  "$1" app-server --help   > "$2/app-server.txt"     2>&1   # not snapshotted; A/B only
}
capture "$OLD" "$SCRATCH/help-old"
capture "$NEW" "$SCRATCH/help-new"

diff -r docs/codex-help/<old-version> "$SCRATCH/help-old"   # authenticate: must be clean
diff -r "$SCRATCH/help-old" "$SCRATCH/help-new"             # the actual A/B
```

(The first `diff -r` reports `app-server.txt` as `Only in` — expected, no version carries a snapshot
of it. Everything else must match.)

**What that check does and does not establish.** It is a sanity check on identity, not a proof of
it: matching help text says the retrieved build presents the same CLI surface as the one verified
last time, which is good evidence it is the same build and no evidence about its generated schemas
or its behavior. Treat a difference found below as *associated with the version*, and reconcile it
against the release's changelog before recording it as one. (Why the check is needed at all: the
binary actually verified last time is gone, and these captures are the only surviving evidence of
it.) If no prior snapshot exists — the first time through this practice — you have no authenticator:
review the new surface in absolute terms and do not claim an A/B.

If npm is unreachable, fall back to reading the committed snapshots directly — a real, if narrower,
diff source. Every other surface below needs the old binary and has no offline substitute.

Then compare the surfaces the snapshots don't cover:
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

    No output means nothing was added or removed — `grep` exits 1 on a clean inventory, so don't
    read that status as failure. Keep the `grep`: unfiltered, `diff -rq` also lists every *differing*
    file, and those are mostly noise, because each generated file inlines shared definitions and one
    real change reverberates across dozens of them (0.144.1 → 0.145.0 listed 64 differing entries for
    what was, on the consumed surface, a single added field — observed 2026-07-21). Do not work
    through that list; the next pass is what reads content.
  - *Content* — diff only the schemas this plugin consumes. That is the comparison that decides
    whether anything we depend on moved. The list below must match `cli_contract.py`'s app-server
    block, which is authoritative — re-copy it from there rather than trusting this snippet to have
    aged well. Canonicalize both sides first: the generator is **not** byte-deterministic (two runs
    of the *same* binary emit `codex_app_server_protocol.v2.schemas.json` with different key order),
    so a raw diff reports drift that isn't there.

    ```sh
    for f in \
      v1/InitializeParams.json \
      v1/InitializeResponse.json \
      v2/ExternalAgentConfigImportParams.json \
      v2/ExternalAgentConfigImportResponse.json \
      v2/ExternalAgentConfigImportProgressNotification.json \
      v2/ExternalAgentConfigImportCompletedNotification.json \
      v2/GetAccountRateLimitsResponse.json
    do
      o="$SCRATCH/schema-old/$f"; n="$SCRATCH/schema-new/$f"
      if [ ! -f "$o" ] || [ ! -f "$n" ]; then echo "MISSING: $f"; continue; fi
      jq -S . "$o" > "$SCRATCH/a.json" && jq -S . "$n" > "$SCRATCH/b.json" \
        || { echo "UNREADABLE: $f"; continue; }
      if diff -q "$SCRATCH/a.json" "$SCRATCH/b.json" >/dev/null; then
        echo "same:    $f"
      else
        echo "CHANGED: $f"; diff "$SCRATCH/a.json" "$SCRATCH/b.json"
      fi
    done
    ```

    `MISSING` and `UNREADABLE` are findings, not skips: a consumed schema that vanished or stopped
    parsing is exactly the drift this pass exists to catch. Read the printed lines — do not infer the
    outcome from the loop's exit status.
- **Behavior with no CLI surface at all.** Some upstream changes have no flag and no subcommand —
  what reaches model context implicitly (auto-loaded `AGENTS.md`, discovered skills), and the
  feature flags that govern it. Run
  [`COMPATIBILITY.md`](../COMPATIBILITY.md) → "Implicit Codex context" → "Re-verifying on a Codex
  upgrade" against **both** binaries and compare the two presence matrices it produces. This A/B is
  what separates "new in this release" from "always true and we never looked" — an absolute-terms run
  cannot tell those apart. That section owns the fixture, the recording rules, and how to read a
  difference; three things are specific to running it twice:
  - **Drive the raw CLI, not this plugin's tools.** `cli_contract.py`'s `CODEX_BIN` is the bare name
    `codex`, so every plugin tool resolves it from `PATH` — a plugin consult cannot be pointed at
    `$OLD`, and two "different" runs would silently invoke the same binary. Invoke each explicitly
    and echo which one you used, so the run records its own provenance:

    ```bash
    for spec in "old:$OLD" "new:$NEW"; do
      tag=${spec%%:*}; bin=${spec#*:}
      ver=$("$bin" --version | tr ' ' '-')
      out="$tag-$ver"   # tag too: two binaries can report the same --version and clobber each other
      echo "=== $tag: $bin ($ver)"
      "$bin" exec --json --sandbox read-only --ignore-user-config --ignore-rules --ephemeral \
        --skip-git-repo-check --cd "$FIXTURE" -c model=<the same slug for both runs> \
        - < "$DISCOVERY_PROMPT_FILE" > "$out-discovery.jsonl" 2> "$out-discovery.err"
      rc=$?
      [ "$rc" -eq 0 ] || echo "INCONCLUSIVE: $bin exited $rc"
      jq -s -e -f no-tool-items.jq "$out-discovery.jsonl"   # the check from COMPATIBILITY.md
    done
    ```

    `--ignore-user-config` is required, not incidental — COMPATIBILITY.md explains which table row
    depends on it. **`--json` and the assertion are equally required**: without them the discovery
    consult cannot tell context codex loaded from a file the model read with its own shell, which is
    the defect that put a wrong row in that table until #478 retracted it (#480). `$FIXTURE`, `$DISCOVERY_PROMPT_FILE` (whose
    text must name no codeword and no skill), and `no-tool-items.jq` all come from that section — as does
    the separate body-egress consult, and the unprompted-selection consult, which this loop does not
    cover and which you still run per binary.
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
  catch a change to what reaches context implicitly or where it comes from.
- **New capabilities worth adopting or explicitly avoiding.** Don't stop at the script's flag `INFO`
  list — it only sees `codex exec` flags. Also scan the `Commands:` section of `codex --help` for new
  **subcommands** and run `codex features list` for new **feature flags** (the `--enable`/`--disable`
  surface). A release's most relevant new surface often lives there rather than in `codex exec`'s
  flags — e.g. 0.142 added the `features` subcommand and a native `codex exec review --output-schema`.
  Adopting any of these is a separate, deliberate change — not part of a version bump.
- **Feature flags this repo has already ruled on.** Scanning for *new* flags is not enough — a flag
  whose posture was decided can have its stage or default moved under you. Re-check each of these
  in `codex features list` and confirm the recorded posture still holds:
  `remote_plugin` ([`COMPATIBILITY.md`](../COMPATIBILITY.md) → "Remote-plugin isolation"; the
  plugin forces it off, and an upstream rename fails loud at arg-parse) and `view_image` /
  `recommended_plugins` ([`COMPATIBILITY.md`](../COMPATIBILITY.md) → "Image reading"; both left
  enabled/unreserved deliberately). If `view_image`'s stage or default moves, or the release notes
  touch image handling, re-run the no-auto-attachment A/B that section records — `codex debug
  prompt-input` with and without `--disable view_image`, **plus the `-i` positive control** without
  which a zero count proves nothing.
- **The read boundary.** `cli_contract.READ_SCOPE_FACT` says the sandbox bounds writes, not reads,
  and every agent-visible carrier states it ([`COMPATIBILITY.md`](../COMPATIBILITY.md) → "The read
  boundary: there isn't one"). It is a *ceiling* claim, so the upgrade risk runs one way: if a
  release ever DID confine reads, the disclosure would be over-broad rather than unsafe, and the
  correct response is to re-probe before narrowing anything. Re-run that section's two-tier probe
  when the release notes touch the sandbox, `--sandbox` values, or filesystem access. Keep the
  **write negative control** — without it a successful read proves nothing about the sandbox, only
  that it might not have been applied.
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
  cannot advertise, so the mechanical drift check can't see it. A key rename/removal is now caught
  by `--strict-config` (#524), which rides every effort-carrying run; what this manual step still
  guards — and the only thing strict cannot see — is a key that keeps its NAME and changes its
  MEANING, default, or accepted values. Run it every time.
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
- **Workspace-write network pin.** The propose tiers pin the no-network-egress guarantee with
  `-c sandbox_workspace_write.network_access=false` (`WORKSPACE_WRITE_NETWORK_ACCESS_CONFIG_KEY`,
  #518). Like the reasoning-effort key above, a rename/removal of the KEY is caught by
  `--strict-config` (#524); this semantic probe guards the case strict cannot see — a key that keeps
  its name while its meaning or default changes, silently re-granting egress from the user's
  `$CODEX_HOME/config.toml` (or a profile). It protects a security guarantee, so run it every time. The probe needs a **scratch** `$CODEX_HOME` (the config file is the thing under
  test), which must carry an `auth.json` copy — a credential — so run the whole block in the
  explicit subshell below, whose `EXIT` trap removes the copy even when a probe is interrupted
  (never paste the steps individually into an interactive shell). It assumes file-backed auth
  (`codex login`); each of the three runs spends a trivial request:

  ```bash
  (                                     # bounded: the trap dies with this subshell
    set -eu                             # fail fast: a failed cp/mktemp must not run the probes
    home=${CODEX_HOME:-$HOME/.codex}    # the active home, not a hard-coded path
    scratch=$(mktemp -d)
    trap 'rm -rf "$scratch"' EXIT       # the credential copy never outlives the probe
    cp "$home/auth.json" "$scratch/"
    printf '[sandbox_workspace_write]\nnetwork_access = true\n' \
      | tee "$scratch/config.toml" > "$scratch/net.config.toml"
    P="Run exactly this shell command and report its exact stdout and exit status verbatim: curl -sS -o /dev/null -w '%{http_code}' https://example.com"
    run() { CODEX_HOME="$scratch" codex exec --json --sandbox workspace-write \
            --ephemeral --skip-git-repo-check "$@" - <<< "$P"; }
    run                                                        # 1: positive control
    run -c sandbox_workspace_write.network_access=false        # 2: the pin
    run -c sandbox_workspace_write.network_access=false --profile net   # 3: pin vs profile
  )
  ```

  Judge each run by the `command_execution` item's `exit_code` in the `--json` stream, not the
  model's prose. Run 1 must report egress (curl exit `0`, HTTP `200`) — the positive control
  proving the probe can see the open state; without it a broken probe and a held guarantee look
  identical. Runs 2 and 3 must be blocked (curl exit `6`, could-not-resolve-host); run 3 shows
  the `-c` override still outranks profiles (verified 0.148.0, re-verified 0.149.1). If run 2 or 3 reports egress,
  the key drifted: update the constant and re-verify before shipping the version bump.
- **Workspace-write writable-roots pin.** The filesystem sibling
  (`-c sandbox_workspace_write.writable_roots=[]`, `WORKSPACE_WRITE_WRITABLE_ROOTS_CONFIG_KEY`,
  #520) has the identical meaning-change hazard (its rename half is covered by `--strict-config`,
  #524), so it gets the same probe treatment — same
  scratch-`$CODEX_HOME` discipline, same file-backed-auth assumption, and each of the three runs
  spends a trivial request. Two things differ from the network probe. Judge by **final on-disk state**, never the model's prose or the
  command's reported exit status. And the target directory must sit outside **every**
  default-writable root: `workspace-write` already permits the workspace plus `/tmp` and
  `$TMPDIR`, so a target under either temp root (e.g. anything `mktemp -d` returns) makes every
  run pass vacuously — a broken probe that looks exactly like a held guarantee. Use a target
  under `$HOME`, a **unique pre-absent file per run** (a file left by the positive control must
  not fake a later run's write), and clean the target directory in the same `EXIT` trap as the
  credential copy:

  ```bash
  (                                     # bounded: the trap dies with this subshell
    set -eu
    home=${CODEX_HOME:-$HOME/.codex}
    scratch=$(mktemp -d)
    # Outside the workspace AND both temp roots. mktemp both creates and names it, so a
    # collision can never send a pre-existing directory into the trap's rm -rf; the trap
    # is installed only after both directories exist.
    outside=$(mktemp -d "$HOME/.codex-fs-probe.XXXXXX")
    trap 'rm -rf "$scratch" "$outside"' EXIT
    mkdir "$scratch/work"               # work: the workspace, not the credential-holding scratch
    cp "$home/auth.json" "$scratch/"
    printf '[sandbox_workspace_write]\nwritable_roots = ["%s"]\n' "$outside" \
      | tee "$scratch/config.toml" > "$scratch/fs.config.toml"
    run() { local f="$outside/$1"; shift
            P="Run exactly this shell command, even if it fails, then reply DONE: touch $f"
            CODEX_HOME="$scratch" codex exec --json --sandbox workspace-write --cd "$scratch/work" \
              --ephemeral --skip-git-repo-check "$@" - <<< "$P" > /dev/null
            [ -e "$f" ] && echo "$f: WROTE" || echo "$f: denied"; }
    run 1.txt                                                        # 1: positive control (file)
    run 2.txt -c 'sandbox_workspace_write.writable_roots=[]'         # 2: the pin vs config file
    run 3.txt -c 'sandbox_workspace_write.writable_roots=[]' --profile fs   # 3: pin vs profile
  )
  ```

  Run 1 must report `WROTE` — the positive control proving the probe can see the open state.
  Runs 2 and 3 must report `denied`; run 3 shows the `-c` override still outranks profiles
  (verified 0.148.0). If run 2 or 3 writes, the key drifted: update the constant and re-verify
  before shipping the version bump. While here, also re-confirm upstream's
  `SandboxWorkspaceWrite` struct (codex-rs `config/src/types.rs` at the release tag) still has
  exactly the fields COMPATIBILITY.md's sandbox section accounts for — a NEW widening key would
  arrive unpinned and reopen the channel the #520 fix closed.
- **Structured output.** Run a small live `codex exec --output-schema <file>` and confirm the final
  message still conforms to the strict-mode schema in `schemas.py`. (Reminder, already in
  `COMPATIBILITY.md`: native `codex review --output-schema` is **not** honored for the final message
  — `codex_review_changes` must keep using `codex exec` with a diff we gather ourselves. Re-confirm
  this hasn't regressed before considering the native review subcommand.)
- **Strict-config grammar (zero spend).** The `--strict-config` guard (#524) is only as good as
  the stderr grammar it is parsed from: if upstream rewords the message,
  `parse_strict_config_rejection` stops matching and a pin drift degrades from
  `cli_contract_changed` to a generic `nonzero_exit` — the run still fails loudly, but the
  diagnosis is lost. Re-verify both shapes against a **scratch** `$CODEX_HOME`. Runs 1 and 2 die
  at config parsing, which precedes auth. Run 3 is the positive control and does **not** trip the
  guard, so it would go on to a real turn if it could authenticate — a scratch `$CODEX_HOME` alone
  does not stop that, because an exported `OPENAI_API_KEY` still authenticates (verified). Unset
  the key variables for the block and it spends nothing:

  ```bash
  (
    set -eu
    scratch=$(mktemp -d)
    # codex writes into $CODEX_HOME (a plugins clone) after run 3 returns, so cleanup can race it
    trap 'rm -rf "$scratch" 2>/dev/null || true' EXIT
    unset OPENAI_API_KEY CODEX_API_KEY   # load-bearing, not a formality: see above
    export CODEX_HOME=$scratch           # no auth.json here, so run 3 stops at auth
    # 1. override form -> unknown configuration field `bogus_key_xyz` in -c/--config override
    codex exec --strict-config -c bogus_key_xyz=1 \
      --skip-git-repo-check 'hi' </dev/null 2>&1 | head -3
    # 2. file form -> a <path>:<line>:<col>: span for the same phrase
    printf 'some_unknown_junk_key = true\n' > "$scratch/config.toml"
    codex exec --strict-config --skip-git-repo-check 'hi' </dev/null 2>&1 | head -3
    # 3. positive control: --ignore-user-config must EXEMPT the file (run gets past config)
    codex exec --strict-config --ignore-user-config \
      --skip-git-repo-check 'hi' </dev/null 2>&1 | head -3
  )
  ```

  Both rejections must still carry `Error loading config.toml` and ``unknown configuration field``,
  the override form must still name `in -c/--config override`, and run 3 must get past config
  parsing (it then fails on auth, which is the expected stopping point). Reconcile any wording
  change with `STRICT_CONFIG_ERROR_PREFIX` / `STRICT_CONFIG_OVERRIDE_ORIGIN_PHRASE` and the two
  patterns beside them, and refresh COMPATIBILITY.md's strict-config section. **Only encode
  phrasings from real observed output.**
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
