#!/usr/bin/env python
"""Assert every GitHub Actions ``uses:`` reference is pinned to an immutable ref.

All workflow ``uses:`` entries in this repo are already pinned to full commit
SHAs, but nothing *enforces* it — ``allowed_actions: all`` and
``sha_pinning_required: false`` at the repo level, so a future workflow edit
could reintroduce a mutable tag (``@v4``) or branch (``@main``) reference
unnoticed. This is the CI lint half of that gap (issue #101): it scans the
committed workflow YAML and fails if any reference is mutable.

"Immutable" means:
    * a local action            -> ``./path`` (lives in this repo, no external ref)
    * an external action / reusable workflow -> ``owner/repo[/path]@<40-hex SHA>``
    * a Docker action           -> ``docker://image@sha256:<64-hex digest>``

Pure stdlib (no PyYAML): it matches scalar ``uses:`` keys line by line, which is
all Actions ever emits, and avoids adding a parse dependency to a CI gate.

Usage:
    uv run python scripts/check_github_actions_pinning.py [ROOT]

Exit codes:
    0  every ``uses:`` reference is immutably pinned
    1  drift: at least one mutable / unpinned reference (a real blocker)
    2  nothing to scan (no .github/workflows/*.y[a]ml found) — verify nothing
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

OK = "OK  "
FAIL = "FAIL"
WARN = "WARN"

# A ``uses:`` key, optionally as a YAML list item, capturing the rest of the line.
_USES_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*(.+?)\s*$")
# A key opening a block scalar (``run: |``, ``run: >-``, ``script: |2-`` …). Lines
# indented deeper than such a key are literal content, not YAML keys, so a content
# line beginning ``uses:`` must NOT be treated as an action reference. The header may
# carry chomping (``+``/``-``) and indentation (digit) indicators in either order.
_BLOCK_SCALAR_RE = re.compile(r"^\s*(?:-\s*)?[^\s:#]+:\s*[|>][-+0-9]*\s*(?:#.*)?$")
# A full 40-char git commit SHA (case-insensitive; GitHub emits lowercase).
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
# A Docker image pinned by immutable digest: docker://image@sha256:<64 hex>.
_DOCKER_DIGEST_RE = re.compile(r"^docker://[^@]+@sha256:[0-9a-fA-F]{64}$")


def _clean_value(raw: str) -> str:
    """Strip an inline ``# comment`` and any surrounding quotes from a value."""
    # YAML inline comments require whitespace before the '#'.
    value = re.sub(r"\s+#.*$", "", raw).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def iter_uses(text: str) -> list[tuple[int, str]]:
    """Return ``(lineno, value)`` for every ``uses:`` entry; lineno is 1-based.

    Lines inside a ``run: |`` / ``run: >`` block scalar (more indented than the key
    that opened it) are skipped so a shell line starting ``uses:`` is not mistaken
    for an action reference.
    """
    found: list[tuple[int, str]] = []
    block_indent: int | None = None  # column of the key that opened a block scalar
    for lineno, line in enumerate(text.splitlines(), start=1):
        indent = len(line) - len(line.lstrip(" "))
        if block_indent is not None:
            if line.strip() == "" or indent > block_indent:
                continue  # blank line or still inside the block's content
            block_indent = None  # dedented back to the opener's level: block ended
        if line.lstrip().startswith("#"):
            continue
        if _BLOCK_SCALAR_RE.match(line):
            block_indent = indent
            continue
        match = _USES_RE.match(line)
        if not match:
            continue
        value = _clean_value(match.group(1))
        if value:
            found.append((lineno, value))
    return found


def classify(value: str) -> str | None:
    """Return ``None`` if ``value`` is immutably pinned, else a violation reason."""
    if value.startswith("./"):
        return None
    if value.startswith("docker://"):
        if _DOCKER_DIGEST_RE.match(value):
            return None
        return "Docker action must be pinned by @sha256:<digest>, not a tag"
    _owner_repo, sep, ref = value.partition("@")
    if not sep:
        return "missing @<sha> ref"
    if _SHA_RE.match(ref):
        return None
    return f"ref '{ref}' is not a full 40-char commit SHA"


def _workflow_files(root: Path) -> list[Path]:
    base = root / ".github" / "workflows"
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*") if p.suffix in {".yml", ".yaml"} and p.is_file())


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(argv[0]) if argv else Path.cwd()

    files = _workflow_files(root)
    if not files:
        print(f"{WARN}: no .github/workflows/*.y[a]ml under {root} — nothing to verify.")
        return 2

    violations: list[str] = []
    for path in files:
        rel = path.relative_to(root)
        for lineno, value in iter_uses(path.read_text(encoding="utf-8")):
            reason = classify(value)
            if reason is not None:
                violations.append(f"{rel}:{lineno}: {value} — {reason}")

    if violations:
        for v in violations:
            print(f"{FAIL}: {v}")
        print(f"\n{len(violations)} unpinned GitHub Actions reference(s) found.")
        return 1

    print(f"{OK}: all GitHub Actions references across {len(files)} workflow file(s) are pinned.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
