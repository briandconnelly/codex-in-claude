#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Enforce the contract-checklist rule-id convention.

`contract-checklist.md` is the single home for this skill's normative rules
(AGENTS.md: "A normative rule has exactly one home"). Every rule carries a
stable id of the form `[section.slug]`, and other files cite ids rather than
paraphrasing the rule text.

Checks:

  * every rule bullet declares a well-formed id, in a numbered section;
  * ids are unique and their section prefix matches the section they sit in;
  * rule-like bullets using a non-canonical marker are rejected, so a rule
    cannot dodge the id requirement by changing its bullet character;
  * declarations inside fenced code blocks do not count;
  * every cited id resolves — including citations that share a line with a
    declaration;
  * the declared id set matches the committed manifest, so deleting a rule
    nobody cites cannot pass silently.

The manifest check is the one that catches deletion. Citation resolution only
protects rules something else references, which is a minority of them.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CHECKLIST = SKILL_DIR / "references" / "contract-checklist.md"
MANIFEST = Path(__file__).resolve().parent / "rule-ids.txt"

SECTION_RE = re.compile(r"^## (\d+)\.")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
# The one canonical rule form: `- `[id]` **Lead.**`, optionally indented.
RULE_RE = re.compile(r"^(?P<indent>\s*)- `\[(?P<id>[^\]`]+)\]` \*\*")
# Any list item whose lead is bold — used to catch rules that skipped the id
# or used a marker the canonical form does not allow.
RULE_LIKE_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:`\[[^\]`]*\]`\s*)?\*\*")
ID_GRAMMAR = re.compile(r"^\d+\.[a-z0-9]+(?:-[a-z0-9]+)*$")
CITE_RE = re.compile(r"`\[(\d+\.[a-z0-9-]+)\]`")


def _strip_fenced(lines: list[str]) -> list[bool]:
    """Return a per-line mask: True when the line sits inside a code fence."""
    inside = False
    mask = []
    for line in lines:
        if FENCE_RE.match(line):
            mask.append(True)  # the fence marker itself is not content
            inside = not inside
            continue
        mask.append(inside)
    return mask


def declared_ids(text: str) -> tuple[dict[str, int], list[str]]:
    ids: dict[str, int] = {}
    problems: list[str] = []
    section: str | None = None
    lines = text.split("\n")
    fenced = _strip_fenced(lines)
    for lineno, (line, in_fence) in enumerate(zip(lines, fenced, strict=True), 1):
        if in_fence:
            continue
        if m := SECTION_RE.match(line):
            section = m.group(1)
            continue
        m = RULE_RE.match(line)
        if not m:
            if RULE_LIKE_RE.match(line):
                lead = line.strip()[:70]
                problems.append(
                    f"{CHECKLIST.name}:{lineno}: rule-like bullet is not in the "
                    f"canonical `- \\`[id]\\` **Lead.**` form: {lead}"
                )
            continue
        rule_id = m.group("id")
        if not ID_GRAMMAR.match(rule_id):
            problems.append(
                f"{CHECKLIST.name}:{lineno}: id '{rule_id}' is malformed "
                f"(want <section>.<lowercase-hyphenated-slug>)"
            )
            continue
        if section is None:
            problems.append(
                f"{CHECKLIST.name}:{lineno}: id '{rule_id}' declared before any numbered section"
            )
            continue
        if rule_id in ids:
            problems.append(
                f"{CHECKLIST.name}:{lineno}: duplicate id '{rule_id}' "
                f"(first declared at line {ids[rule_id]})"
            )
            continue
        prefix = rule_id.split(".", 1)[0]
        if prefix != section:
            problems.append(
                f"{CHECKLIST.name}:{lineno}: id '{rule_id}' declares section "
                f"{prefix} but appears in section {section}"
            )
        ids[rule_id] = lineno
    return ids, problems


def unresolved_citations(ids: dict[str, int]) -> list[str]:
    problems: list[str] = []
    for path in sorted(SKILL_DIR.rglob("*.md")):
        if any(part in {".pytest_cache", "runs"} for part in path.parts):
            continue
        rel = path.relative_to(SKILL_DIR)
        lines = path.read_text(encoding="utf-8").split("\n")
        fenced = _strip_fenced(lines)
        for lineno, (line, in_fence) in enumerate(zip(lines, fenced, strict=True), 1):
            if in_fence:
                continue
            scan = line
            if path == CHECKLIST and (m := RULE_RE.match(line)):
                # Skip only the declaration token, then scan the rest of the
                # line: a rule lead may itself cite another rule.
                scan = line[m.end("id") + 2 :]
            for cited in CITE_RE.findall(scan):
                if cited not in ids:
                    problems.append(f"{rel}:{lineno}: cites unknown rule id '{cited}'")
    return problems


def manifest_drift(ids: dict[str, int]) -> list[str]:
    if not MANIFEST.is_file():
        return [f"{MANIFEST.name}: missing rule-id manifest"]
    committed = set()
    for raw in MANIFEST.read_text(encoding="utf-8").split("\n"):
        line = raw.strip()
        # Test the stripped value: an indented comment would otherwise survive
        # the filter and be compared as if it were a rule id.
        if line and not line.startswith("#"):
            committed.add(line)
    problems = []
    for gone in sorted(committed - ids.keys()):
        problems.append(
            f"{MANIFEST.name}: rule '{gone}' is in the manifest but no longer "
            f"declared — if the removal is intended, drop it from the manifest "
            f"in the same commit"
        )
    for added in sorted(ids.keys() - committed):
        problems.append(
            f"{MANIFEST.name}: rule '{added}' is declared but not in the manifest — add it"
        )
    return problems


def main() -> int:
    if not CHECKLIST.is_file():
        print(f"missing {CHECKLIST}", file=sys.stderr)
        return 2
    ids, problems = declared_ids(CHECKLIST.read_text(encoding="utf-8"))
    if not ids:
        print("no rule ids found — the convention is not in place", file=sys.stderr)
        return 2
    problems += unresolved_citations(ids)
    problems += manifest_drift(ids)
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(f"{len(ids)} rule ids declared; all citations resolve; manifest matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
