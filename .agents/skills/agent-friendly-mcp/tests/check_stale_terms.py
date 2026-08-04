#!/usr/bin/env python3
"""Fail if agent-consumed skill files still teach retired MCP 2025-11-25 protocol.

Scope: SKILL.md, references/*.md, and tests/scenarios.md — the surfaces an
agent reads as current guidance.  Deliberately excluded: references/
mcp-2025-11-25-compat.md (its purpose is cataloging the old revision),
decisions/ (historical records), and tests/runs/ (recorded evidence, which
states the contract in force at its own date — see the Results policy in
tests/scenarios.md).

A hit is a retired term on a line that does not signal historical framing
(markers like "no longer", "removed", "2025-11-25").  The marker filter keeps
legitimate "X no longer exists" teaching lines out of the report while still
catching prose that states the old contract as current.

Self-test: before scanning, the checker must flag a known-bad sample line —
a scanner that can no longer detect a stale term fails the build itself.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent

STALE_PATTERNS: dict[str, re.Pattern[str]] = {
    "tasks/result": re.compile(r"tasks/result"),
    "tasks/list": re.compile(r"tasks/list"),
    "execution.taskSupport": re.compile(r"taskSupport"),
    "pollInterval (old spelling)": re.compile(r"pollInterval(?!Ms)"),
    "ttl (old spelling)": re.compile(r"`ttl`|\"ttl\"|\bttl\b(?!Ms)"),
    "resources/subscribe": re.compile(r"resources/(un)?subscribe\b"),
    "Mcp-Session-Id": re.compile(r"Mcp-Session", re.IGNORECASE),
    "related-task _meta key": re.compile(r"related-task"),
    "model-immediate-response _meta key": re.compile(r"model-immediate-response"),
    "-32042 URLElicitationRequired": re.compile(r"-32042|URLElicitationRequired"),
    "-32002 resource-not-found": re.compile(r"-32002"),
    "elicitationId": re.compile(r"\belicitationId\b"),
    "notifications/elicitation/complete": re.compile(r"notifications/elicitation/complete"),
    "notifications/roots/list_changed": re.compile(r"notifications/roots/list_changed"),
    "notifications/tasks/status": re.compile(r"notifications/tasks/status"),
    "server.capabilities.* path": re.compile(r"server\.capabilities\."),
    "client.capabilities.* path": re.compile(r"client\.capabilities\."),
    "initialize handshake": re.compile(
        r"`initialize`|initialize/initialized|during initialization"
    ),
}

# Historical framing in the same clause as a match means the text is talking
# *about* the old revision, not teaching it.  Clause-scoped (not whole-line or
# character-window) so a mixed sentence — historical clause plus a stale term
# taught as current in the next clause — still fails.
HISTORICAL_MARKERS = (
    "no longer",
    "removed",
    "retired",
    "2025-11-25",
    "replaces",
    "instead of",
    "there is no",
    "do not exist",
    "does not exist",
    "pre-2026",
    "deprecated",
)

# Clause boundaries: sentence enders and semicolons (only before whitespace or
# end-of-line, so dots inside identifiers like `execution.taskSupport` do not
# split), em-dashes, and commas that start a new coordinate clause.
CLAUSE_SPLIT = re.compile(r"[.;](?=\s|$)|—|,\s+(?:but|so|and now|instead)\b")

# A dated results-table row explicitly labeled with the old contract is a
# historical record in its entirety (tests/scenarios.md Results section).
HISTORICAL_ROW = re.compile(r"^\|\s*\d{4}-\d{2}-\d{2}\s*\|.*2025-11-25 contract")

EXCLUDED = {"mcp-2025-11-25-compat.md"}


def scan_files() -> list[Path]:
    files = [SKILL_DIR / "SKILL.md", SKILL_DIR / "tests" / "scenarios.md"]
    files += sorted((SKILL_DIR / "references").glob("*.md"))
    return [f for f in files if f.name not in EXCLUDED and f.exists()]


def _clause_spans(line: str) -> list[tuple[int, int]]:
    spans = []
    start = 0
    for boundary in CLAUSE_SPLIT.finditer(line):
        spans.append((start, boundary.start()))
        start = boundary.end()
    spans.append((start, len(line)))
    return spans


def scan_line(line: str) -> list[str]:
    if HISTORICAL_ROW.match(line):
        return []
    clauses = _clause_spans(line)
    hits = []
    for name, pattern in STALE_PATTERNS.items():
        for match in pattern.finditer(line):
            start, end = next(
                ((s, e) for s, e in clauses if s <= match.start() < e), (0, len(line))
            )
            clause = line[start:end].lower()
            if not any(marker in clause for marker in HISTORICAL_MARKERS):
                hits.append(name)
                break
    return hits


def self_test() -> None:
    """A scanner that cannot flag a known-stale line is a broken instrument."""
    cases = [
        # (line, should_flag)
        ("Poll `tasks/get` and fetch the result with `tasks/result` before `ttl` expires.", True),
        ("`tasks/result` and `tasks/list` no longer exist — poll `tasks/get`.", False),
        # Mixed clauses: a historical clause must not excuse a stale term taught
        # as current in an adjacent clause, however close.
        ("Roots is deprecated; fetch long-running output with `tasks/result`.", True),
        ("`tasks/result` no longer exists, but use `tasks/list` to recover task IDs.", True),
        (
            "Roots is deprecated and its notification was removed from the revised protocol"
            " surface entirely; meanwhile, to recover long-running output you should fetch it"
            " with `tasks/result`.",
            True,
        ),
    ]
    for line, should_flag in cases:
        if bool(scan_line(line)) is not should_flag:
            print(
                f"check_stale_terms: SELF-TEST FAILED on: {line[:80]!r}",
                file=sys.stderr,
            )
            raise SystemExit(2)


def main() -> int:
    self_test()
    hits: list[str] = []
    for path in scan_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name in scan_line(line):
                hits.append(f"{path.relative_to(SKILL_DIR)}:{lineno}: {name}: {line.strip()[:120]}")
    if hits:
        print(f"{len(hits)} stale 2025-11-25 term(s) taught as current:", file=sys.stderr)
        for hit in hits:
            print(f"  {hit}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
