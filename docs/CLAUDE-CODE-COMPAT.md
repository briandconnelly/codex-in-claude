# Compatibility with Claude Code (the host)

This plugin runs *inside* Claude Code. That makes the coupling here the mirror image of the one in
[`COMPATIBILITY.md`](../COMPATIBILITY.md): there we **call** the `codex` CLI and pin a tight flag
contract that fails loud when a flag is rejected; here Claude Code **hosts** us, so there is nothing
to pin and no rejected-flag signal. A Claude Code release can change a manifest schema, the MCP
protocol it negotiates, or a skill/command format, and we'd typically learn about it from a user
reporting that the plugin stopped working.

This is the **reactive runbook** for that case: what we couple to, how a break shows up, how to find
the drifted surface, and how to respond. It is not an upgrade procedure (we can't drive Claude Code's
version) — for the codex CLI we *do* call, see [`docs/UPGRADING-CODEX.md`](UPGRADING-CODEX.md).

## What we couple to — and what already guards it

Four surfaces, each with the file that owns it and the test that already catches the *shape* drifting.
All packaging tests live in `tests/test_packaging.py` and run in the default suite.

| Surface | We own | Guarded by |
|---------|--------|------------|
| **Plugin / marketplace manifest** — the keys Claude Code reads to load us (`skills`, `commands`, `mcpServers`, `interface`) | `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` | `test_plugin_manifest_valid_and_versioned`, `test_marketplace_valid` |
| **MCP server launch** — how Claude Code starts the server | `.mcp.json` (the `uvx --from codex-in-claude==X.Y.Z` pin) | `test_mcp_json_launches_pinned_release` |
| **Skill + command formats** — frontmatter and directory layout Claude Code discovers | `skills/`, `commands/codex/` | `test_skill_present_with_frontmatter`, `test_commands_present`, `test_delegate_async_command_present` |
| **MCP protocol** — the tool/resource/prompt contract spoken over stdio, via `fastmcp` | `src/codex_in_claude/server.py` + `schemas.py`; `fastmcp>=3.4` in `pyproject.toml` | `test_capabilities_match_registered_tools`, `test_tool_error_codes_cover_every_tool_and_are_valid`, the golden schema tests |

The packaging tests assert our files are *internally* valid and self-consistent. They cannot assert
they still match Claude Code's *current* expectations — Claude Code publishes no versioned manifest or
plugin schema to pin against. That gap is exactly what this runbook covers by hand.

## What we deliberately do NOT depend on

- **No host-version pin.** `plugin.json` has no min-Claude-Code-version field, and we don't gate on
  one. Plugins auto-update for users; pinning would create more breakage than it prevents.
- **No Claude Code internals.** We integrate only through the published extension points (manifest
  keys, the MCP protocol, skill/command files), never through undocumented CLI internals or paths.
- **A negotiated protocol, not a frozen one.** `fastmcp` negotiates the MCP protocol version with the
  client at `initialize`. We depend on the stable, documented MCP surface (tools, resources, prompts,
  `listChanged`, strict-mode JSON Schema), not on a specific wire version — so a protocol bump
  degrades or no-ops a capability rather than hard-breaking a run.

## Breakage signals → triage → response

When a Claude Code change breaks us, it surfaces as one of these. Match the symptom, inspect the
named surface, run the named suite, ship a fix.

### 1. Plugin won't install or load
**Signal:** the plugin doesn't appear, or Claude Code reports a manifest/marketplace error on
install. Skills, commands, and the MCP server are all absent at once.
**Surface:** plugin / marketplace manifest.
**Triage:** re-read `.claude-plugin/plugin.json` and `marketplace.json` against the current Claude
Code plugin docs — look for a renamed/removed key, a newly-required field, or a changed `interface`
shape. `git log` the manifest to see what we last set.
**Respond:** update the manifest, re-run `uv run pytest tests/test_packaging.py`, ship a patch
release ([`docs/RELEASING.md`](RELEASING.md)). Manifest-only fixes don't touch `FINGERPRINT`.

### 2. MCP server won't register / tools missing
**Signal:** `codex_status` and the other tools fail with a transport error — `Connection closed`, or
`No such tool available: mcp__codex-in-claude__*` — even though the plugin loaded.
**Surface:** MCP server launch (`.mcp.json`) or the MCP protocol (`fastmcp`).
**Triage:** confirm `uvx --from codex-in-claude==X.Y.Z codex-in-claude-mcp` still starts the server
from a shell (this is the exact command Claude Code runs). If it starts standalone but Claude Code
can't reach it, suspect a protocol/transport change in the host; check the `fastmcp` release notes and
try a `fastmcp` bump in `pyproject.toml`. The collaborating-with-codex skill's "If the MCP server is
unavailable" section is the user-facing recovery for the transient case.
**Respond:** fix `.mcp.json` or bump/adjust `fastmcp`; run the full suite. If a protocol change alters
the agent-visible surface, treat it as breaking — bump `FINGERPRINT` and note it in `CHANGELOG.md`.

### 3. Skills or slash commands silently absent
**Signal:** the plugin and MCP tools work, but `/codex:*` commands or the collaborating-with-codex
skill don't show up or don't trigger. No error — they're just not discovered.
**Surface:** skill / command formats.
**Triage:** compare `skills/collaborating-with-codex/SKILL.md` frontmatter and the `commands/codex/`
files against the current Claude Code skill/command format docs — a changed frontmatter key or
directory convention is the usual cause.
**Respond:** update the files, re-run the packaging tests (they assert presence + frontmatter), ship a
patch. This is doc/format-only; no `FINGERPRINT` change.

### 4. Client rejects otherwise-valid tool calls
**Signal:** tools are registered but calls fail validation at the client before reaching our code, or
schemas render wrong in the UI.
**Surface:** MCP protocol — our tool input schemas vs. what the client now enforces.
**Triage:** Claude Code may have tightened JSON Schema handling (this repo already declares a schema
dialect and strict-mode shapes — see the schema commits). Re-validate `schemas.py` against the
current MCP/JSON-Schema expectations.
**Respond:** adjust the schemas, bump `FINGERPRINT` (the agent-visible surface changed), and follow
the breaking-change release rules in `AGENTS.md`.

## Staying ahead of it

This is intentionally reactive — Claude Code ships no pinnable version contract, so there's no
drift-check script to mirror `scripts/check_codex_contract.py`. Two cheap habits help:

- **Watch the extension points, not the version number.** When Claude Code announces plugin-manifest,
  skill/command-format, or MCP-protocol changes, re-read the three or four files above. The version
  number itself tells us nothing.
- **If Claude Code ever publishes a versioned plugin/manifest schema,** add a snapshot/validation test
  to `tests/test_packaging.py` that checks our manifest against it — that would convert surface #1
  from reactive to proactive. (Not built today; tracked here as the obvious upgrade.)
