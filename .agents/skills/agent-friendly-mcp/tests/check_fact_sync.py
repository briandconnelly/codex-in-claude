#!/usr/bin/env python3
"""Fail if a shared fact drifts at one of its known restatement sites.

Two review-demonstrated drift modes motivate this checker (findings F1-F3
of the 2026-08-15 three-model skill review; not archived in-repo): a
summary sentence in SKILL.md's Spec Baseline silently dropping part of a
spec fact, and an examples.md enumeration silently dropping a native
field.  Each probe names one fact, an anchor regex that finds its
restatement lines, and a co-term every such line must carry.

Semantics: within the probe's scope, at least one line must match `anchor`
(deletion protection), and every anchor-matching line must also match
`requires` (rewording protection).

Limitation: probes assert token co-occurrence, not meaning — a rewrite that
keeps every token on the line but inverts the statement passes this gate.
That class of change is a deliberate edit for human/model review to catch;
this instrument exists to catch silent omission.

Self-test: before scanning, every probe must flag its recorded `bad_sample`
(the pre-fix text) — a probe that can no longer detect its own known-bad
line fails the build itself.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Probe:
    name: str
    path: str
    section: str | None  # H2 heading text; None scans the whole file
    anchor: re.Pattern[str]
    requires: re.Pattern[str]
    bad_sample: str


PROBES: list[Probe] = [
    Probe(
        name="spec-baseline-cache-hint-carriers",
        path="SKILL.md",
        section="Spec Baseline",
        anchor=re.compile(r"`ttlMs`"),
        requires=re.compile(r"`server/discover`"),
        bad_sample=(
            "Every result carries a required `resultType`, and list/read results carry "
            "the native cache hints `ttlMs` and `cacheScope`."
        ),
    ),
    # Conservative by design: if the notification sentence is ever split across
    # lines (listen-stream line separate from the request-scoped carve-out), this
    # fires a false positive — update the probe rather than deleting it.
    Probe(
        name="spec-baseline-notification-split",
        path="SKILL.md",
        section="Spec Baseline",
        anchor=re.compile(r"`subscriptions/listen`"),
        requires=re.compile(r"`notifications/progress`"),
        bad_sample="and push notifications ride an opt-in `subscriptions/listen` stream.",
    ),
    Probe(
        name="prompt-native-inventory-icons",
        path="references/examples.md",
        section=None,
        anchor=re.compile(r"native is `name`"),
        requires=re.compile(r"`icons`"),
        bad_sample="(native is `name`, `title`, `description`, `arguments`, `_meta`)",
    ),
    Probe(
        name="tool-native-inventory-icons",
        path="references/examples.md",
        section=None,
        anchor=re.compile(r"full native `Tool` record"),
        requires=re.compile(r"`icons`"),
        bad_sample=(
            "the full native `Tool` record (`name`, `title`, `description`, `inputSchema`, "
            "`outputSchema`, `annotations`, `_meta`)"
        ),
    ),
]


def line_ok(probe: Probe, line: str) -> bool:
    """A line passes if it is not an anchor line, or carries the required co-term."""
    return not probe.anchor.search(line) or bool(probe.requires.search(line))


def _scoped_lines(text: str, section: str | None) -> list[str]:
    if section is None:
        return text.splitlines()
    lines, out, inside, in_fence = text.splitlines(), [], False, False
    for ln in lines:
        if ln.startswith("```"):
            in_fence = not in_fence
        elif not in_fence and ln.startswith("## "):
            inside = ln[3:].strip() == section
            continue
        if inside:
            out.append(ln)
    return out


def self_test(probes: list[Probe]) -> bool:
    return all(not line_ok(p, p.bad_sample) for p in probes)


def run_probes(root: Path) -> list[str]:
    violations: list[str] = []
    for p in PROBES:
        target = root / p.path
        if not target.exists():
            violations.append(f"{p.name}: {p.path} missing")
            continue
        scoped = _scoped_lines(target.read_text(encoding="utf-8"), p.section)
        anchored = [ln for ln in scoped if p.anchor.search(ln)]
        if not anchored:
            violations.append(
                f"{p.name}: no line matching {p.anchor.pattern!r} in {p.path} — fact deleted?"
            )
            continue
        for ln in anchored:
            if not line_ok(p, ln):
                violations.append(
                    f"{p.name}: {p.path}: line lacks {p.requires.pattern!r}: {ln.strip()[:120]}"
                )
    return violations


def main() -> int:
    if not self_test(PROBES):
        print(
            "check_fact_sync: SELF-TEST FAILED — a probe no longer detects its known-bad sample",
            file=sys.stderr,
        )
        return 1
    violations = run_probes(SKILL_DIR)
    for v in violations:
        print(f"check_fact_sync: {v}", file=sys.stderr)
    if violations:
        return 1
    print(f"{len(PROBES)} fact-sync probes pass; self-test ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
