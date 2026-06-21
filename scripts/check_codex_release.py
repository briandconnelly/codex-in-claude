#!/usr/bin/env python
"""Detect a new upstream `codex` minor and emit a notify/skip decision.

The companion to ``scripts/check_codex_contract.py``. That script does the
*mechanical drift* half of ``docs/UPGRADING-CODEX.md`` and needs the real CLI
installed; this one does the cheap, no-CLI *watch* half: given the latest
published `codex` version (the GitHub Action fetches it from the npm registry),
it decides whether that version's ``(major, minor)`` is already tracked in
``cli_contract.SUPPORTED_VERSIONS``. If not, the workflow opens a tracking issue
pointing at the upgrade procedure.

Decision is intentionally **minor-level**, matching the structured contract:
``SUPPORTED_VERSIONS`` stores ``(major, minor)`` only. A patch bump within a
tracked minor is the doc's softer "may refresh" — not a new-minor trigger — so it
is reported as ``new=false`` to keep the notifier high-signal.

Usage:
    uv run python scripts/check_codex_release.py --latest 0.142.0
    uv run python scripts/check_codex_release.py --latest 0.142.0 --tracked 0.141

Exit codes:
    0  decision emitted (see the ``new`` output: true/false)
    2  could not parse the supplied --latest version (nothing decided)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from codex_in_claude import cli_contract

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str) -> tuple[int, int, int]:
    """Extract ``(major, minor, patch)`` from a version string.

    Tolerates a leading ``v`` or a ``codex-cli `` prefix. Raises ``ValueError``
    if no ``X.Y.Z`` triple is present.
    """
    match = _VERSION_RE.search(text.strip())
    if match is None:
        raise ValueError(f"no X.Y.Z version found in {text!r}")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _tracked_minors() -> set[tuple[int, int]]:
    """The contract's source-of-truth set of supported ``(major, minor)`` pairs."""
    return set(cli_contract.SUPPORTED_VERSIONS)


def evaluate(latest: str, tracked: set[tuple[int, int]]) -> dict[str, object]:
    """Compare ``latest`` against ``tracked`` minors; return the decision record."""
    major, minor, _patch = parse_version(latest)
    latest_minor = (major, minor)
    highest_tracked = max(tracked)
    is_new = latest_minor not in tracked and latest_minor > highest_tracked
    return {
        "new": is_new,
        "latest_minor": f"{major}.{minor}",
        "tracked": f"{highest_tracked[0]}.{highest_tracked[1]}",
    }


def _emit(result: dict[str, object]) -> None:
    """Write GitHub Actions outputs (if running in one) and a human summary."""
    lines = [
        f"new={'true' if result['new'] else 'false'}",
        f"latest_minor={result['latest_minor']}",
        f"tracked={result['tracked']}",
    ]
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    if result["new"]:
        print(
            f"New codex minor available: {result['latest_minor']} "
            f"(tracked: {result['tracked']}). See docs/UPGRADING-CODEX.md."
        )
    else:
        latest_minor = result["latest_minor"]
        tracked = result["tracked"]
        print(f"No new codex minor: latest {latest_minor} <= tracked {tracked}.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect a new upstream codex minor.")
    parser.add_argument("--latest", required=True, help="Latest published codex version (X.Y.Z).")
    parser.add_argument(
        "--tracked",
        default=None,
        help="Override tracked minor as M.N (default: cli_contract.SUPPORTED_VERSIONS).",
    )
    args = parser.parse_args(argv)

    if args.tracked is not None:
        try:
            major, minor, *_ = (*parse_version(args.tracked + ".0"),)
        except ValueError:
            print(f"FAIL: could not parse --tracked {args.tracked!r}.", file=sys.stderr)
            return 2
        tracked = {(major, minor)}
    else:
        tracked = _tracked_minors()

    try:
        result = evaluate(args.latest, tracked)
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    _emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
