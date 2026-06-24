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

- The new `codex` is installed and authenticated (`codex login`).
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

This is the mechanical half only. Steps 2–3 are the judgment half the script cannot do.

## 2. Manual semantic + surface review (judgment — not automatable)

The script confirms shapes; you confirm meaning. Diff the new CLI's help against the **committed
snapshot of the previously verified version** under [`docs/codex-help/`](codex-help/) — an in-place
upgrade (Homebrew, `codex update`) destroys the old binary, so that snapshot is the only diff source
once you've upgraded. The snapshot covers `codex --help`, `codex exec --help`, `codex review --help`,
`codex exec review --help`, and `codex features list`. (If no prior snapshot exists — the first time
through this practice — review the new help in absolute terms instead.) Then check:

- **Flag semantics unchanged.** A flag the script found may have changed behavior. Spot-check the
  guarantee-bearing ones: does `--sandbox read-only` still block writes? does `workspace-write` still
  block network egress? does `--output-last-message` still receive the final message? does
  `--ignore-rules` still drop every policy source? A semantics change is a guarantee change even
  though the flag name is unchanged — treat it like a removal.
- **Sandbox values** (`read-only`, `workspace-write`, `danger-full-access`) still present and still
  mean the same boundary. Confirm the default paths still never emit `danger-full-access` or any
  `--dangerously-bypass-*`.
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
  comment's re-verified date.
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
literal** — `grep -rn '0\.141' src tests docs *.md` — and reconcile every hit. The table names the
usual ones, but treat the grep as authoritative: a stale enumerated list *will* miss a file (e.g.
`tests/test_check_codex_contract.py`'s `VERSION` was nearly missed this way).

| File | What changes |
|------|--------------|
| `src/codex_in_claude/cli_contract.py` | `SUPPORTED_VERSIONS`; the `Verified against …` / `0.x` comments; the `KNOWN_MODEL_SLUGS` provenance comment; any flag, sandbox, signature, or event-marker drift found in step 2 |
| Test version literals | Bump the literals that represent **the supported/current version** — `test_config.py`, `test_coverage_extra.py`, `test_codex.py`, `test_server.py`, and `test_check_codex_contract.py`'s `VERSION`. **Leave deliberate logic fixtures alone:** `test_check_codex_release.py` exercises the watcher's "new vs. tracked" logic with arbitrary versions, and `test_codex_models.py` uses a synthetic cache fixture — neither is the supported-set, so flipping them is wrong. |
| `docs/codex-help/<new-version>/` | Commit fresh `--help` + `features list` snapshots for the new version (the diff source for the *next* upgrade — see step 2) |
| `COMPATIBILITY.md` | the `Verified against` line; any changed policy |
| `README.md` | only if user-facing compatibility text changes (it carries no pinned literal otherwise) |
| `CHANGELOG.md` | an entry under `## [Unreleased]` |

Do **not** touch the package-release version set (`pyproject.toml`, `.claude-plugin/plugin.json`,
`.mcp.json` pin) here — those move only when cutting a release per `docs/RELEASING.md`.

## 5. Breaking vs. non-breaking

- **Non-breaking** (a codex bump usually is): adding/replacing a verified codex minor, refreshing
  advisory warnings, adding signatures for *existing* error codes, test/doc updates. No `FINGERPRINT`
  change.
- **Breaking** — bump `FINGERPRINT` in `schemas.py`, update the fingerprint test, note it in
  `CHANGELOG.md`, and follow the pre-1.0 minor-version release rules — when the **agent-visible
  surface** changes: tool names, params, result fields, value enums, error codes, schema shape; or
  any change that **weakens a documented guarantee** (sandbox/isolation, `--output-last-message`,
  structured-output enforcement). A codex change only forces this if you propagate it to our surface.

## 6. Verify before shipping

```sh
# fast contract-adjacent suites first, then the full gate
uv run pytest tests/test_cli_contract.py tests/test_preflight.py tests/test_codex.py tests/test_config.py
uv run pytest                                   # full suite, 95% coverage floor
uv run ruff check . && uv run ruff format --check . && uv run ty check
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
- A long-lived MCP server caches `codex exec --help` for `HELP_CACHE_TTL_SECONDS`; after an in-place
  upgrade it re-probes only once the TTL lapses. Restart the server (or wait it out) when validating.
- `codex login status` output may include account-identifying details — don't paste it into commits,
  issues, or PRs.
