# Compatibility with the `codex` CLI

This plugin shells out to the OpenAI `codex` CLI. Every assumption it makes lives in
`src/codex_in_claude/cli_contract.py`, so an upstream change is centralized and greppable — though
incorporating one takes the lockstep procedure in
[`docs/UPGRADING-CODEX.md`](docs/UPGRADING-CODEX.md), not a single edit.
Design goal: **fail loudly and safely, never silently weaken a guarantee.**

Verified against `codex-cli 0.148.0`.

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
no paid call depends on either. The rate-limit read verifies against **codex-cli 0.148.0** (probe:
drive `codex app-server` and confirm `account/rateLimits/read` returns a quota block; an integration
test does this live). See "Session transfer" below for the import flow.

## Sandbox modes

`--sandbox` is the capability boundary for a run (`cli_contract.py`): `read-only` for the
consult/review tiers, `workspace-write` for the propose tiers (`codex_delegate`,
`codex_delegate_async`); we never pass `danger-full-access` or `--dangerously-bypass-*` by default.

**`workspace-write` permits filesystem writes inside the workspace but blocks network egress.** This
is codex's own sandbox boundary and we pass it through deliberately. The practical consequence: a
propose/apply task **cannot perform network operations** — `git push`/`fetch`, `gh ...`, `curl`,
`npm publish`, dependency installs, etc. all fail inside the sandbox (typically with a
`Could not resolve host` / DNS error). Delegated tasks must therefore be self-contained; do any
network step yourself after reviewing and applying the returned diff. The tool docstrings and the
`codex_capabilities` `negative_scope` state this so a calling agent doesn't assume write access
implies internet access.

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
  silent posture regression. Verify with a tool-surface probe on each Codex upgrade.

**Scope and boundary.** The guarantee covers model-bearing `codex exec` calls (consult/review/delegate);
it does not describe the separate `codex app-server` path used by `codex_transfer` (no model call). And
like the sandbox/approval `-c` denials below, it is bounded by the **`--profile` operator-trust
boundary** — an opaque profile this server cannot inspect could re-enable the feature, so only enable
that knob with profiles you control.

## Image reading (`view_image`, #479) — deliberately left enabled

Codex **0.147+** ships a `view_image` feature, stage `stable`, default **on** (unchanged at
`0.148.0`). This plugin neither sends nor refuses it, and that is a decision, not an oversight.
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

**Probe: no auto-attachment** (zero spend, `codex-cli 0.148.0`). In a scratch git repo containing
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

