#!/usr/bin/env python
"""Validate a commit message against this repo's Conventional Commit policy.

Run as a ``commit-msg`` hook (see ``prek.toml``): Git/prek passes the path to the
file holding the commit message as the first argument. The allowed types and
optional scopes mirror ``AGENTS.md`` — when that policy changes, update both.

Pure stdlib (no deps): the hook must run in any environment without setup.

Usage:
    uv run python scripts/check_commit_message.py <commit-msg-file>

Exit codes:
    0  the message is a valid Conventional Commit (or a git-generated form we skip)
    1  invalid message, or no file argument given
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Conventional Commit types allowed by AGENTS.md.
ALLOWED_TYPES = (
    "feat",
    "fix",
    "chore",
    "docs",
    "refactor",
    "test",
    "perf",
    "ci",
    "build",
    "revert",
)
# Optional scopes from the codebase areas listed in AGENTS.md.
ALLOWED_SCOPES = (
    "jobs",
    "cli-contract",
    "core",
    "tools",
    "schemas",
    "worktree",
    "packaging",
    "config",
)

# Auto-generated / non-Conventional forms that bypass validation. `revert:` is a
# Conventional type and is deliberately NOT here — only Git's `Revert "..."` is.
_SKIP_RE = re.compile(r'^(Merge |Revert "|fixup! |squash! )')

# type(scope)?!?: subject  — scope and the `!` breaking marker are optional.
_HEADER_RE = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[a-z-]+)\))?(?P<bang>!)?: (?P<subject>.+)$"
)

EXPECTED = (
    "Expected a Conventional Commit: `type(scope)?!?: subject`\n"
    f"  types:  {', '.join(ALLOWED_TYPES)}\n"
    f"  scopes (optional): {', '.join(ALLOWED_SCOPES)}\n"
    "  subject: imperative, lowercase, no trailing period."
)


def first_line(text: str) -> str:
    """Return the first non-blank, non-comment line of a commit message."""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("#"):
            continue
        return line
    return ""


def validate(header: str) -> str | None:
    """Return ``None`` if ``header`` is valid, else a human-readable reason.

    Git-generated forms (merge/revert/fixup/squash) are skipped (return ``None``).
    """
    if _SKIP_RE.match(header):
        return None
    match = _HEADER_RE.match(header)
    if not match:
        return "header does not match `type(scope)?!?: subject`"
    type_ = match.group("type")
    if type_ not in ALLOWED_TYPES:
        return f"type '{type_}' is not allowed"
    scope = match.group("scope")
    if scope is not None and scope not in ALLOWED_SCOPES:
        return f"scope '{scope}' is not allowed"
    subject = match.group("subject")
    if subject[0].isupper():
        return "subject must not start with a capital letter"
    if subject.endswith("."):
        return "subject must not end with a period"
    return None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("FAIL: no commit-message file argument given.")
        return 1

    header = first_line(Path(argv[0]).read_text(encoding="utf-8"))
    reason = validate(header)
    if reason is not None:
        print(f"FAIL: {reason}\n\n  {header!r}\n\n{EXPECTED}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
