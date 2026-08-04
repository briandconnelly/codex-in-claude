#!/usr/bin/env python3
"""Enforce single-authority and reference integrity across agent-friendly-github.

Why this exists: twice during the 2026-08-03 remediation, a normative rule was
corrected in its canonical home while paraphrases of the refuted version
survived in other files — once in a *test assertion*, which would have failed a
correct run.  AGENTS.md says a normative rule has exactly one home and every
other file points at it; that convention prevents drift only if corrections are
swept, and a human sweeping greps by hand misses files.  This is the sweep.

Five checks, each independently self-tested:

1. REFUTED   — claims the skill has established are false must not reappear
               in any wording, unless the line negates them.  Applies inside
               fenced code blocks as well: those are emitted artifacts that
               get copied into real repositories, so a refuted claim in an
               example `AGENTS.md` travels further than one in prose.
2. GOVERNED  — a rule's values may be STATED only in its declared home; other
               files must cite (name the home file or its owning section)
               rather than restate.  Skips fences — an example legitimately
               contains the config values it illustrates.
3. SECTIONS  — every "§N" reference resolves to a section heading that exists
               in config-checklist.md.
4. THREATS   — every "Tn" citation resolves to a threat class defined in
               threat-model.md, and every defined class is cited at least once.
5. LINKS     — every relative markdown link resolves to a file that exists.

Scope: SKILL.md, references/**.md, tests/scenarios.md.  decisions/ is excluded
from REFUTED and GOVERNED: the fact sheet's job is to record what was believed,
corrected, and sourced, so it necessarily states values and quotes refuted
claims.  It is still covered by SECTIONS, THREATS, and LINKS.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent

# --- 1. Refuted claims -------------------------------------------------------
# Each entry: (label, pattern, negation markers that make the line legitimate).
# A line matching the pattern fails unless it also carries a negation marker,
# which is how the skill is allowed to say "there is no such permission".

REFUTED: list[tuple[str, re.Pattern[str], tuple[str, ...]]] = [
    (
        "a withholdable release/package permission (releases ride on contents:write)",
        re.compile(
            r"no release (?:or|and) package write"
            r"|release / package write"
            r"|release write permission"
            r"|holds no release"
            # "...are permissions in their own right, not a side effect of contents: write"
            r"|permissions in their own right",
            re.IGNORECASE,
        ),
        # Unambiguous negations only. A modality word like "cannot" would let
        # "release write permission cannot be dropped" — which restates the
        # refuted idea — pass as if it denied it.
        ("there is no", "no separable", "deliberately no", "not a permission"),
    ),
    (
        "the solo interim's gates binding the shared-credential agent",
        re.compile(
            r"genuinely bind every actor"
            r"|gates that bind every actor"
            r"|actor-independent gates (?:still )?(?:bind|hold|carry)",
            re.IGNORECASE,
        ),
        ("do not", "does not", "no longer", "cannot", "falsely"),
    ),
    (
        "pull_request_review runs attaching to the PR head SHA",
        re.compile(r"attach to the PR'?s? head SHA|runs attach to the head SHA", re.IGNORECASE),
        ("not the", "NOT the", "wrong", "contradict"),
    ),
    (
        "human-only-approvals described as enforcement rather than detection",
        re.compile(r"makes the policy enforceable|enforces the human-only", re.IGNORECASE),
        ("provisional", "detection", "unverified", "not an enforced"),
    ),
    (
        # Deliberately a REFUTED rule, not a GOVERNED one.  The pre-fix line
        # "at the organization level if the repo is org-owned, per §2" cited its
        # authority while restating a condition the authority had corrected —
        # citation veneer.  Requiring the plan-gate marker itself, rather than
        # any citation, is what catches that.
        "organization-level ruleset prescribed without its Enterprise plan gate",
        # \W{0,4} so markdown emphasis does not smuggle it past: the pre-fix
        # line was "at the **organization** level", which "organization-level"
        # alone does not match.
        re.compile(r"organization\W{0,4}level", re.IGNORECASE),
        # Descriptive contexts — auditing which placement exists, or stating the
        # admin-edit mechanism — are not prescriptions and need no plan gate.
        (
            "plan",
            "enterprise",
            "caveats",
            "inherited",
            "write access",
            "record whether",
            "can edit or delete",
        ),
    ),
]

# --- 2. Governed rules -------------------------------------------------------
# A rule's VALUES live in one home.  Elsewhere the same line must cite the home
# (by filename or by the owning section marker) instead of restating.

GOVERNED: list[tuple[str, re.Pattern[str], tuple[str, ...], tuple[str, ...]]] = [
    (
        "solo interim review count",
        re.compile(r"required_approving_review_count`?:?\s*(?:to\s*)?`?0`?|reviews (?:are |at )0"),
        ("references/config-checklist.md",),
        ("config-checklist", "Repository Profiles", "interim posture", "§2"),
    ),
    (
        # `exempt` is banned in every profile, so naming it is never a
        # profile-dependent restatement — only pull_request/always are governed.
        "bypass-actors composition",
        re.compile(r"bypass_mode`?:?\s*`?(?:pull_request|always)"),
        ("references/config-checklist.md", "references/examples/rulesets.md"),
        ("config-checklist", "§2", "profile"),
    ),
]

# tests/scenarios.md is exempt from GOVERNED but NOT from REFUTED.  A scenario
# must state the values it exercises — a fixture that cannot quote
# `required_approving_review_count: 0` cannot test the interim.  What it must
# never do is assert a rule the skill has refuted, which is how the 2026-08-03
# remediation left an assertion that would fail a correct run; REFUTED covers
# exactly that, and covers it here too.
GOVERNED_EXEMPT = {"tests/scenarios.md"}

# --- helpers -----------------------------------------------------------------

CODE_FENCE = re.compile(r"^\s*```")
SECTION_REF = re.compile(r"§(\d+)")
THREAT_REF = re.compile(r"\bT(\d+)\b")
THREAT_DEF = re.compile(r"^##\s+T(\d+)\s+—", re.MULTILINE)
THREAT_DEF_LINE = re.compile(r"^##\s+T\d+\s+—")
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)#][^)]*)\)")


def scan_files() -> list[Path]:
    files = [SKILL_DIR / "SKILL.md", SKILL_DIR / "tests" / "scenarios.md"]
    files += sorted((SKILL_DIR / "references").rglob("*.md"))
    return [f for f in files if f.exists()]


def all_files() -> list[Path]:
    return scan_files() + sorted((SKILL_DIR / "decisions").glob("*.md"))


def rel(path: Path) -> str:
    return str(path.relative_to(SKILL_DIR))


def check_refuted(line: str) -> list[str]:
    hits = []
    low = line.lower()
    for label, pattern, negations in REFUTED:
        if pattern.search(line) and not any(n.lower() in low for n in negations):
            hits.append(f"refuted claim restated — {label}")
    return hits


def check_governed(relpath: str, line: str) -> list[str]:
    hits = []
    if relpath in GOVERNED_EXEMPT:
        return hits
    for label, pattern, homes, citations in GOVERNED:
        if relpath in homes or not pattern.search(line):
            continue
        if not any(c.lower() in line.lower() for c in citations):
            hits.append(
                f"rule stated outside its home — {label}"
                f" (home: {homes[0]}; cite it instead of restating)"
            )
    return hits


def check_links(path: Path, line: str) -> list[str]:
    hits = []
    for match in MD_LINK.finditer(line):
        target = match.group(1)
        if target.startswith(("http", "mailto", "/")):
            continue
        target = target.split("#")[0]
        if target and not (path.parent / target).resolve().exists():
            hits.append(f"broken link — {target}")
    return hits


def defined_sections() -> set[str]:
    text = (SKILL_DIR / "references" / "config-checklist.md").read_text(encoding="utf-8")
    return set(re.findall(r"^##\s+§(\d+)", text, re.MULTILINE))


def defined_threats() -> set[str]:
    text = (SKILL_DIR / "references" / "threat-model.md").read_text(encoding="utf-8")
    return set(THREAT_DEF.findall(text))


def self_test() -> None:
    """Each check must flag a known-bad line and pass a known-good one.

    A checker that cannot fail is not evidence, and a clean run from a broken
    checker is indistinguishable from a clean tree.
    """
    cases: list[tuple[str, str, bool]] = [
        # (check, line, should_flag) — 'check' selects which function to call.
        ("refuted", "the identity holds no release or package write permission", True),
        ("refuted", "There is deliberately NO release row: releases ride on contents:write", False),
        # Modality is not negation: this restates the refuted idea.
        ("refuted", "the release write permission cannot be dropped", True),
        ("refuted", "rely on the gates that genuinely bind every actor", True),
        ("refuted", "these gates do not genuinely bind every actor once admin is held", False),
        ("refuted", "pull_request_review runs attach to the PR's head SHA", True),
        ("refuted", "GITHUB_SHA is the merge commit, NOT the PR head SHA", False),
        ("refuted", "This check makes the policy enforceable", True),
        ("refuted", "provisional detection; it does not make the policy enforceable", False),
        ("governed", "set required_approving_review_count: 0 in the interim", True),
        ("governed", "set required_approving_review_count: 0 per config-checklist.md", False),
        ("governed", "add the maintainer with bypass_mode: pull_request", True),
        ("governed", "use the bypass_mode: pull_request entry §2 specifies", False),
        ("refuted", "create an organization-level ruleset if the repo is org-owned", True),
        # Citation veneer: cites its authority while restating the corrected
        # condition.  This is the line the pre-fix tree actually shipped.
        ("refuted", "at the **organization** level if the repo is org-owned, per §2", True),
        ("refuted", "an organization-level ruleset where the plan provides one (§2)", False),
        ("refuted", "release tags are permissions in their own right", True),
        # Real lines from the tree that must NOT flag — pinned so a future
        # loosening of these patterns is a deliberate act, not a regression.
        (
            "governed",
            "Flag any `bypass_mode: exempt` entry regardless of actor — exempt skips",
            False,
        ),
        (
            "refuted",
            "a repo admin may lack write access on an inherited organization-level ruleset",
            False,
        ),
        # A refuted claim in a TEST file must still fail, even though
        # tests/scenarios.md is exempt from GOVERNED.
        ("refuted", "- [ ] Requires the identity to hold no release or package write", True),
    ]
    for check, line, should_flag in cases:
        if check == "refuted":
            got = bool(check_refuted(line))
        else:
            got = bool(check_governed("references/setup-workflow.md", line))
        if got is not should_flag:
            print(
                f"check_authority: SELF-TEST FAILED ({check}, expected "
                f"{'flag' if should_flag else 'pass'}): {line!r}",
                file=sys.stderr,
            )
            raise SystemExit(2)

    # THREATS: a definition heading must NOT count as a citation, or the
    # dead-class check silently never fires (every class would self-cite).
    cited: set[str] = set()
    check_refs(SKILL_DIR / "SKILL.md", "## T7 — Dependency confusion", {"7"}, {"7"}, cited)
    if cited:
        print(
            "check_authority: SELF-TEST FAILED (a threat heading counted as a citation)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    check_refs(SKILL_DIR / "SKILL.md", "mitigated by pinning (T7)", {"7"}, {"7"}, cited)
    if "7" not in cited:
        print(
            "check_authority: SELF-TEST FAILED (a real citation was not counted)",
            file=sys.stderr,
        )
        raise SystemExit(2)

    # LINKS must reject a target that does not exist and accept one that does.
    probe = SKILL_DIR / "SKILL.md"
    if not check_links(probe, "[x](references/does-not-exist.md)"):
        print("check_authority: SELF-TEST FAILED (links accepted a missing file)", file=sys.stderr)
        raise SystemExit(2)
    if check_links(probe, "[x](references/config-checklist.md)"):
        print("check_authority: SELF-TEST FAILED (links rejected a real file)", file=sys.stderr)
        raise SystemExit(2)


def check_refs(
    path: Path, line: str, sections: set[str], threats: set[str], cited: set[str]
) -> list[str]:
    """SECTIONS, THREATS, and LINKS — the checks that apply to every file."""
    found = check_links(path, line)
    for n in SECTION_REF.findall(line):
        if n not in sections:
            found.append(f"§{n} does not resolve to a config-checklist.md section")
    # A threat's own "## Tn — ..." heading is a definition, not a citation.
    # Counting it would make every defined class self-cite, and the
    # dead-class check below could then never fire.
    defines_itself = bool(THREAT_DEF_LINE.match(line))
    for n in THREAT_REF.findall(line):
        if not defines_itself:
            cited.add(n)
        if n not in threats:
            found.append(f"T{n} is cited but not defined in threat-model.md")
    return found


def main() -> int:
    self_test()

    hits: list[str] = []
    sections = defined_sections()
    threats = defined_threats()
    cited_threats: set[str] = set()
    governed_scope = set(scan_files())

    for path in all_files():
        relpath = rel(path)
        in_fence = False
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if CODE_FENCE.match(line):
                in_fence = not in_fence
                continue

            # REFUTED applies inside fences too: the fenced blocks here are
            # emitted artifacts (AGENTS.md, CONTRIBUTING.md, ruleset JSON) that
            # get copied into real repositories, so a refuted claim there ships
            # further than one in prose. GOVERNED and the reference checks skip
            # fences — an example legitimately contains config values and
            # illustrative paths that are not restatements or real links.
            found: list[str] = []
            if path in governed_scope:
                found += check_refuted(line)
            if in_fence:
                for problem in found:
                    hits.append(f"{relpath}:{lineno}: {problem}\n      {line.strip()[:110]}")
                continue

            found += check_refs(path, line, sections, threats, cited_threats)
            if path in governed_scope:
                found += check_governed(relpath, line)

            for problem in found:
                hits.append(f"{relpath}:{lineno}: {problem}\n      {line.strip()[:110]}")

    for n in sorted(threats - cited_threats, key=int):
        hits.append(f"references/threat-model.md: T{n} defined but never cited — dead threat class")

    if hits:
        print(f"{len(hits)} authority/reference problem(s):", file=sys.stderr)
        for hit in hits:
            print(f"  {hit}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
