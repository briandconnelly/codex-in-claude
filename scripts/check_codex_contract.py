#!/usr/bin/env python
"""No-spend drift check between the installed `codex` CLI and our cli_contract.

Runs only the two FREE local probes the plugin already uses — `codex --version`
and `codex exec --help` — and diffs what they report against the source-of-truth
sets in ``codex_in_claude.cli_contract``. No model call, no token spend.

It reuses the SAME parser the running server uses (``preflight._parse_supported``),
so a flag this script can't find is a flag the server's feature-detection can't
find either. It is the mechanical half of ``docs/UPGRADING-CODEX.md``; the
judgment half (do flag *semantics* still hold? did an stderr phrasing change?)
cannot be automated and stays in that checklist.

Usage:
    uv run python scripts/check_codex_contract.py

Exit codes:
    0  contract holds (warnings are non-fatal)
    1  drift: a guarantee-bearing flag or sandbox value is gone (a real blocker)
    2  could not probe (codex missing / timed out / help unparseable) — verify nothing
"""

from __future__ import annotations

import sys

from codex_in_claude import cli_contract, config, preflight
from codex_in_claude._core import runtime

OK = "OK  "
WARN = "WARN"
FAIL = "FAIL"


def _probe_version() -> str:
    run = runtime.run_sync_capture(
        [cli_contract.CODEX_BIN, *cli_contract.VERSION_ARGS], timeout_seconds=10
    )
    if run.binary_missing or run.timed_out:
        return ""
    return run.stdout.strip()


def _probe_help() -> str:
    run = runtime.run_sync_capture(
        [cli_contract.CODEX_BIN, *cli_contract.EXEC_HELP_ARGS], timeout_seconds=10
    )
    # A timeout writes "__timed_out__" to stderr; without this guard that non-empty
    # text would parse to zero flags and misreport a hung probe as contract drift.
    if run.binary_missing or run.timed_out:
        return ""
    return f"{run.stdout}\n{run.stderr}"


def main() -> int:
    version_str = _probe_version()
    help_text = _probe_help()

    if not version_str or not help_text.strip():
        print(f"{FAIL}: could not probe `codex` (binary missing, timed out, or help unparseable).")
        print("      Install/authenticate codex, then re-run. Nothing was verified.")
        return 2

    # Parse with the very same regex the server's feature-detection uses.
    flags = preflight._parse_supported(help_text)
    blocking = False

    # --- version ---------------------------------------------------------------
    parsed = config.parse_version(version_str)
    if parsed is None:
        print(f"{WARN}: could not parse a (major, minor) from {version_str!r}.")
    else:
        tracked = parsed in cli_contract.SUPPORTED_VERSIONS
        tag = OK if tracked else WARN
        tracked_str = ", ".join(f"{m}.{n}" for m, n in sorted(cli_contract.SUPPORTED_VERSIONS))
        hint = "" if tracked else "  (untracked — bump SUPPORTED_VERSIONS once verified)"
        print(
            f"{tag}: {version_str} -> minor {parsed[0]}.{parsed[1]}; "
            f"SUPPORTED_VERSIONS = {{{tracked_str}}}{hint}"
        )

    # --- guarantee-bearing flags (a miss is a real blocker) --------------------
    missing_always = sorted(f for f in cli_contract.ALWAYS_SEND_FLAGS if f not in flags)
    if missing_always:
        blocking = True
        print(f"{FAIL}: ALWAYS_SEND flags absent from `codex exec --help`: {missing_always}")
        print("      These are sent unconditionally — a removal/rename weakens a guarantee.")
    else:
        print(f"{OK}: all {len(cli_contract.ALWAYS_SEND_FLAGS)} ALWAYS_SEND flags present.")

    # --- depth/cosmetic flags (a miss is gracefully dropped, only warn) --------
    for flag in sorted(cli_contract.HELP_GATED_FLAGS):
        if flag in flags:
            print(f"{OK}: HELP_GATED flag {flag} present.")
        else:
            print(f"{WARN}: HELP_GATED flag {flag} absent — server drops it gracefully.")

    # --- sandbox values (capability boundary) ----------------------------------
    # Deliberately coarse: a substring scan of the whole help blob, not a parse of
    # the `--sandbox` value enum. It can false-pass if a value appears in unrelated
    # prose, or false-fail on a help-format change — acceptable for this mechanical
    # pre-check, which the manual semantic review and the live integration tests
    # (docs/UPGRADING-CODEX.md) back up.
    missing_sandbox = [v for v in cli_contract.VALID_SANDBOXES if v not in help_text]
    if missing_sandbox:
        blocking = True
        print(f"{FAIL}: sandbox value(s) absent from help text: {missing_sandbox}")
    else:
        print(f"{OK}: sandbox values present: {list(cli_contract.VALID_SANDBOXES)}")

    # --- informational: flags codex offers that the contract doesn't consume ---
    known = cli_contract.ALWAYS_SEND_FLAGS | set(cli_contract.HELP_GATED_FLAGS)
    unconsumed = sorted(flags - known)
    if unconsumed:
        print(
            f"\nINFO: {len(unconsumed)} flag(s) in `codex exec --help` are not consumed by the "
            "contract.\n      Skim for anything newly relevant "
            "(see docs/UPGRADING-CODEX.md step 2):"
        )
        print("      " + ", ".join(unconsumed))

    print()
    if blocking:
        print(f"{FAIL}: contract drift detected — update cli_contract.py before shipping.")
        return 1
    print(f"{OK}: contract holds against {version_str}. Semantics still need manual checks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