Two things that verification exposed, tracked separately because neither is about `view_image`'s
posture: `read-only` permits reads **anywhere the OS user can read**, while several agent-visible
descriptions still scope reads to the "resolved working dir" or "repo files" (#509); and a
successful `view_image` call emits **no item** in the `codex exec --json` stream — failures reach
only stderr — so image egress is invisible to anything parsing it, this plugin included (#510).

`recommended_plugins` is `stable`/default-**off** at `0.148.0` and is left unreserved on the same
reasoning as above — adjacency in the feature table is not evidence that it bypasses the
`remote_plugin` guarantee. [`docs/UPGRADING-CODEX.md`](docs/UPGRADING-CODEX.md) owns the
obligation to re-check both flags on each upgrade.

`recommended_plugins` is `stable`/default-**off** at `0.148.0` and is left unreserved on the same
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
**body** was observed arriving by a read the **model** itself issues (0.147.0, 0.148.0). Both are egress the caller never
asked for — a global skill's body came back from a prompt that named neither the skill nor the file,
the model having selected it on its auto-loaded description alone (probed 2026-08-18) — but only the
first is auto-loading. Verified
empirically against codex-cli 0.147.0 (2026-08-07, issues #300 and #358), re-verified 2026-08-18
under the read-forbidding probe below, and re-verified again 2026-08-19 against codex-cli 0.148.0 —
each A/B (against 0.146.0, then against 0.147.0) produced an identical presence matrix under that
same probe, so the user-global discovery is pre-existing rather than new.

**The `AGENTS.md` sources, probed 2026-08-19 (`codex-cli 0.148.0`, #472).** Every run below used
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
invisible. Two of these markers live in the real `$CODEX_HOME` — create them, probe, and **delete
them in the same run**; a forgotten `$CODEX_HOME/AGENTS.md` silently joins every later Codex call.
`AGENTS.override.md` masks `AGENTS.md` in that directory, so probe one at a time.

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
     --skip-git-repo-check --cd <parent>/repo - < discovery-prompt.txt \
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

Then run **three** consults with `--cd <parent>/repo` — a consult is single-turn, so this cannot be
one call. Only the first is read-forbidden; the other two are tool-using by design, and each is
worded so that what comes back is still evidence:

- **Discovery** — under the three requirements above, ask it to list every skill available to it and
  every codeword already in its context, then to answer SEEN/NOT-SEEN for each of the five markers
  **described by its role** — "the user-global skill's codeword", "the `AGENTS.md` above the git
  root" — never by the generated name or codeword itself. Asking role by role is what separates
  "not discovered" from "the model did not bother to mention it"; keeping the generated values out
  of the prompt is what keeps the answer evidence rather than an echo. **Rows 1-3 come from this
  run**, except row 1's egress half (below); row 4 is not testable by this probe at all. A run that
  fails the assertion is discarded, not interpreted.
- **Body egress** — ask it to use the global marker skill by name and report the codeword in its
  body. This run is **deliberately tool-using, so the assertion above does not apply to it**: on
  0.147.0 and 0.148.0 the model reads the `SKILL.md` itself once it selects the skill. It demonstrates
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

Observed under codex-cli 0.148.0 and A/B'd against 0.147.0 with an identical presence matrix
(observations, not guarantees — re-run the probe rather than assuming they still hold). Rows 1-3
were re-verified 2026-08-19 with the read-forbidding, assertion-backed probe above, run against
**both** binaries side by side; each discovery capture passed the assertion, and the two matrices
were identical — as they were in the 2026-08-18 0.147.0-vs-0.146.0 A/B. Row 1's egress half was
re-confirmed on 0.148.0 by both the body-egress and the unprompted-selection consults; the
unprompted run returned the global skill's codeword on **both** binaries from a prompt naming
neither the skill nor the file. (The 0.148.0 body-egress run located and read the marker
`SKILL.md`; the paired 0.147.0 run gave up searching for it and returned no codeword. Read that as
model search behavior on a tool-using run — the uncontrolled variable this section warns about —
not as a boundary: 0.147.0's reachability is the 2026-08-18 observation, and its unprompted-selection
run returned the codeword here.) Row 4 is not testable by this probe and stayed unverified:

| Question | Observed |
|---|---|
| `$CODEX_HOME/skills/` discovered despite `--ignore-user-config`? | **Yes** — its name was auto-discovered unprompted; after selection, its body reached the model through a model-issued read |
| Project `.claude/skills/` discovered? | **No** |
| Parent-directory `AGENTS.md` above the git root loaded? | **No** — the upward walk is git-root-bounded (see the correction below) |
| `project_doc_max_bytes=0` fully disables loading? | **Not verified — do not assume** |

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
  output-schema, …). Sent unconditionally and never gated on `--help`. If `codex` removes or
  renames one, it rejects the invocation at argument parsing — before any model call, zero spend —
  and the failure is reported as `cli_contract_changed` with repair guidance.
- **HELP_GATED_FLAGS** — depth/cosmetic only (e.g. `--model`). Feature-detected via
  `codex exec --help`; dropped gracefully if absent and noted in `meta.compat_warnings`.

## Reasoning-effort control (`model_reasoning_effort`, #309)

`codex exec` 0.148.0 has no dedicated reasoning-effort flag (verified against
`codex exec --help`, 2026-08-19 — the 0.147.0 → 0.148.0 diff adds no `codex exec` flag at all, only
the `codex exec fork` subcommand, which this plugin never invokes), so the per-call
`reasoning_effort` parameter and
`CODEX_IN_CLAUDE_REASONING_EFFORT` are sent as a **config override**:
`-c model_reasoning_effort="<value>"`, with the value **TOML-string-encoded** (JSON string syntax,
which is valid TOML). Codex TOML-parses the `-c` right-hand side and falls back to a string only
when that parse fails, so a raw interpolation would retype boolean/numeric/collection-shaped values
(codex 0.144.3 then rejects them locally as an invalid type) and silently unwrap quoted ones;
encoding makes the advertised open string round-trip exactly. A config key cannot be help-gated —
`--help` advertises flags,
not config keys — so a requested effort is sent unconditionally. Drift coverage is **narrower than
ALWAYS_SEND**: only removal of the `-c` flag itself fails loudly as `cli_contract_changed` with
zero spend. If a future `codex` renames or removes the **key**, the drift is **silent** — codex
tolerates unknown `-c` keys as junk it never reads (the same tolerance recorded for lookalike keys
below) — and the requested effort is quietly ignored; the re-verification probe in
`docs/UPGRADING-CODEX.md` is the guard for that case. (Verified 2026-07-13: a CLI `-c` override
survives `--ignore-user-config`, so an explicit effort stays effective under every isolation mode.)

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
  **operator-trust boundary** — only enable this knob with profiles you control.

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
`appserver.py`. Verified against `codex-cli 0.148.0` via `codex app-server generate-json-schema --out <DIR>`.
The 0.147.0 → 0.148.0 schema diff left every consumed schema byte-identical after canonicalization;
its inventory pass added three unconsumed v2 messages (`NullableGetAccountTokenUsageParams`,
`ThreadQueueChangedNotification`, `ThreadRevertedNotification`) and removed none.
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

1. **auth** (`AUTH_FAILURE_PATTERNS`) → `codex_auth_required`.
2. **contract drift** (`CONTRACT_DRIFT_STDERR_PATTERNS`) → `cli_contract_changed`, **unless** the
   rejection names an operator `CODEX_IN_CLAUDE_EXTRA_ARGS` descriptor → `extra_args_rejected` instead
   (user-owned passthrough, not a plugin-contract drift; see the passthrough section above), **or**
   this run sent a first-class reasoning-effort override and the failure carries the backend's
   `REASONING_EFFORT_REJECTION_MARKERS` → `invalid_reasoning_effort` (a caller value to correct; see
   the reasoning-effort section above). Checked
   before rate-limit so a genuine contract change is never mistaken for a transient (retryable) failure.
3. **rate limit** (`RATE_LIMIT_PATTERNS`: `rate limit`, `too many requests`, `usage limit`, `quota`,
   `retry-after`, plus `429` matched with word boundaries so an incidental digit run can't fire it)
   → `codex_rate_limited`, `temporary=True` with `retry_after_ms` set from a parsed
   `Retry-After`/"retry after Ns" value **when it is seconds-valued** (a non-second unit or HTTP-date
   is ignored), else `RATE_LIMIT_DEFAULT_BACKOFF_MS` (60s). Lets a caller back off deterministically
   instead of retry-storming a transient limit.
4. everything else → `nonzero_exit`.

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
