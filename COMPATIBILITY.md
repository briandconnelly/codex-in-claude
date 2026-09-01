# Compatibility with the `codex` CLI

This plugin shells out to the OpenAI `codex` CLI. Every assumption it makes lives in
`src/codex_in_claude/cli_contract.py`, so an upstream change is centralized and greppable — though
incorporating one takes the lockstep procedure in
[`docs/UPGRADING-CODEX.md`](docs/UPGRADING-CODEX.md), not a single edit.
Design goal: **fail loudly and safely, never silently weaken a guarantee.**

Verified against `codex-cli 0.152.0`.

## Platform support

**macOS or Linux (POSIX) only.** The async-job safety layer — `fcntl` advisory locks
(pid-reuse / zombie-worker guards), process-group teardown (`os.killpg` /
`start_new_session`), and `SIGTERM`-driven graceful cancellation — is POSIX-only. On a
non-POSIX platform these guarantees quietly degrade to owned-children-only locking and
direct-PID kills that orphan `codex`'s child processes, so the server refuses to start
there instead of shipping a half-safe process model.

- [WSL2](https://learn.microsoft.com/en-us/windows/wsl/) reports `os.name == "posix"` and
  is unaffected (it *is* Linux).
- `CODEX_IN_CLAUDE_ALLOW_UNSUPPORTED_PLATFORM=1` downgrades the startup refusal to a
  stderr warning for operators who knowingly accept consult-only, unsupported use; do not
  use `codex_delegate`/`codex_review_changes` against untrusted work in that mode.

WSL2's Windows-PATH interop forwards Windows `PATH` entries into the WSL `PATH`, so a bare
`codex` lookup can resolve to a Windows-side npm-global shim instead of the WSL-native install.
`codex-in-claude` works around this automatically; the probe order lives in
[`src/codex_in_claude/binresolve.py`](src/codex_in_claude/binresolve.py) rather than being
restated here. To force a specific binary, set `CODEX_IN_CLAUDE_CODEX_BIN` (see the README's
Configuration table).

The `pyproject.toml` trove classifiers declare `Operating System :: MacOS` and
`Operating System :: POSIX :: Linux` (not `OS Independent`) so PyPI reflects this.

## What we invoke

- `codex exec --json --sandbox <mode> --cd <dir> --output-last-message <file> [--output-schema <file>]
  [--ephemeral] [--ignore-user-config] [--ignore-rules] [--skip-git-repo-check] [--add-dir <dir>]
  [--model <m>] -` — prompt delivered on **stdin** (the trailing `-`), keeping context out of argv.
- `codex app-server` — short-lived JSON-RPC sessions, driven by `codex_transfer` (session import)
  and by `codex_status`'s rate-limit read (`account/rateLimits/read`, no model spend — see #321).
  See "Session transfer" below.
- `codex --version`, `codex login status`, `codex exec --help` — free local probes.

Every paid call family — `codex_consult[_async]`, `codex_review_changes[_async]`, and
`codex_delegate[_async]` — runs its model work on `codex exec` alone. None uses the native
`codex review`/`codex exec review` subcommand, and none uses the `app-server`
JSON-RPC/broker protocol, which was the source of most of the upstream `codex-plugin-cc`
reliability issues. Reviews use `codex exec` with a diff we gather ourselves.

**Why not native review — re-verified at codex-cli 0.144.1 (issue #124).** `codex exec review`
now advertises `--output-schema`, `--output-last-message`, and `--json`, but `--output-schema` is
**accepted and silently ignored**. A clean-room run —

```sh
codex exec review --uncommitted --ignore-user-config --ignore-rules --strict-config \
  --ephemeral --output-schema <strict FINDINGS_OUTPUT_SCHEMA> --output-last-message <file> --json
```

— exits 0 yet writes free-form prose (not schema-conforming JSON) to the last-message file, and no
structured-findings payload appears anywhere in the `--json` event stream (only `command_execution`
items and one prose `agent_message`). Native review therefore can't back our strict result contract. Two lesser notes:
`codex exec review` has **no `--sandbox` flag** (config-based read-only control was not tested), and
the historical "output depends on the user's Codex MCP fleet" concern was **not re-tested** this
pass — it appears mitigable via `--disable remote_plugin`. Adopting native review remains blocked on
`--output-schema` alone; re-open the question only if a future Codex honors it.

Two flows reach the `app-server` surface: `codex_transfer` (transcript import) and `codex_status`'s
rate-limit read (`account/rateLimits/read`, added for #321 when codex 0.144 moved quota off the
`codex exec` stream). Both are quarantined the same way: the surface is experimental upstream, so
every assumption lives in `cli_contract.py` and `appserver.py`, neither call spends model tokens, and
no paid call depends on either. The rate-limit read verifies against **codex-cli 0.152.0** (probe:
drive `codex app-server` and confirm `account/rateLimits/read` returns a quota block; an integration
test does this live). See "Session transfer" below for the import flow.

## Sandbox modes

`--sandbox` is the capability boundary for a run (`cli_contract.py`): `read-only` for the
consult/review tiers, `workspace-write` for the propose tiers (`codex_delegate`,
`codex_delegate_async`); we never pass `danger-full-access` or `--dangerously-bypass-*` by default.

**`workspace-write` permits filesystem writes inside the workspace (plus the OS temp roots — see
the temp-root note below) but blocks network egress.** This
is codex's own sandbox boundary and we pass it through deliberately. The practical consequence: a
propose/apply task **cannot perform network operations** — `git push`/`fetch`, `gh ...`, `curl`,
`npm publish`, dependency installs, etc. all fail inside the sandbox (typically with a
`Could not resolve host` / DNS error). Delegated tasks must therefore be self-contained; do any
network step yourself after reviewing and applying the returned diff. The tool docstrings and the
`codex_capabilities` `negative_scope` state this so a calling agent doesn't assume write access
implies internet access.

Codex itself makes that boundary configurable — `[sandbox_workspace_write] network_access = true`
in `$CODEX_HOME/config.toml` (or a profile) re-grants egress inside `workspace-write` — and at the
default `inherit` isolation the user's config file is read, so a user who enabled that key for
their own interactive codex use would silently void the promise above (#518). The server therefore
**pins the guarantee**: every `workspace-write` run sends
`-c sandbox_workspace_write.network_access=false`
(`cli_contract.WORKSPACE_WRITE_NETWORK_ACCESS_CONFIG_KEY`). The `-c` override outranks both the
config file and an operator `--profile` (codex resolves by config layer, not argv order; verified
live on 0.148.0 and re-verified on 0.149.1, 0.151.0 and 0.152.0 with positive controls), so the promise holds at every
isolation level, and for
this one key the `--profile` operator-trust carve-out is closed. This deliberately overrides the
user's own setting for plugin-launched runs only; their interactive codex sessions are untouched.
A `-c` KEY cannot fail loudly the way a flag does — codex ignores an unknown key — so an upstream
rename would once have reopened the channel silently. `--strict-config` now closes that (see
**Strict config validation** below), leaving only a same-name change of *meaning* to the semantic
probe in `docs/UPGRADING-CODEX.md`, which still runs on every version change.

The filesystem half of the sentence above — the sandbox's write boundary — gets the same
treatment (#520): every `workspace-write` run also sends
`-c sandbox_workspace_write.writable_roots=[]`
(`cli_contract.WORKSPACE_WRITE_WRITABLE_ROOTS_CONFIG_KEY`), so a user's own
`writable_roots = [...]` — extra writable directories outside the workspace, reasonable for their
interactive codex use — cannot silently widen a plugin-launched run. `[]` is codex's own default,
so default-config runs are unchanged. The same precedence facts apply (verified live on 0.148.0 and
re-verified on 0.149.1, 0.151.0 and 0.152.0,
macOS, positive controls judged by on-disk state): the `-c` override outranks the config file and
`--profile`, the same drift coverage holds (`--strict-config` catches a KEY rename; the
`docs/UPGRADING-CODEX.md` semantic probe covers a same-name change of meaning), and the user's
interactive sessions are untouched.

Two deliberate bounds on that filesystem pin. First, it restores codex's *default* boundary
rather than tightening it: the default already grants writes to the OS temp roots (`/tmp` and
`$TMPDIR`), the pins do not close that grant, and the remaining `sandbox_workspace_write` keys
(`exclude_tmpdir_env_var`, `exclude_slash_tmp`) are deliberately **not** pinned — their default
(`false`) is the widest state, so the config file can only *narrow* them, which is the operator's
own prerogative (an operator who sets one merely denies plugin-launched shell commands the
matching temp root). The default temp-root grant is disclosed across the propose-tier surface
itself (#523): the worktree does not bound Codex's writes, the tool descriptions and docs say
so, and the delegate tools advertise `destructiveHint: true` because a task can overwrite
pre-existing files under those roots. Second, the pin binds the *config* layer only: the `--add-dir` **flag** layer
outranks it (verified in the same probes), which is why `--add-dir` stays reserved for
plugin-owned use and no model-bearing call sends it today (see the builder's note in
`codex.py`). Upstream completeness — that no *new* `sandbox_workspace_write` key has appeared
unpinned — is re-checked against the upstream struct on every version change
(`docs/UPGRADING-CODEX.md`).

## Remote-plugin isolation (`remote_plugin`, #287)

Codex **0.143+** flipped the `remote_plugin` feature to **default-on**, which makes named
third-party connectors (GitHub, Gmail, Google Drive, Slack, Notion, …) available to the model on
every run. Those connectors are network side-effect / data-disclosure channels **outside** the
`--sandbox` filesystem boundary, so they are incompatible with this server's safe, read-only-by-default
posture. Crucially, `--ignore-user-config` does **not** neutralize them: plugins load from marketplace
snapshots (`~/.cache/codex-runtimes/`, `~/.codex/.tmp/bundled-marketplaces/`), not `config.toml`.

The server therefore sends **`--disable remote_plugin`** on **every model-bearing `codex exec` call**,
regardless of tier or isolation (`cli_contract.py`: `DISABLE_FEATURE_FLAG` + `REMOTE_PLUGIN_FEATURE`,
emitted in `codex.build_exec_command`). It is an **ALWAYS_SEND** guarantee-bearing flag:

- `--disable <FEATURE>` is documented as exactly `-c features.<FEATURE>=false`, and it wins over any
  `--enable`/`-c features.remote_plugin=true` **regardless of order**.
- An **unknown feature name fails loud** (`Error: Unknown feature flag`), so a future rename/removal of
  `remote_plugin` upstream surfaces as `cli_contract_changed` at arg-parse — zero spend — rather than a
  silent posture regression.

**How to re-verify this on an upgrade, and what the check can and cannot reach.** The guarantee has
two halves, and only one of them is reliably probeable on an arbitrary machine.

- **The mechanism half — always run it, zero spend.** Read `remote_plugin` out of `codex features
  list` under each argv shape and confirm all four: the upstream default is readable (it is `true`,
  which is the positive control — without it a `false` reading proves nothing), `--disable` drives
  it to `false`, `--disable` still wins against both an `--enable` in either order and an explicit
  `-c features.remote_plugin=true`, and an unknown feature name still fails loud. `tests/
  test_integration.py::test_remote_plugin_disabled_by_plugin_flag_live` covers most of this; the
  `-c` arm and the unknown-name arm are the upgrade-time additions. Verified on `0.148.0`,
  re-verified identically on `0.149.1` (2026-08-25), `0.151.0` (2026-08-29) and `0.152.0` (2026-09-01).
- **The tool-surface half — run it only when a positive control exists.** Proving that no connector
  *tool* is exposed requires a machine where a connector actually IS exposed when the feature is
  enabled. On a machine with no connector installed, both arms of that A/B come back empty and the
  "absent under `--disable`" reading is a negative from a blind instrument, indistinguishable from a
  silently broken guarantee. **Check for a positive control first** (`codex plugin list` — a
  connector under a remote marketplace must read `installed, enabled`); if there is none, record
  that the half was **not exercised** rather than recording a pass. Two instruments that look like
  they would work do not: `codex debug prompt-input` renders *input items*, never the tool list, and
  the skills a live `codex exec` reports from a remote marketplace cache appear only intermittently,
  so their absence is not evidence either. This was the state on 2026-08-25 for `0.149.1` — every
  `openai-curated` connector read `not installed` — so that half is **unverified at `0.149.1`**, as
  it was at every earlier version, and the posture rests on the mechanism half plus the source
  reading below (#548).
- **When the release touches plugin gating, read the source too.** `0.149.0` carried upstream
  "Remove the workspace settings gate for apps and plugins", which sounded adjacent to this
  guarantee. Its diff touches only the `app-server` and `chatgpt` crates — nothing under `core`,
  `exec`, or `tools` — and it moves control of plugin APIs *toward* the effective feature
  configuration, which is exactly the input `--disable remote_plugin` sets. So it does not reach
  the `codex exec` path this guarantee covers.

**Scope and boundary.** The guarantee covers model-bearing `codex exec` calls (consult/review/delegate);
it does not describe the separate `codex app-server` path used by `codex_transfer` (no model call). And
like the sandbox/approval `-c` denials below, it is bounded by the **`--profile` operator-trust
boundary** — an opaque profile this server cannot inspect could re-enable the feature, so only enable
that knob with profiles you control.

## Image reading (`view_image`, #479) — deliberately left enabled

Codex **0.147+** ships a `view_image` feature, stage `stable`, default **on** (unchanged at
`0.148.0`, `0.149.1`, `0.151.0`, and `0.152.0`). This plugin neither sends nor refuses it, and that is a decision, not an oversight.
The question #479 raised was whether it widens egress the way `remote_plugin` did, since a
`read-only` sandbox bounds *writes*, not what gets read and sent.

**It is a model-invoked tool, not implicit context.** The tool's own JSON schema is in the 0.148.0
binary: `view_image` — "View a local image file from the filesystem when visual inspection is
needed" — takes `path` ("Local filesystem path to an image file.") and an optional `detail`
(`high` | `original`). The binary also carries `core/src/tools/handlers/view_image.rs` and
`ViewImageToolCall*` events. So `view_image` puts an image in context only after the **model
issues a call naming a file** — categorically unlike `AGENTS.md`, whose content is in context
before the turn begins (see "Implicit Codex context" below). (That is a statement about this
feature, not about every image path: `codex exec -i <file>` attaches one with no model call at
all. This plugin never passes `-i`.)

**Probe: no auto-attachment** (zero spend, `codex-cli 0.148.0`; re-run as an A/B against
`0.149.1` on 2026-08-25 with an identical result on both binaries, and re-run again on `0.151.0`
on 2026-08-29 — same result, live positive control). In a scratch git repo containing
`secret_marker.png`, `codex debug prompt-input` — which renders the model-visible prompt input list
as JSON — produced **zero** `"type": "input_image"` items, and its output was byte-identical with
and without `--disable view_image` apart from message UUIDs and timestamps. **Positive control:**
the same command with `-i secret_marker.png` produced exactly one `input_image` item, so the
negative is a real observation and not a broken instrument. Note the probe's bound: it renders
*input items*, **not** the tool list, so it says nothing about whether the tool is offered — that
comes from the schema above.

**Why this is not the `remote_plugin` case.** `remote_plugin` was disabled (above) because
connectors are a network data and side-effect channel **outside** the sandbox entirely.
`view_image` reads a local file into the OpenAI model call this plugin already discloses. The
data-flow category is unchanged: local readable file → model-directed read → the same model call.
Decisively, **disabling it would buy no containment.** The read-only tiers already give the model a
shell over the same filesystem, so `--disable view_image` would remove no file from reach — it
would only remove the model's ability to interpret pixels, while breaking legitimate requests
("look at this screenshot") and adding a permanent fail-loud feature-name obligation.

**No disclosure change, so no fingerprint effect.** The published egress disclosures are
deliberately **modality-neutral** — `README.md`, the tool descriptions, and the
`readonly_honesty_statement` say "files" and "their content", never "text files". Amending them to
name images would imply that other modalities had been excluded, and would churn the agent-visible
surface without changing the promise. `SKILLS_DISCOVERY_FACT` is a narrow disclosure about
*implicit* context, which `view_image` is not, so it does not belong there either.

**What would reopen this.** Any one of: a default `prompt-input` run containing an `input_image`
before any model tool call; evidence of directory scanning with automatic image attachment; the
handler reading a path the shell/filesystem policy cannot; or image data leaving through a
non-OpenAI channel. A merely *unprompted* model call does **not** reopen it — an unprompted shell
read is already possible and already disclosed.

**The read boundary is verified equal to the shell's (#507).** This was the one open question that
could have flipped the decision, and it was closed two ways rather than by assuming `--sandbox`
covers it (it does not: `codex exec --help` scopes that flag to "model-generated **shell
commands**", which a native handler is not).

- **Source**, at the `rust-v0.148.0` tag: the handler routes **both** `get_metadata` and
  `read_file` through `turn.file_system_sandbox_context(...)`, so its reads are sandbox-mediated
  rather than raw filesystem access. Upstream carries a test named
  `handle_passes_sandbox_context_for_local_filesystem_reads` asserting exactly that.
- **Exercised**, at `0.148.0` under this plugin's own flags (`--sandbox read-only --cd <ws>
  --ephemeral --skip-git-repo-check --disable remote_plugin`): `view_image` read and transmitted a
  PNG in `$HOME` — outside the workspace and outside any repo — and the model reported its colour
  correctly across **three** distinct pairs (blue/red, then green/yellow, then magenta). The
  filenames were colour-neutral (`a.png`, `b.png`) and the only shell command available in those
  runs revealed no colour (an 8-byte header dump; a `cat` of an unrelated text file), so the
  answers can only have come from the pixels. The same runs' shell reads of the same outside paths
  also succeeded.

So `view_image` reaches **no file the shell cannot**, and disabling it still buys no containment —
#479's posture now rests on evidence rather than on the argument it was originally filed with.

One thing that verification exposed remains open, and is tracked separately because it is not about
`view_image`'s posture: a successful `view_image` call emits **no item** in the `codex exec --json`
stream — failures reach only stderr — so image egress is invisible to anything parsing it, this
plugin included (#510).

## Sleep tool (`sleep_tool`, `clock.sleep`) — assessed at 0.152.0, not disabled

Codex **0.152.0** added a `sleep_tool` feature, stage `stable`, default **on**. It is the one new
flag in that release with a model-facing surface, so it was assessed rather than rubber-stamped.

**What it is.** When exposed, the model receives a `clock` tool **namespace** whose single function
is `sleep`: "Pause execution for a specified duration. The sleep ends early when new input arrives
for the active turn. Returns the elapsed wall-clock time." Its one parameter, `duration_ms`, is
documented as **"Must be between 1 and 43200000"** — up to **12 hours**, far beyond this server's
sync deadline (built-in 300s) and its async deadline (built-in 1800s).

**Exposure is conditional, and it was NOT exposed on the default path at 0.152.0.** The tool is
registered by the ordinary `codex exec` tool planner, gated on the feature's `mode`:

- `mode = "model_driven"` (the default) exposes it only when the selected model's
  `experimental_supported_tools` advertises `clock`. On 2026-09-01 that array is **empty for all
  eight** slugs in the `models_cache.json` observed here. That cache is account-scoped and
  backend-populated (see `KNOWN_MODEL_SLUGS`' provenance note in `cli_contract.py`), so the correct
  reading is **"none of the eight models in the observed cache"** — not "no model anywhere". Another
  account, or a later catalog refresh, could advertise `clock` and expose the tool with no CLI
  change.
- `mode = "always_on"` exposes it regardless of model metadata.

**How that was established** (zero spend — the sink answers 400 before any model call).
`scripts/capture_wire_tools.py` points codex at a local HTTP sink instead of the real API and prints
the `tools` array out of the request codex actually sent. That array is the evidence, not the
model's self-report — asked directly, the model answered `NOTOOL`, which is a claim, not an
observation. Each row below is one runnable command:

```sh
uv run python scripts/capture_wire_tools.py                                              # default
uv run python scripts/capture_wire_tools.py -- -c 'features.sleep_tool.mode="always_on"'
uv run python scripts/capture_wire_tools.py -- --disable sleep_tool -c 'features.sleep_tool.mode="always_on"'
uv run python scripts/capture_wire_tools.py -- --disable view_image                      # positive control
uv run python scripts/capture_wire_tools.py --bin "$OLD"                                 # the A/B's other half
```


| Run | `clock` in the request? |
|---|---|
| Default flags | **No** |
| `-c 'features.sleep_tool.mode="always_on"'` | **Yes** (the `clock` namespace, with `sleep`) |
| `--disable sleep_tool` **plus** `mode="always_on"` | **No** — the flag outranks the mode |
| `--disable view_image` (positive control) | `view_image` disappears |

The positive control is what makes the first row evidence: without it, a blind capture and a genuine
absence look identical. Do **not** record the absence as architectural — an earlier reading of this
probe wrongly concluded the tool was interactive/app-server-only. It is on the `codex exec` code
path; the tool was simply **gated off** for these models, because none of them advertised
`clock`.

**Posture: assessed, left enabled, not sent.** The plugin does not send `--disable sleep_tool`
today, because on the default path there is nothing to disable. That posture rests on **backend
model metadata**, which can change with no CLI upgrade and no version bump to notice it: a
`models_cache.json` refresh that adds `clock` to `experimental_supported_tools` would expose the
tool under the default `model_driven` mode. An operator can also reach it directly through
`CODEX_IN_CLAUDE_EXTRA_ARGS`. Adopting the disable is tracked separately as a deliberate
behavior change, per `AGENTS.md` → the rule that adopting or avoiding a new capability is not part
of a version bump.

**Why it would matter if exposed.** The deadline still binds — this server terminates the run — so
the failure mode is not an unbounded hang. It is **spend without result**: a single `sleep` call can
consume the whole budget and turn a run that would have succeeded into a `timeout`.

**Re-check on every upgrade.** `docs/UPGRADING-CODEX.md` step 2A owns this as a required check.
Re-run `scripts/capture_wire_tools.py` against both binaries **and** re-read
`experimental_supported_tools` in the live model cache
(`jq -r '.models[] | "\(.slug): \(.experimental_supported_tools)"' "$CODEX_HOME/models_cache.json"`),
not just the feature's stage and default — the stage and default did not move when the exposure
gate did.
`--disable sleep_tool` is verified to work and an unknown feature name still fails loud as
`Error: Unknown feature flag`, so adopting the disable later stays cheap.

## Default tool-catalog changes are invisible to `--help` (`update_plan`, 0.152.0)

Codex **0.152.0** removed `update_plan` from the default tool set; `-c tools.update_plan.enabled=true`
restores it (both confirmed by the same sink capture, 0.151.0 vs 0.152.0). This plugin references
`update_plan` nowhere and parses the JSONL stream tolerantly, so **no contract or parser dependency
here changed**. That is narrower than "nothing changed": the model-facing tool catalog genuinely
did change, and a Codex run may behave differently for it — this plugin simply has no stake in the
difference.

It is recorded because of **how it was found, not what it was**: every `--help` surface was
byte-identical for this release, and the `codex features list` diff (four added rows, none removed)
named nothing resembling this change — neither surface can see it at all. The model-facing request
is a distinct surface. `scripts/capture_wire_tools.py` is the only probe here that sees it, so run
it as an A/B — old binary and new — on every upgrade and diff the `tools` array;
`docs/UPGRADING-CODEX.md` step 2A carries it as a required step.

## The read boundary: there isn't one (#509)

The sandbox bounds writes, not reads. Neither `read-only` nor `workspace-write` confines what
Codex may read: it can read files outside the workspace, up to everything the OS user running it can
read, and send them to OpenAI. No `--cd` / `workspace_root` you can pass is a read boundary.

Verified on **codex-cli 0.148.0**, re-verified on **0.149.1** (2026-08-25), under this plugin's own
`ALWAYS_SEND` flags (`--sandbox <tier>
--cd <ws> --json --output-last-message --skip-git-repo-check --ephemeral --ignore-user-config
--ignore-rules --disable remote_plugin`), with the workspace a bare non-repo directory under `/tmp`:

| Tier | Probe | Result |
|---|---|---|
| `read-only` | model shelled out: `/bin/zsh -lc 'cat $HOME/<marker>.txt'` | codeword returned |
| `read-only` | **negative control** — write to `$HOME` | refused: "blocked by read-only sandbox"; no file created |
| `workspace-write` | same `$HOME` read | codeword returned |

The negative control is what makes the read informative: it shows the sandbox **was** in force, so
the read is evidence about what the sandbox bounds rather than about a dropped flag. `view_image`
reaches the same paths as the shell (see above, #507).

Stated as a **ceiling, not a warrant**: the probe establishes reads far outside the workspace, not
that every file the OS user can read is reachable — a platform sandbox (macOS TCC) can still deny
individual paths to this process. `cli_contract.READ_SCOPE_FACT` is the canonical wording every
carrier states; the `RULE` comment beside it lists them.

`recommended_plugins` is `stable`/default-**off** at `0.148.0`, `0.149.1`, `0.151.0`, and `0.152.0`, and is left unreserved on the same
reasoning as above — adjacency in the feature table is not evidence that it bypasses the
`remote_plugin` guarantee. [`docs/UPGRADING-CODEX.md`](docs/UPGRADING-CODEX.md) owns the
obligation to re-check both flags on each upgrade.

## Implicit Codex context (`AGENTS.md`, both skills roots, #300, #358)

`codex exec` **automatically loads** guidance from three `AGENTS.md` sources into model context and
**auto-discovers** skills from two roots (per upstream docs: name/description metadata up front; a
skill's body is read in when the skill is selected):

- the **resolved workspace's own `AGENTS.md`**;
- **inside a repository, every ancestor `AGENTS.md`** from the resolved workspace up to the
  repository root — the walk crosses **above the directory the caller selected** and stops **at**
  the repository root without crossing it. Outside a repository there is no walk at all: only the
  workspace's own file loads (#472);
- **`$CODEX_HOME/AGENTS.override.md`, else `$CODEX_HOME/AGENTS.md`** — **user-global**, loaded on
  every call from **any** workspace, the `AGENTS.md` twin of the global skills root below (#472);
- the workspace's **`.agents/skills/`** — project-level, and
- **`$CODEX_HOME/skills/`** (default `~/.codex/skills/`) — **user-global, discovered from outside
  the workspace**, so no choice of workspace excludes it.

**Why the ancestor walk matters.** `resolve_workspace` (`pontonier.core.workspace`) returns an
explicit `workspace_root` **unchanged**, so a subdirectory *stays* the resolved workspace. A caller
who narrows `workspace_root` to `repo/sub` precisely in order to bound egress still ships
`repo/AGENTS.md` — a file it never named, outside the directory it selected.

The caller directs none of it, and every model-bearing call in this plugin runs `codex exec`, so
that content can reach OpenAI even when the caller's prompt never mentions those files. Be precise
about *how* each part arrives, because the two differ: the `AGENTS.md` content and the skill
name/description are already in the model's context when the turn begins — codex reads them itself
while assembling the prompt, so the model issues no read for them — while a selected skill's
**body** was observed arriving by a read the **model** itself issues (0.147.0, 0.148.0, 0.149.1,
0.151.0, 0.152.0). Both are egress the caller never
asked for — a global skill's body came back from a prompt that named neither the skill nor the file,
the model having selected it on its auto-loaded description alone (probed 2026-08-18) — but only the
first is auto-loading. Verified
empirically against codex-cli 0.147.0 (2026-08-07, issues #300 and #358), re-verified 2026-08-18
under the read-forbidding probe below, re-verified again 2026-08-19 against codex-cli 0.148.0, and
re-verified once more 2026-08-25 against codex-cli 0.149.1, and again 2026-08-29 against
codex-cli 0.151.0, and again 2026-09-01 against codex-cli 0.152.0 — each A/B (against 0.146.0, then
0.147.0, then 0.148.0, then 0.149.1, then 0.151.0) produced an identical presence matrix under that same probe, so the
user-global discovery is pre-existing rather than new. The 0.149 run is worth naming: that release
reworked skill selection and added an upstream change titled "Enforce filesystem permissions when
loading AGENTS.md", and the matrix still did not move. The 0.151 run is worth naming for a different
reason: 0.151 introduced a `skip_host_skill_discovery` feature flag, `under development` and
default-**off**, whose name describes precisely the behavior this section documents. It is inert at
that stage — the matrix did not move — but it is the flag to re-probe first when it stages. It is
still `under development`/**off** at 0.152.0 (2026-09-01), and the matrix again did not move.

**The `AGENTS.md` sources, probed 2026-08-19 (`codex-cli 0.148.0`, #472), re-probed 2026-08-25
(`codex-cli 0.149.1`, #542), again 2026-08-29 (`codex-cli 0.151.0`) and again 2026-09-01
(`codex-cli 0.152.0`, #586) — identical results on every binary.** Every run below used
the read-forbidding probe defined further down — the `--json` stream asserted free of tool items,
so a codeword in the answer can only have arrived as auto-loaded context. Fixture:
`<parent>/repo` (git root) with `repo/sub` as the resolved workspace, a distinct codeword in each
`AGENTS.md`, run with `--cd repo/sub`:

| Marker | Location | In context? |
|---|---|---|
| `MARMOSET7` | `repo/sub/AGENTS.md` — the resolved workspace | **YES** (positive control) |
| `OTTERGATE3` | `repo/AGENTS.md` — ancestor of the workspace, at the git root | **YES** |
| `PELICANFIVE` | `<parent>/AGENTS.md` — above the git root | **NO** |
| `LYNX33` / `HERON22` / `BADGER11` | `top/mid/leaf` with **no git repo anywhere**, `--cd .../leaf` | leaf **YES**, `mid` and `top` **NO** |
| `WALRUS44` | `$CODEX_HOME/AGENTS.md`, no override present | **YES** |
| `NEWT55` | `$CODEX_HOME/AGENTS.override.md` | **YES**, and it masks `AGENTS.md` |

Three further negatives, each a differential against an otherwise identical run that *did* show the
markers — so none is a trivial negative from a broken instrument:

- **`--cd` versus process cwd makes no difference.** Running with the process cwd set to `repo/sub`
  and no `--cd` loaded exactly the same two files.
- **`-c project_doc_max_bytes=0` suppresses the workspace and ancestor files** — no project codeword
  reached context — **but not the `$CODEX_HOME` file**, which still arrived. It is a
  project-guidance off-switch, not a global one. (This resolves the question this section
  previously flagged as unverified; it does **not** make the knob a containment mechanism, and the
  plugin does not expose it.)
- **`--ignore-user-config` does not suppress the `$CODEX_HOME` guidance file** either, exactly as it
  fails to suppress `$CODEX_HOME/skills/`.

The behavior is invisible in
`codex exec --help` (no flag, no subcommand), so the mechanical help-drift check cannot catch
upstream changes to it. Upstream docs:
[AGENTS guidance](https://developers.openai.com/codex/concepts/customization#agents-guidance) and
[skills](https://developers.openai.com/codex/concepts/customization#skills).

**The isolation flags do not suppress it.** `--ignore-user-config` and `--ignore-rules` cover
specific `$CODEX_HOME` state — `config.toml` and execpolicy `.rules` respectively — and **not** any
`AGENTS.md` source, `.agents/skills/`, or `$CODEX_HOME/skills/`; no `isolation` value changes this.
The user-global cases are the surprising ones: `--ignore-user-config` reads as broad user-level
isolation but drops only `config.toml`, and a probe run *with* that flag still emitted a
`$CODEX_HOME/skills/` skill body **and** the `$CODEX_HOME` guidance file's codeword. (The plugin's default `isolation=inherit` does not even send the flag — see
`config.isolation_flags`.) For the delegate tools the `AGENTS.md`/skills seeded into the throwaway
worktree (committed content plus replayed uncommitted tracked changes; untracked files are not
copied) apply there too — that `AGENTS.md` auto-loads and those skills are discovered — and the
user-global skills are discovered alongside them, neither tracked nor seeded, so scrubbing the
worktree does not exclude them. A skill selected in the worktree has its body read there just the
same.

### Re-verifying on a Codex upgrade

Marker probes are the only way to observe any of this. Run them against **both** the new binary and
the previous one; [`docs/UPGRADING-CODEX.md`](docs/UPGRADING-CODEX.md) step 2A covers retrieving the
old binary. **Every row of the table below needs its own marker** — a row whose marker is absent produces a trivial negative that looks identical to a real
one, so the probe would report "unchanged" no matter what upstream did. Build the fixture as
`<parent>/repo`, where `repo` is a scratch git repo (`git init` + one commit) and `<parent>` is
**outside** it, with `repo/sub` as the resolved workspace (`--cd repo/sub`) and a distinct codeword
in each of these seven places:

| Marker | Path | Counts as present when | Tests |
|---|---|---|---|
| Workspace `AGENTS.md` | `repo/sub/AGENTS.md` | its **codeword** is in context | positive control |
| Project skill | `repo/sub/.agents/skills/<name>/SKILL.md` | its **name** is in the skill inventory | positive control |
| Global skill | `$CODEX_HOME/skills/<name>/SKILL.md` — **temporary** | its **name** is in the inventory (discovery); its **codeword** comes back after selection (body egress) | row 1 |
| Claude-dir skill | `repo/sub/.claude/skills/<name>/SKILL.md` | its **name** is in the skill inventory | row 2 |
| Parent `AGENTS.md` | `<parent>/AGENTS.md` (above the git root) | its **codeword** is in context | row 3 |
| Ancestor `AGENTS.md` | `repo/AGENTS.md` (inside the repo, above the workspace) | its **codeword** is in context | row 4 |
| Global `AGENTS.md` | `$CODEX_HOME/AGENTS.md` — **temporary** | its **codeword** is in context | row 5 |

The workspace is a **subdirectory** of the git root on purpose: with `--cd repo` the ancestor row
would be indistinguishable from the workspace's own file, and the walk that #472 corrected would be
invisible.

**The two `$CODEX_HOME` markers are the destructive part of this fixture; treat them accordingly.**
They must live in the **real** `$CODEX_HOME`, because that is the path under test — a scratch
`CODEX_HOME` would also have to carry a copy of `auth.json`, which is a credential and should not be
copied around. So the guidance marker is created in a directory that may already hold the
maintainer's own guidance file. Put the whole probe in a **script or an explicit subshell** — never
paste this into an interactive shell, where the `EXIT` trap outlives the probe and can delete a
guidance file you legitimately create later in the same session:

```sh
(                                      # bounded: the trap dies with this subshell
  set -u
  home=${CODEX_HOME:-$HOME/.codex}     # resolve ONCE; every later step reuses $home
  created=()
  # -e misses a DANGLING symlink, which the cleanup would then delete: test -L as well.
  for f in "$home/AGENTS.md" "$home/AGENTS.override.md"; do
    if [ -e "$f" ] || [ -L "$f" ]; then
      echo "REFUSING: $f already exists — move it aside and re-run"; exit 1
    fi
  done
  trap 'rm -f "${created[@]}"' EXIT    # fallback only; remove what THIS probe created
  printf 'Global guidance. Codeword: <codeword>\n' > "$home/AGENTS.md"; created+=("$home/AGENTS.md")
  ...                                  # run the discovery consults below
  # Explicit cleanup on the success path. Clear the tracking array ONLY once removal
  # succeeded and the paths are gone — clearing unconditionally leaves the EXIT fallback
  # with nothing to retry, so a failed rm would strand a marker in the real $CODEX_HOME.
  if rm -f "${created[@]}" && ! ls -d "${created[@]}" >/dev/null 2>&1; then
    created=()
  else
    echo "CLEANUP FAILED — remove these by hand: ${created[*]}" >&2; exit 1
  fi
)
```

Clean up explicitly on success and keep the trap as the fallback for the failure path — a trap that
is the *only* cleanup silently becomes a no-op the day someone runs the steps by hand. A forgotten
`$CODEX_HOME/AGENTS.md` joins **every** later Codex call on that machine, this plugin's included.

**Two variants are needed beyond the ordinary discovery run, because the results table records
behavior the ordinary run cannot reproduce.** A recorded observation whose procedure cannot
re-derive it is exactly the stale-record failure this section exists to prevent:

- **Override precedence.** Create **both** guidance files at once, with **distinct** codewords, and
  assert that only the `AGENTS.override.md` codeword arrives. Probing them one at a time shows each
  loads on its own and says nothing about which one wins.
- **`project_doc_max_bytes=0`.** Repeat the discovery run with `-c project_doc_max_bytes=0` added,
  against the same fixture, and record the workspace/ancestor markers and the `$CODEX_HOME` guidance
  marker separately — the knob suppresses the first two and not the third, so one undifferentiated
  boolean would hide a change to either half.
- **No enclosing repository.** Build a second fixture — `top/mid/leaf`, **no `.git` anywhere** — with
  a codeword at each level, run with `--cd .../leaf`, and record all three markers. Only the leaf's
  own file loads; without this variant the "no walk outside a repository" claim rests on nothing the
  procedure runs, since the main fixture is always a repository.
- **Process cwd instead of `--cd`.** Repeat the ordinary discovery run with the process cwd set to
  `<parent>/repo/sub` and **`--cd` omitted**. The markers must match the `--cd` run exactly; that
  equality is the recorded claim, so a divergence is the signal.

Capture each variant under its own `$OUT` name and run the same clean-stream assertion over it; a
variant that fails the assertion is discarded like any other run.

**The "present" column is not uniform, and that is the point.** An `AGENTS.md` auto-loads as
*content*, so its codeword is the evidence; a skill auto-discovers as *name and description only*,
so its name is. Recording one undifferentiated boolean per marker hides which observable moved —
a matrix that reads "unchanged" while the underlying evidence changed from codeword to name is
exactly the kind of false continuity this section exists to prevent.

Each `SKILL.md` needs YAML frontmatter — `---` / `name: <name>` / `description: <one line>` / `---`
— then the codeword in the body. **A skill without frontmatter is silently not discovered**, which
would make a "not discovered" result indistinguishable from upstream having changed. The two
positive controls are what make the negatives meaningful: if the project `AGENTS.md` **codeword** or
the `.agents/skills/` marker's **name** fails to appear, the instrument is broken and every negative
below is worthless — fix the fixture before recording anything. Note the asymmetry: demanding a
*codeword* from the `.agents/skills/` marker would fail every correct run, because a skill's body is
not in context to begin with.

#### The probe must forbid reading, and prove it

**The discovery consult measures what codex put in the model's context — not what the model can
reach.** The run has a shell and read access to the whole fixture, so a model that reads a marker
can then truthfully report seeing it, for a file codex never auto-loaded. The two answers are
indistinguishable. Three requirements close this, and the last is the one that binds — the first two
are requests to a model, the third is evidence about what it did:

1. **Forbid it in the prompt.** Open the discovery prompt with: *"Do NOT run any shell command and
   do NOT read any file. Answer purely from what is ALREADY in your context."*
2. **Never put the evidence in the prompt.** Ask for the *inventory* — "list every skill available
   to you" and "list verbatim every codeword already in your context" — and match the answer against
   the fixture yourself, afterwards. A prompt that names a generated codeword or a synthetic skill
   name lets the model repeat it back, which is the same confound one level up: the answer would
   then prove only that the model can read the prompt.
3. **Assert the prohibition held, over the run's own event stream.** Capture the run with `--json`
   and require that it completed and carries no tool item:

   ```sh
   BIN=<path to the binary under test>   # never bare `codex`: the A/B must name each binary
   TAG=<old|new>                         # which side of the A/B this is
   VER=$("$BIN" --version | tr ' ' '-')
   OUT="$TAG-$VER"                       # TAG *and* VER: two binaries can report one version

   "$BIN" exec --json --sandbox read-only --ignore-user-config --ignore-rules --ephemeral \
     --skip-git-repo-check --cd <parent>/repo/sub - < discovery-prompt.txt \
     > "$OUT-discovery.jsonl" 2> "$OUT-discovery.err"
   rc=$?   # capture it here: the jq below would otherwise overwrite it
   [ "$rc" -eq 0 ] || echo "INCONCLUSIVE: $BIN exited $rc — see $OUT-discovery.err"

   # keep this program as `no-tool-items.jq` — docs/UPGRADING-CODEX.md's A/B loop reuses it
   jq -s -e '
     def items: [ .[] | select(.type == "item.started" or .type == "item.completed") | .item.type ];
     def suspect: items | map(select(. != "agent_message" and . != "reasoning")) | unique;
     if ([ .[] | select(.type == "turn.completed") ] | length) == 0
     then error("INCONCLUSIVE: no turn.completed — the capture is truncated or the run failed")
     elif (suspect | length) > 0
     then error("NOT CLEAN: \(suspect | join(", "))")
     else "clean" end
   ' "$OUT-discovery.jsonl"
   ```

   **The run is clean only if both checks pass** — `rc` is 0 *and* the `jq` exits 0. A prompt-level
   prohibition is a request, not a guarantee; the stream is the evidence. Three properties of that
   `jq` are load-bearing, and each was verified against real captures before it was written down:

   - **It fails, rather than printing a clean-looking zero.** `jq -s -e` parses the whole file at
     once and exits non-zero, so malformed, empty, or truncated JSONL is an error — not the "no tool
     calls found" a `grep | wc -l` pipeline would report for the same broken capture. Require
     `turn.completed` for the same reason: a run killed mid-flight must not read as clean.
   - **It allowlists non-tool items and fails closed on anything else.** `agent_message` and
     `reasoning` are the model thinking and answering; every other item type — `command_execution`
     among them, which is what a shell-based file read produces — fails the run, *including a type
     this list has never seen*. Do not invert this into a denylist of known tool names: the next
     codex release would then add a tool the check waves through in silence.
   - **A failure is inconclusive, not a negative.** Discard the run, fix the cause, and re-probe.
     Never record a matrix row from a capture that failed this check.

**Why the older controls could not catch this.** The positive controls pass under both hypotheses —
the model sees the project `AGENTS.md` codeword whether codex loaded it or the model read it. A
removal-based negative control (delete the marker, watch the codeword vanish) behaves *identically*
under both, because with the file gone the read returns nothing either. It rules out confabulation,
which was never the failure mode. Tool-assisted reading is, and only the event stream discriminates.
This is not hypothetical: the 2026-08-02 run's transcript shows the parent codeword arriving as a
tool output after the model ran `sed` on `../AGENTS.md`, and the resulting "parent `AGENTS.md`
loaded? **Yes**" row stood as published fact until it was retracted.

Then run **three** consults with `--cd <parent>/repo/sub` — a consult is single-turn, so this cannot be
one call. Only the first is read-forbidden; the other two are tool-using by design, and each is
worded so that what comes back is still evidence:

- **Discovery** — under the three requirements above, ask it to list every skill available to it and
  every codeword already in its context, then to answer SEEN/NOT-SEEN for each of the seven markers
  **described by its role** — "the user-global skill's codeword", "the `AGENTS.md` above the git
  root", "the `AGENTS.md` one directory above the one you were pointed at", "the `AGENTS.md` in your
  Codex home" — never by the generated name or codeword itself. Asking role by role is what separates
  "not discovered" from "the model did not bother to mention it"; keeping the generated values out
  of the prompt is what keeps the answer evidence rather than an echo. **Rows 1-5 come from this
  run**, except row 1's egress half (below). A run that
  fails the assertion is discarded, not interpreted.
- **Body egress** — ask it to use the global marker skill by name and report the codeword in its
  body. This run is **deliberately tool-using, so the assertion above does not apply to it**: on
  0.147.0, 0.148.0, 0.149.1, 0.151.0, and 0.152.0 the model reads the `SKILL.md` itself once it selects the skill. It demonstrates
  *reachability* — a body outside the workspace reaching the model — and nothing else. In
  particular it **cannot** establish discovery, because naming the skill in the prompt is what let
  the model find the file: a read would have succeeded even if codex had never discovered that
  root. Take discovery only from the discovery consult's unprompted inventory.
- **Unprompted selection** — the run that matters for the egress claim, because the two above
  cannot make it. Add a *second* temporary global skill whose `description` matches an ordinary
  task, then ask for that task **without naming the skill, the file, or the codeword**: "I have a
  messy CSV with inconsistent delimiters and duplicate rows — what is the recommended procedure? If
  any house procedure applies, follow it." If the codeword comes back, a user-global skill body
  reached the model on a prompt that never referred to it. This run is tool-using by design; what
  makes it evidence is that the *prompt* contains nothing the model could have echoed. Remove this
  marker with the other one.

**Row 1 therefore rests on two independent observations, and needs both.** The synthetic global
skill's *name* appears in the discovery consult's inventory though nothing named it (auto-discovery,
under `--ignore-user-config`), and its *codeword* comes back from the body-egress consult after
selection (reachability). Neither observation implies the other: discovery could regress while a
prompted read still succeeds, which would leave a "Yes" standing on nothing.

**Drive the raw CLI, not this plugin's tools.** Only a raw invocation yields the `--json` stream the
assertion needs, and `docs/UPGRADING-CODEX.md` step 2A already requires the raw CLI so each binary
can be named explicitly.

**Keep every capture, and keep enough around it to identify the run.** The JSONL is the
*tool-activity* trace — it settles whether the model read anything, and nothing else. On its own it
cannot establish provenance, so retain alongside it: the exact prompt, the full command, the binary
path and `--version`, stderr, and the exit status. Give each capture a distinct name
(`<tag>-<version>-discovery.jsonl`, `<tag>-<version>-body-egress.jsonl`, one set per binary) — a
shared `run.jsonl` silently overwrites the other binary's evidence, and the A/B is the whole point.
**Name them by the tag as well as the version**: two binaries can report the same `--version` (two
build channels, a rebuild, a local build against the same tag), and comparing exactly those is a
reasonable thing to want. Keyed on version alone, the second run would quietly clobber the first and
the A/B would compare a capture against itself with no error to notice. With
these artifacts kept, `--ephemeral` costs nothing and stays on: the capture, not the session file,
is the record. Skipping this is what the retracted row cost — those runs were `--ephemeral` with no
capture, so their instrument could not be re-examined and the probe had to be rebuilt from scratch
to find the flaw.

Run both with the raw CLI flag set including **`--ignore-user-config`** — the flag the plugin's
`isolation=ignore-config` sends, though the plugin path is not interchangeable here, because it
yields no `--json` stream to assert over. The plugin's default `isolation=inherit` does **not** send
that flag at all, so a default-flags run cannot test the first row of the table — it would confirm
that the global skill is discovered and its body reachable, while never exercising the isolation
claim, and record a false positive on the exact point this section exists to hold. **Remove the temporary global skill afterwards.**

**Record a run as a presence matrix, not as prose.** For each binary, write down its executable path,
its `--version`, and one boolean per marker in the table above. Do **not** diff the raw answers —
they are model output, so wording and ordering vary between runs that observed the same thing. If
either positive control comes back false, record nothing from that run: fix the fixture and re-run.
And read a difference conservatively — the backend model is an uncontrolled variable, so a change is
*associated* with the binary, not proven caused by it. Reproduce it before concluding.

Observed under codex-cli 0.152.0 and A/B'd against 0.151.0 with an identical presence matrix
(observations, not guarantees — re-run the probe rather than assuming they still hold). Rows 1-3
were re-verified 2026-08-19 with the read-forbidding, assertion-backed probe above, run against
**both** binaries side by side; each discovery capture passed the assertion, and the two matrices
were identical — as they were in the 2026-08-18 0.147.0-vs-0.146.0 A/B, the 2026-08-25
0.149.1-vs-0.148.0 A/B, the 2026-08-29 0.151.0-vs-0.149.1 A/B, and the 2026-09-01
0.152.0-vs-0.151.0 A/B (#586). Every variant agreed on both binaries in the 2026-08-29 and
2026-09-01 runs: `project_doc_max_bytes=0`, the no-enclosing-repository fixture,
process-cwd-instead-of-`--cd`, and override precedence.
Row 1's egress half was
re-confirmed on 0.148.0, on 0.149.1, on 0.151.0, and on 0.152.0 by both the body-egress and the unprompted-selection consults; the
unprompted run returned the global skill's codeword on **both** binaries from a prompt naming
neither the skill nor the file. (The 0.149.1 body-egress run located and read the marker
`SKILL.md`; the paired 0.147.0 run gave up searching for it and returned no codeword. Read that as
model search behavior on a tool-using run — the uncontrolled variable this section warns about —
not as a boundary: 0.147.0's reachability is the 2026-08-18 observation, and its unprompted-selection
run returned the codeword here. The 2026-08-29 A/B produced the mirror image — 0.151.0 read the
marker on the first attempt while the paired 0.149.1 run gave up — and two immediate 0.149.1
retries both returned the codeword. That the same binary fails and then succeeds unchanged is
direct evidence for reading this as search behavior rather than a version difference.)

**A caution about the SEEN/NOT-SEEN answers, learned in the 2026-08-29 run.** Record the *codeword
inventory*, never the model's own role labels. In that run one binary answered "SEEN" for the
`AGENTS.md` above the git root and "NOT-SEEN" for the `$CODEX_HOME` one, while its own codeword
list — in the same reply — contained the `$CODEX_HOME` codeword and not the parent's. The
inventory is the evidence; the role labels are the model mapping prose onto a question, and they
were wrong on both binaries in at least one variant each. Diffing the labels would have manufactured
a matrix difference that the underlying observations flatly contradict.

| Question | Observed |
|---|---|
| `$CODEX_HOME/skills/` discovered despite `--ignore-user-config`? | **Yes** — its name was auto-discovered unprompted; after selection, its body reached the model through a model-issued read |
| Project `.claude/skills/` discovered? | **No** |
| Parent-directory `AGENTS.md` above the git root loaded? | **No** — the upward walk is git-root-bounded (see the correction below) |
| Ancestor `AGENTS.md` inside the repo, above the resolved workspace, loaded? | **Yes** (#472) |
| `$CODEX_HOME` guidance `AGENTS.md` loaded, despite `--ignore-user-config`? | **Yes** (#472); `AGENTS.override.md` loads in its place when present |
| `project_doc_max_bytes=0` disables loading? | **Partly** — it suppresses the workspace and ancestor files, **not** the `$CODEX_HOME` guidance file (#472). Not a containment mechanism, and the plugin does not expose it |

Row 1 is the compound one: read it as *discovered, then reachable*, per the two observations above.
The egress conclusion is unchanged — content from outside the workspace reaches OpenAI on a call
whose prompt never named it — but do not read the row as "the body is auto-loaded into context".

**Correction (2026-08-07), superseding the 2026-08-02 correction.** That earlier note recorded the
third row as **Yes** on both 0.145.0 and 0.146.0. It does not reproduce. Probing 0.146.0 and 0.147.0
side by side, the parent codeword was absent from **both**, in three variants: with a project
`AGENTS.md` present, with it removed (so no nearer file could mask the parent), and with `--cd` set
to a subdirectory of the repo. That last variant is the one that explains the mechanism rather than
just contradicting the record: from `repo/sub`, codex **did** load `repo/AGENTS.md` — so it walks
upward from the resolved directory, but the walk stops at the git root and does not cross it. Both
positive controls were green in every run — which, by the reasoning above, does not by itself
establish that the instrument was sound, since those runs could read. What settles it is the
2026-08-18 re-run under the read prohibition and the no-tool-item assertion, which reproduced both
halves on 0.147.0 with **no tool item** in either stream: from `repo` the parent codeword was absent,
and from `repo/sub` the `repo/AGENTS.md` codeword was present in context with no read to explain
it — so that one is auto-loading, not a tool-assisted read.

Read this as *the record was wrong*, not *0.147 changed*: new and old agree, and they disagree with
what was written down. What is retracted is the **mechanism** — "above the git root" — not the
concern. The corrected boundary is the git root, and the walk still starts at the **resolved
workspace directory**, which is not the same thing:

> **`resolve_workspace` returns an explicit `workspace_root` unchanged** (`pontonier.core.workspace`), so
> a caller may resolve the workspace to a *subdirectory* of a repository. The upward walk then
> crosses **above that directory** on its way to the git root. A caller who narrows
> `workspace_root` to `repo/sub` precisely in order to bound egress still ships `repo/AGENTS.md`,
> a file it never named and that lies outside the directory it selected.

So the published caveat — "the resolved workspace's `AGENTS.md`" — **does understate egress**, and
issue #472's conclusion stands even though the observation it cited does not. Correcting the
disclosure is a wording/meaning change to a `FINGERPRINT_COVERS` category and is tracked there, not
here; this section records only the corrected behavior. (An earlier draft of this note argued the
existing wording already covered the subdirectory case. It does not — "resolved workspace" is the
selected directory, not the enclosing repository — and the `--cd repo/sub` probe above is the
positive demonstration of exactly the egress path #472 describes.)

## Flag classes

- **ALWAYS_SEND_FLAGS** — guarantee-bearing (sandbox, cd, json, output-last-message, isolation,
  output-schema, …). Never gated on `--help`. If `codex` removes or renames one, it rejects the
  invocation at argument parsing — before any model call, zero spend — and the failure is reported
  as `cli_contract_changed` with repair guidance. The class means *never help-gated*, not *present
  on every argv*: several members ride only the invocations that need them (`--add-dir` and
  `--output-schema` when a caller supplies one, `--strict-config` when the run carries a `-c`
  override). What the class guarantees is that when such a flag is sent, its rejection is loud.
- **HELP_GATED_FLAGS** — depth/cosmetic only (e.g. `--model`). Feature-detected via
  `codex exec --help`; dropped gracefully if absent and noted in `meta.compat_warnings`.


## Caller developer instructions (`developer_instructions`, #556)

`codex_consult`, `codex_consult_async`, `codex_review_changes`, and `codex_review_changes_async`
accept an optional `developer_instructions` parameter — caller stance/focus text for Codex's
developer turn. `codex exec` has no flag for developer-turn text, so the channel is the
`developer_instructions` config key (`cli_contract.DEVELOPER_INSTRUCTIONS_CONFIG_KEY`), which
lands as the **first developer-role message**, ahead of Codex's own developer messages and
`AGENTS.md` (verified zero-spend with `codex debug prompt-input` on 0.152.0; the
`tests/test_integration.py` probes pin it against the installed CLI, with a negative control).
Deliberately *not* `model_instructions_file` — that key **replaces** the built-in instructions.

What bounds it, and what each bound is for:

- **One composed value.** The server's framing leads and cannot be displaced; the caller's text
  is delimited on both sides; the closing marker restates that the preceding rules outrank
  anything between the markers (`prompts.compose_developer_instructions`). Ordering is a property
  of the string, not of how codex merges repeated `-c` overrides. Text carrying a framing-marker
  line is refused pre-spend as `invalid_arguments` (the machine-readable reason names
  `forged_framing_marker` — it is a reason token, not an `error.code`) — case- and
  whitespace-insensitively: any fenced marker phrase, and any marker phrase at a line start
  with or without a fence — because delimiters are only meaningful while unforgeable; the match
  makes forgery harder, not impossible.
- **Normalized once.** Stripped at the boundary; blank means omitted. The bytes counted against
  the 4096-byte cap (bytes, not characters), the bytes hashed into `meta`, the bytes persisted in
  the job spec, and the bytes codex receives are the same string. NUL bytes and lone surrogates
  are refused pre-spend, as are other C0 control characters (except tab/LF/CR) and DEL —
  json.dumps does not escape U+007F, which TOML 1.0 forbids in a basic string; codex 0.151.0
  tolerates it, but a stricter upstream parser would turn it into a config-load failure. The
  text also counts against `CODEX_IN_CLAUDE_MAX_INPUT_BYTES` together
  with the call's other caller-authored inputs.
- **TOML-string-encoded** like the effort override (JSON string syntax with
  `ensure_ascii=False`), so newlines, quotes, and astral characters round-trip byte-exact and
  codex's TOML-value fallback never retypes the payload.
- **Emitted only when text was supplied.** The common run stays byte-identical to the pre-#556
  argv: no framing-only developer turn, and no `--strict-config` arming (which at `inherit`
  isolation would hard-fail on unknown keys anywhere in the user's own `config.toml` — the #524
  availability trade). A run that does carry the text also carries `--strict-config`, so an
  upstream rename of the key fails loudly (`cli_contract_changed`) instead of silently dropping
  the caller's instructions; the key is in `PLUGIN_OWNED_CONFIG_KEYS` for that attribution.
  The CLI `-c` outranks a `config.toml` `developer_instructions` (verified live; the
  integration probe pins it with a positive control), so on instruction-carrying runs the
  composed value wins. The `--profile` layer was NOT probed for this key — the workspace pins'
  flag-outranks-profile result (0.148.0) suggests the same, but that is inference, not
  verification. On runs without the parameter the operator-trust boundary stands unchanged.
- **Audited, never echoed.** `meta.developer_instructions` carries `{sha256, bytes}` of the
  normalized text — on sync results, the async job-start handle, and a fetched job result (the
  worker rebuilds it from the spec). Two plaintext carriers exist and are disclosed on the
  parameter itself: the composed value rides the codex **command line** (visible to local process
  listings for the run's duration), and the normalized text is persisted in the background-job
  `spec.json` — like `question` and `extra_context` — until the record is consumed or expires.
- **Two guarantees of different strengths.** Codex cannot gain a tool from the text (tools ride
  argv/config, not the prompt — mechanical); Codex is *instructed* not to let the text determine
  a verdict (behavioral). The tool description says "instructed", not "cannot". The verdict
  instruction is one case of the broader behavioral limit: compliance with the caller text as a
  whole is best-effort — the model may honor it in full, in part, or not at all, and no result
  field attests which happened. Non-compliance may be silent, though the framing does instruct
  Codex to say so when the text conflicts with the rules above it (#563).
- **The user-turn framing is unchanged.** Every per-tool rule still rides the user turn on stdin
  exactly as before; the developer turn is additive and binds only the caller text.
- **The raw key stays refused in the passthrough** (#555, above), so the operator channel and
  this parameter cannot disagree, and `codex_delegate`/`codex_delegate_async` do not accept the
  parameter (delegate edits files; a caller stance there would widen what an untrusted workspace
  can steer).

## Reasoning-effort control (`model_reasoning_effort`, #309)

`codex exec` 0.152.0 has no dedicated reasoning-effort flag (verified against
`codex exec --help`, 2026-09-01 — the 0.151.0 → 0.152.0 diff adds NO `codex exec` flag at all:
`codex exec --help` is byte-identical across those two releases, as it was across
0.149.1 → 0.151.0. Earlier, the 0.148.0 → 0.149.1
diff added one flag, `--thread-source`, which is thread metadata and not an effort control; and,
earlier still, only
the `codex exec fork` subcommand, which this plugin never invokes), so the per-call
`reasoning_effort` parameter and
`CODEX_IN_CLAUDE_REASONING_EFFORT` are sent as a **config override**:
`-c model_reasoning_effort="<value>"`, with the value **TOML-string-encoded** (JSON string syntax,
which is valid TOML). Codex TOML-parses the `-c` right-hand side and falls back to a string only
when that parse fails, so a raw interpolation would retype boolean/numeric/collection-shaped values
(codex 0.144.3 then rejects them locally as an invalid type) and silently unwrap quoted ones;
encoding makes the advertised open string round-trip exactly. A config key cannot be help-gated —
`--help` advertises flags,
not config keys — so a requested effort is sent unconditionally. Removal of the `-c` flag itself
fails loudly as `cli_contract_changed` with zero spend, and a rename or removal of the **key** now
does too: an effort-carrying run also sends `--strict-config` (see below), which rejects an unknown
key at startup instead of tolerating it as junk. What remains uncovered is a key that keeps its
name and changes **meaning** — the re-verification probe in `docs/UPGRADING-CODEX.md` is the guard
for that case. (Verified 2026-07-13: a CLI `-c` override survives `--ignore-user-config`, so an
explicit effort stays effective under every isolation mode.)

The **semantic value set** is open and not allowlisted by this plugin. The plugin still enforces
transport-shape bounds (length and argv/JSON safety); values passing those bounds are sent
unchanged. The CLI accepts such a string silently, and the **backend** rejects an unsupported
model/effort combination at request time with a 400 whose message carries
`[ReasoningEffortParam] [reasoning.effort] [invalid_enum_value] …` (probed against codex-cli
0.144.3, 2026-07-13). That message also matches the generic `invalid value` drift pattern, so the
classifier checks `REASONING_EFFORT_REJECTION_MARKERS` (`reasoning.effort`,
`reasoningeffortparam` — deliberately **not** the config key name) first: when this run sent a
first-class effort override and **every marker appears in its bracketed `[…]` field form**, the
failure is the caller's argument (`invalid_reasoning_effort`), not contract drift. A marker as a
free substring does not match — an operator passthrough naming one (`--enable reasoning.effort`, a
profile so named) stays attributable to `extra_args_rejected`. A passthrough descriptor that
itself carries the full bracketed signature (a profile literally named
`[reasoning.effort][ReasoningEffortParam]`) is attributed to the passthrough *before* the backend
check, so it cannot impersonate the backend rejection either. A rejection naming only
`model_reasoning_effort` (the key) still fails loudly as `cli_contract_changed`. The accepted set
genuinely varies by model and account — the backend advertised
`none|minimal|low|medium|high|xhigh` for gpt-5.5 on ChatGPT, while the models cache advertises
`max`/`ultra` for other slugs — so discovery stays advisory (below) and no enum is pinned.

**Discovery** reads the same undocumented `models_cache.json` as the slug catalog: each entry's
`default_reasoning_level` (a string) and `supported_reasoning_levels` (a list of
`{effort, description, …}` objects, of which only the `effort` tokens are surfaced) map to
`ModelInfo.default_reasoning_effort` / `supported_reasoning_efforts`, defensively validated
(`REASONING_EFFORT_TOKEN_PATTERN`, `SUPPORTED_EFFORTS_MAX_ENTRIES`) and advisory only. The bundled
static fallback carries no effort data.

## Strict config validation (`--strict-config`, #524)

By default `codex` **tolerates an unknown config key**: it reads it as junk and never applies it.
For an ordinary key that is harmless, but three of the keys this plugin sends are
*guarantee-bearing* pins — `sandbox_workspace_write.network_access`,
`sandbox_workspace_write.writable_roots`, and `model_reasoning_effort` — and for those, tolerance is
the failure mode. An upstream rename would leave the plugin sending a key codex no longer reads,
and the guarantee would reopen with no signal at all.

`--strict-config` turns that into a **zero-spend startup failure**: codex parses config before it
authenticates or calls a model, so an unknown key fails the run at once, for no spend. Verified live
on codex-cli 0.148.0 (2026-08-20), re-verified on 0.149.1 (2026-08-25), 0.151.0 (2026-08-29) and
0.152.0 (2026-09-01).

**Scope: runs that carry a `-c` override, not every run.** The flag is `ALWAYS_SEND`-class
(guarantee-bearing, never help-gated), but it is *emitted* only when the built argv actually carries
a config override — every `workspace-write` run (the two sandbox pins), every effort-carrying run,
and any run with an operator `-c` in `CODEX_IN_CLAUDE_EXTRA_ARGS`. The reason is blast radius: at
the default `inherit` isolation the flag also hard-fails on an unknown key **anywhere in the user's
own config**, including tables for profiles the run never selects. A plain read-only consult sends
no `-c` at all, so sending the flag there would risk the user's runs while guarding nothing. On a
codex build lacking the flag, argument parsing rejects it loudly and for zero spend — the safe
direction.

**Which config sources it validates** (all verified live on 0.148.0, re-verified on 0.149.1 and,
for the override / file / `--ignore-user-config` rows, on 0.151.0 and 0.152.0):

| Source | Validated under `--strict-config`? |
| --- | --- |
| A `-c`/`--config` argv override | Yes |
| `$CODEX_HOME/config.toml` | Yes — at `inherit` isolation |
| An **unselected** `[profiles.X]` table in that file | Yes |
| `$CODEX_HOME/NAME.config.toml`, with `--profile NAME` | Yes |
| The same file when the profile is **not** selected | No — codex never reads it |
| Any of the file sources under `--ignore-user-config` | No — `ignore-config`/`ignore-rules` isolation exempts every file, leaving the flag a pure self-check of argv keys |

Only key **names** are checked, not values: `-c model_reasoning_effort="totally_bogus"` still
parses and is judged by the backend, so the `invalid_reasoning_effort` path above is unaffected.

**Failure classification.** The rejection is recognized from the anchored stderr grammar, on stderr
alone, and *before* the auth and drift checks — codex echoes the offending key and file path, either
of which can contain a substring those matchers look for (`401` in a path, `invalid value` in a
quoted TOML key), and each would otherwise win on ordering. Ownership is then decided by the
rejected key, never by the shared `-c` descriptor that appears in codex's own message:

| Rejection | Code |
| --- | --- |
| An override naming one of the plugin's own pinned keys | `cli_contract_changed` — the drift signal this flag exists to produce |
| An override naming an operator `-c` key | `extra_args_rejected` |
| An override naming neither | `cli_contract_changed` — fail loud rather than guess |
| A file the operator selected with `--profile` | `extra_args_rejected` |
| Any other config file | `user_config_rejected` — the user's own config, naming the file and line to fix |

`user_config_rejected` is permanent (`temporary: false`) and repairs with `correct_config`. Its
repair leads with fixing or removing the offending key; `isolation="ignore-config"` is offered only
as a lossy fallback, since it drops the user's entire config — model provider, MCP servers, and all.
Note that neither `codex_status` nor a dry run parses the user's Codex config, so neither can
predict this failure: `ready: true` reports a found, authenticated binary, not a valid config.

### A RETIRED setting is a second, separate grammar (#542)

`--strict-config` recognizes an **unknown key**. A key that still exists whose **value** upstream
has retired produces a different message, which that parser cannot see and which matches no
`CONTRACT_DRIFT_STDERR_PATTERNS` entry either — so before #542 it reached the caller as a bare
`nonzero_exit` with the diagnosis thrown away. Captured verbatim from `codex-cli 0.149.1`
(2026-08-25, scratch `$CODEX_HOME`, zero spend); the same wording re-observed on `0.151.0`
(2026-08-29) and `0.152.0` (2026-09-01), and `parse_strict_config_rejection` still returns the parsed origin, key, path, and
line from it:

```text
Error: approval_policy = "untrusted" is no longer supported; remove this setting
```

Three properties make this worth its own recognizer rather than a broadened strict-config pattern:

- **It does not need `--strict-config`.** Codex refuses the retired value while parsing config, so
  it fires on *every* model-bearing run, including those that carry no `-c` pin at all.
- **It reaches the default isolation.** `inherit` does not send `--ignore-user-config`, so the
  user's own `config.toml` is read and this is the failure they meet on their first run after
  upgrading. `codex-cli 0.148.0` accepted the identical config, so it is an upgrade-time break
  rather than a standing one.
- **Ownership cannot be read off the message.** Unlike the strict grammar it names no file and
  carries no `-c` marker, so `parse_unsupported_config_setting` reports only the key and value, and
  `codex.py` attributes it: `extra_args_rejected` when the operator's own passthrough sets that key,
  otherwise `user_config_rejected`. For `approval_policy` specifically the operator branch can never
  fire — the extra-args parser already refuses that key as one that could weaken an advertised
  guarantee — so the rejection stays the user's, and a test pins that coupling.

### An INVALID value is a third grammar (#550)

A recognized key whose value fails serde's own validation — the wrong enum variant, or the wrong
TOML type — is a third message, two lines with the key on the second. Captured verbatim from
`codex-cli 0.149.1` (2026-08-25, scratch `$CODEX_HOME`, zero spend), re-observed unchanged on
`0.151.0` (2026-08-29) and `0.152.0` (2026-09-01) with both sub-grammars still parsing; these two sub-grammars, and
only these, are what `parse_invalid_config_value` encodes:

```text
Error loading config.toml: unknown variant `bogus`, expected one of `untrusted`, `on-failure`, `on-request`, `granular`, `never`
in `approval_policy`
```

```text
Error loading config.toml: invalid type: string "yes", expected a boolean
in `sandbox_workspace_write.network_access`
```

It shares the retired grammar's blast radius (no pin, no `--strict-config`, the default isolation)
and its ownership problem — the identical text is printed whether the value came from `config.toml`
or from a `-c` override (probed both ways), so the message carries no origin. Three further facts
were established live and shape the recognizer and its attribution:

- **The two lines are the entire stderr** (a blank line follows; nothing precedes). The pattern is
  anchored to the whole blob, not to lines: it runs ahead of the auth/drift/rate-limit substring
  matchers, and a config-shaped pair quoted ahead of a genuine diagnostic must not steal its
  classification.
- **A `-c` override outranks a bad file value entirely.** With `network_access = "yes"` in the file
  *and* the plugin's `-c sandbox_workspace_write.network_access=false` on the argv, the run does not
  fail at all. So when this grammar names a key the plugin pinned on **this run**, the refused value
  can only have been the plugin's own — `cli_contract_changed`. When the plugin did not send the key
  (the pins ride only `workspace-write` runs; the effort key only when an effort was requested) the
  same message is the user's file or the operator's passthrough. `classify_failure` takes the
  emitted key set (`plugin_config_keys_for`) for this reason; membership in
  `PLUGIN_OWNED_CONFIG_KEYS` alone is not proof the key was sent, and the retired-setting path
  above now uses the same rule.
- **A parent-table `-c t={k=v}` is echoed as the dotted child `t.k`**, so operator ownership
  (`owns_config_key`) covers the dotted descendants of a passthrough key, whole segments only.

The offending value is consumed by the grammar but never captured or echoed: it is free-form text
the user typed into the wrong key — plausibly a secret — and no pattern-based redactor recognizes
an arbitrary one. What codex *expected* is codex's own text and is surfaced instead.

## Operator extra-args passthrough (`CODEX_IN_CLAUDE_EXTRA_ARGS`, #231)

An opt-in operator knob adds extra **global** `codex` options to every paid `exec` invocation
(consult/review/delegate). It is a small allowlist — `-c`/`--config KEY=VALUE`, `-p`/`--profile NAME`,
`--enable`/`--disable FEATURE` — appended after the plugin's own help-gated tokens and before the
stdin sentinel, so it can select a `model_provider`/`--profile` (its motivating use is doing so under
`ignore-config` isolation, which sends `--ignore-user-config` and drops `config.toml`, leaving `-c`
the only lever) **without** displacing the envelope-bearing flags. Anything outside the allowlist is
refused at parse time with `extra_args_rejected`, before any spend.

This passthrough is **user-owned surface, not part of the CLI contract**: the option names/config
keys/profile names an operator supplies are their responsibility, so when `codex` rejects one the
expected signature is `extra_args_rejected` (operator config to fix) — **not** `cli_contract_changed`.
Drift is attributed to the passthrough only when `codex`'s rejection names one of the (sanitized)
descriptors this server injected; a rejection of a plugin-owned guarantee flag still fails loudly as
`cli_contract_changed`. Two boundaries the allowlist cannot fully police, and why:

- **`-c` values are free-form** and can override any dotted config path. Keys under `sandbox`,
  `approval_policy`, or `shell_environment_policy` are refused because they would weaken a guarantee
  this server advertises (the sandbox capability boundary, the delegate no-network-egress promise, the
  approval posture, or the host-env isolation of commands `codex` runs). The key is normalized the way
  codex's own `-c` parser trims it before this check, so a leading/segment space cannot slip a denied
  key past. A `-c` value may hold a secret, so it is never echoed in `codex_status` or an error
  envelope.
- **`model` and `model_reasoning_effort` are reserved for the first-class controls** (#310, #309).
  `meta.model` (and `raw_response.model`) report the model the per-call `model` parameter or
  `CODEX_IN_CLAUDE_MODEL` requested, and `meta.reasoning_effort` reports the effort the per-call
  `reasoning_effort` parameter or `CODEX_IN_CLAUDE_REASONING_EFFORT` sent; a passthrough
  `-c model=…` / `-c model_reasoning_effort=…` would make the run use the operator's value while
  the envelope reports the per-call/server value (null in the common case), so both exact keys are
  refused at parse time. The parser also conservatively refuses case- and quote-varied lookalikes
  (`Model`, `"model_reasoning_effort"`) that codex-rs 0.144.3 treats as distinct junk keys, not
  aliases. Set the env var or the per-call parameter instead — those flow into
  `resolved_defaults` and the meta fields correctly. This is not a `model_*` root reservation:
  `model_provider`/`model_providers.*` (this knob's motivating use case) and other `model_*`
  keys still pass through. An opaque `--profile` can still set either key — the operator-trust
  boundary below, restated, not closed.
- **The instruction-bearing keys are refused outright** (#555): `developer_instructions`,
  `model_instructions_file`, its deprecated alias `experimental_instructions_file`, the
  documented-as-reserved `instructions`, and `model_catalog_json` (a catalog entry carries
  `base_instructions` / `model_messages.instructions_template` for its slug, so an operator
  catalog redefines the selected model's built-in instructions). Every framing string this server sends (`prompts.py`,
  including the untrusted-data clause) rides the **user** turn on stdin; `-c developer_instructions`
  lands as the *first* developer-role message (verified with `codex debug prompt-input` on 0.152.0),
  and `model_instructions_file` replaces the built-in instructions (documented — the prompt-input
  renderer does not show base instructions, so that key is denied on its documented semantics).
  `meta` records only that a valid passthrough was configured, never which keys, so such a run is
  indistinguishable from a default one. These are exact normalized keys with the same lookalike
  over-denial as `model`; a nested `instructions` segment (`mcp_servers.<id>.instructions`) is a
  different key and still passes. Other config indirections were not exhaustively audited — a
  key that can shape instructions and is not listed here is the same boundary, not a promise.
  For `developer_instructions` the first-class replacement is the per-call, meta-reported
  parameter on consult/review (#556, below); the file- and catalog-shaped keys have no
  first-class control on purpose. An opaque `--profile`, and at the default `inherit` isolation
  the user's own `config.toml`, can still set them: the operator-trust boundary below, restated
  (with one carve-out: a run that *carries* the parameter outranks a `config.toml`
  `developer_instructions`, verified live — see the section below).
- **`remote_plugin` is wholly plugin-owned in the passthrough.** Both `--enable remote_plugin` and
  `--disable remote_plugin`, and any `-c features.remote_plugin=…` (either spelling, since
  `--enable X` == `-c features.X=true`), are refused — the server manages this feature as a documented
  security guarantee (#287, above). `--disable` is refused even though it agrees with the plugin, so a
  drift on the plugin's own guarantee flag can't be misattributed to the operator's passthrough. The
  refusal also covers the bare **`-c features=…`** parent key (a TOML inline table that could reach
  `remote_plugin`) and quoted key segments that resolve to the same path (`features."remote_plugin"`,
  `"features".remote_plugin`). Other features set by their own dotted key (`-c features.some_other=true`,
  `--disable some_other`) are still allowed.
- **`--profile` layers an opaque on-disk TOML** this server cannot inspect. A profile can therefore
  re-introduce configuration the denylist would otherwise refuse, so a profile is a documented
  **operator-trust boundary** — only enable this knob with profiles you control. One key is
  excepted: `sandbox_workspace_write.network_access` is pinned by a plugin-owned `-c` override
  that outranks profiles (verified 0.148.0, re-verified 0.149.1, 0.151.0 and 0.152.0; see Sandbox modes above), so a profile cannot
  re-grant network egress to a `workspace-write` run.

## Version policy

Advisory only. A version outside the tested set warns (`codex_status.version_warning`,
`StatusResult`) but never blocks — readiness depends only on the binary being found and
authenticated. Override the tested set with `CODEX_IN_CLAUDE_SUPPORTED_VERSIONS` (comma-separated
`major.minor`).

## Result extraction

The final answer is read from the `--output-last-message` file (stable). The `--json` JSONL event
stream is parsed **tolerantly** for optional metadata only (token usage, session id, error events),
so an event-schema change degrades metadata rather than breaking a run.

## Session transfer (`codex app-server`)

`codex_transfer` imports a Claude Code session transcript into a resumable Codex thread by driving
`codex app-server` — a newline-delimited JSON-RPC 2.0 stream over stdio (one JSON object per line, no
`Content-Length` framing). This whole surface is **experimental** upstream (`codex app-server` is
labeled `[experimental]` and the import method rides behind the `experimentalApi` capability), so
every assumption lives in `cli_contract.py` (the `APP_SERVER_*` / `IMPORT_*` constants) and
`appserver.py`. Verified against `codex-cli 0.152.0` via `codex app-server generate-json-schema --out <DIR>`.
The 0.151.0 → 0.152.0 schema diff added ONE v2 message this plugin does not consume
(`AuthRecoveryNotification`), removed none, and left six of the seven consumed schemas
byte-identical after canonicalization; `GetAccountRateLimitsResponse` gained two OPTIONAL additive
fields (`accountId`, `rateLimitUpsell`). Neither is read: `appserver.py` extracts the rate-limit
block key by key, so an added key is ignored, and the account identifier never reaches an envelope.
The earlier 0.149.1 → 0.151.0 schema diff added ten v2 messages this plugin does not consume,
removed none, and left **all seven** consumed schemas byte-identical after canonicalization.
The earlier 0.148.0 → 0.149.1 schema diff left six of the seven consumed schemas byte-identical after
canonicalization; `GetAccountRateLimitsResponse` gained two `PlanType` enum values (`edu_plus`,
`edu_pro`), which this plugin absorbs because it reads `planType` as a bounded free-form string
rather than against an allowlist (`RATE_LIMIT_PLAN_TYPE_MAX_BYTES`). Its inventory pass added three
unconsumed v2 notifications (`ProjectChangedNotification`, `StrictReviewRequiredNotification`,
`ThreadProjectUpdatedNotification`) and removed none.
The earlier 0.147.0 → 0.148.0 schema diff left every consumed schema byte-identical after
canonicalization; its inventory pass added three different unconsumed v2 messages
(`NullableGetAccountTokenUsageParams`, `ThreadQueueChangedNotification`,
`ThreadRevertedNotification`) and removed none.
The earlier 0.146.0 → 0.147.0 schema diff is additive only for the consumed surface (an optional
`extensions` map on `InitializeParams`, which this plugin does not send; an optional `title` on the
import progress/completed per-item results, which is read tolerantly and ignored; and two values
added to the `PlanType` enum — `self_serve_business_prolite` and `enterprise_cbp_automation` — read
as a free-form capped string rather than an enum), so nothing this plugin sends or reads changed.
The inventory pass added ten unconsumed v2 `ThreadSection*` messages and removed none.

The flow: `initialize` (with `capabilities.experimentalApi=true`) → `initialized` notification → one
`externalAgentConfig/import` request carrying a single `SESSIONS` migration item → wait for the
matching `externalAgentConfig/import/completed` notification → terminate the child. The client is
deliberately single-request (no broker, no session reuse).

**Thread-id discovery.** Two sources, in order:

1. **The completed notification** (`itemTypeResults[SESSIONS].successes[].target`) — the imported
   thread id, present on a **fresh** import. This is part of the app-server's *emitted* JSON schema
   (`generate-json-schema`), so it is the primary, versioned surface.
2. **The import ledger** `$CODEX_HOME/external_agent_session_imports.json` (undocumented — same drift
   class as `models_cache.json`) — read tolerantly and bounded, only as a fallback. Codex deduplicates
   a byte-identical transcript to a silent no-op (empty `successes` **and** `failures`), so a
   re-import's thread id is recoverable only here, matched on `source_path` + `content_sha256`.

Because a live Claude session transcript grows on every turn, re-transferring it is **not** idempotent
— the changed bytes are a fresh import with a new thread; the ledger fallback only fires for a
genuinely unchanged (typically closed) transcript. An old CLI without the import method returns
JSON-RPC `-32601` (method-not-found) → `transfer_unsupported` (the hard backstop behind the advisory
version gate). A completed import with no `target` and no ledger record → `transfer_incomplete`, naming
the ledger it checked.

Any other error on the import *request* is classified by its JSON-RPC code, because the two cases have
opposite owners. A code in the reserved `-32768..-32000` range (invalid params/request, parse/internal
error, plus the server-defined `-32000..-32099` band) — or an error malformed enough to carry no integer
`code` — means **our request** drifted from the CLI's schema, so it fails loudly as
`cli_contract_changed`. An application-range code is Codex rejecting **this transcript**, so it surfaces
as `transfer_failed` carrying the app-server's message. Broken stream or handshake (EOF, a non-JSON
line, an `initialize` error, a missing `codexHome`) remains `cli_contract_changed`.

## Failure classification

A non-success `codex exec` run is classified from its stderr/stdout and JSONL `error` events against
the signature sets in `cli_contract.py`, checked in order so a more specific cause is never masked by
a generic one:

1. **strict-config rejection** (`parse_strict_config_rejection`, on **stderr alone**) →
   `user_config_rejected`, `extra_args_rejected`, or `cli_contract_changed` by the ownership table in
   the strict-config section above. First because codex parses config before it authenticates or
   calls a model — so this is never an auth or rate-limit failure — while the key and path it echoes
   can contain substrings the matchers below would fire on.
2. **retired config setting** (`parse_unsupported_config_setting`, stderr alone, #542) and then
   **invalid config value** (`parse_invalid_config_value`, stderr alone, #550) → the same three
   codes, attributed by whether *this run* pinned the key, then operator passthrough, else the
   user's config (see the two subsections above). Same position, same reason: both consume a
   value the user wrote, which can carry any substring the matchers below fire on.
3. **auth** (`AUTH_FAILURE_PATTERNS`) → `codex_auth_required`.
4. **contract drift** (`CONTRACT_DRIFT_STDERR_PATTERNS`) → `cli_contract_changed`, **unless** the
   rejection names an operator `CODEX_IN_CLAUDE_EXTRA_ARGS` descriptor → `extra_args_rejected` instead
   (user-owned passthrough, not a plugin-contract drift; see the passthrough section above), **or**
   this run sent a first-class reasoning-effort override and the failure carries the backend's
   `REASONING_EFFORT_REJECTION_MARKERS` → `invalid_reasoning_effort` (a caller value to correct; see
   the reasoning-effort section above). Checked
   before rate-limit so a genuine contract change is never mistaken for a transient (retryable) failure.
5. **rate limit** (`RATE_LIMIT_PATTERNS`: `rate limit`, `too many requests`, `usage limit`, `quota`,
   `retry-after`, plus `429` matched with word boundaries so an incidental digit run can't fire it)
   → `codex_rate_limited`, `temporary=True` with `retry_after_ms` set from a parsed
   `Retry-After`/"retry after Ns" value **when it is seconds-valued** (a non-second unit or HTTP-date
   is ignored), else `RATE_LIMIT_DEFAULT_BACKOFF_MS` (60s). Lets a caller back off deterministically
   instead of retry-storming a transient limit.
6. everything else → `nonzero_exit`.

Signatures are confirmed against real `codex` output; this file is the source of truth for the
phrasings, so update `cli_contract.py` (one place) when upstream wording changes.

## Structured output

`--output-schema` uses OpenAI strict structured outputs: every property must appear in `required`
and every object must set `additionalProperties: false`. The findings schema in `schemas.py`
follows this (optional fields are nullable but still required).

## Canonical error envelope

Every `ok: false` response carries a uniform `error` object. The full schema is published at the
`codex://error-envelope` resource (fetch it once and cache by `fingerprint`); clients should
read that resource rather than hard-code the shape.

**Key contract points:**

- `temporary` (bool) signals whether retrying can succeed; `retry_after_ms` is always present
  (`null` unless `temporary` is true). Callers must read `temporary` — not `retry_after_ms`
  presence — as the retry signal.
- `repair{next_step,tool,arguments,alternative}` provides a stable SYMBOLIC `next_step` label
  (e.g. `poll_job_status`, `correct_arguments`) that callers branch on in code; `tool`/`arguments`
  name a recovery tool call; `alternative` is prose fallback. The `repair` field is omitted only
  when no corrective path exists.
- `details{field,fields,reason,allowed_values}` describes the offending input(s): `field` names a
  single input; `fields` (mutually exclusive with `field`; non-empty, unique) names inputs whose
  *combination* is invalid (e.g. a combined-size limit where no single input is at fault). The
  rejected `value` is deliberately never echoed — a parameter can accept arbitrary input that may be
  a secret. Neither carrier is required; whichever is present (`field`, `fields`, or neither) plus
  `reason`/`allowed_values` is sufficient to repair the call.
- Absent optional fields are **omitted** from the payload (no placeholder nulls), except
  `retry_after_ms` which is always present.

**Opaque wire branch:** tools that publish `outputSchema` include a compact opaque error branch
(a discriminated `ok: false` object) rather than the full error schema inline. Callers must branch
on `ok` first; the full envelope shape lives solely at `codex://error-envelope`. This keeps the
preloaded `tools/list` catalog compact.

**Stored job results across releases:** a persisted `result.json` is guaranteed readable only by
the release that wrote it. A backward-compatible *newer* release generally still reads it (added
fields are optional, and a compatible pre-upgrade payload is returned with `meta.fingerprint`
re-stamped to the current surface — pre-1.0, a breaking field removal/retype can end that).
**Cross-format replay is unsupported** (notably downgrade — the same code fires in the upgrade
direction after a breaking format change): each job record carries the writer's persisted-format
version (`RESULT_FORMAT`, stamped at spawn), and a stored result that fails validation under a
*different* recorded format is returned as `job_result_incompatible` — `temporary: false`, repair
`start_new_job` — because no retry can make this release able to read it (a reused
`idempotency_key` cannot succeed either; use a new one or none). A result that fails
validation under the *same*, a missing, or an unusable recorded format is corruption and stays
`internal_error` (message `"job result could not be returned: …"`).
(Records that have actually expired past their TTL still return `job_not_found`.)

## When `codex` changes

Follow the full procedure in [`docs/UPGRADING-CODEX.md`](docs/UPGRADING-CODEX.md): run the no-spend
drift check (`uv run python scripts/check_codex_contract.py`), do the manual semantic review the
script can't, then update `cli_contract.py` (and the lockstep files), run the test gate plus the live
integration tests, and bump `FINGERPRINT`/`CHANGELOG.md` only if the agent-visible surface changed.
