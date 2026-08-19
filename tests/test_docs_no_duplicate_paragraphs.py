"""No Markdown doc repeats a prose paragraph verbatim.

#514 is the evidence: PR #511 re-added a paragraph that #508 had already placed in
`COMPATIBILITY.md`, leaving two verbatim consecutive copies that survived review and the
full gate. Every existing doc guard is presence-based (`"..." in text`), and a presence
check cannot see a second copy — it passes at one occurrence and at ten.

The scan is deliberately narrow. It looks only at multi-line prose paragraphs outside
fenced code, so a repeated table row, list item, heading, or code sample is not a
finding: those legitimately recur. What it catches is the shape #514 had — a whole
paragraph of prose duplicated.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories that hold no authored docs of ours.
_SKIP_DIRS = {".git", ".venv", "node_modules", "dist", "build", "__pycache__", ".ruff_cache"}

_FENCE = re.compile(r"^(```|~~~)")

# A paragraph must clear both to be scanned: enough lines and enough characters that a
# verbatim repeat is authorial duplication rather than a recurring short phrase.
_MIN_LINES = 2
_MIN_CHARS = 120

# Leaders that mark a block as structural rather than prose. These recur by design.
_STRUCTURAL_LEADERS = ("-", "*", "|", "#", ">", "1.")


def _prose_paragraphs(text: str) -> list[str]:
    """Whitespace-normalized prose paragraphs, code fences and structure removed."""
    blocks: list[list[str]] = []
    current: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.strip():
            current.append(line.strip())
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)

    paragraphs = []
    for block in blocks:
        if len(block) < _MIN_LINES:
            continue
        if block[0].startswith(_STRUCTURAL_LEADERS):
            continue
        joined = " ".join(block)
        if len(joined) < _MIN_CHARS:
            continue
        paragraphs.append(joined)
    return paragraphs


def _duplicate_paragraphs(text: str) -> list[str]:
    seen: set[str] = set()
    duplicates = []
    for paragraph in _prose_paragraphs(text):
        if paragraph in seen:
            duplicates.append(paragraph)
        seen.add(paragraph)
    return duplicates


def _markdown_files() -> list[Path]:
    return sorted(
        path
        for path in _REPO_ROOT.rglob("*.md")
        if not _SKIP_DIRS & set(path.relative_to(_REPO_ROOT).parts)
    )


_MARKDOWN_FILES = _markdown_files()


@pytest.mark.parametrize("path", _MARKDOWN_FILES, ids=lambda p: str(p.relative_to(_REPO_ROOT)))
def test_doc_has_no_duplicate_prose_paragraph(path):
    duplicates = _duplicate_paragraphs(path.read_text(encoding="utf-8"))
    assert not duplicates, (
        f"{path.relative_to(_REPO_ROOT)} repeats a paragraph verbatim: "
        f"{duplicates[0][:120]!r}... — delete the redundant copy"
    )


def test_scan_covers_the_docs_that_matter():
    """Guard the guard: an empty or shrunken corpus would pass vacuously."""
    covered = {str(p.relative_to(_REPO_ROOT)) for p in _MARKDOWN_FILES}
    assert {"COMPATIBILITY.md", "README.md", "AGENTS.md", "CHANGELOG.md"} <= covered
    assert len(covered) > 20, f"only {len(covered)} Markdown files scanned"


def test_scan_detects_the_regression_it_guards():
    """Positive control: the exact #514 shape must be reported, not merely absent now.

    A clean run over the repo is only evidence if the instrument can fail. This feeds it
    the paragraph #514 filed, duplicated the way `main` carried it.
    """
    paragraph = (
        "`recommended_plugins` is `stable`/default-**off** at `0.148.0` and is left unreserved\n"
        "on the same reasoning as above — adjacency in the feature table is not evidence that it\n"
        "bypasses the `remote_plugin` guarantee. `docs/UPGRADING-CODEX.md` owns the obligation to\n"
        "re-check both flags on each upgrade.\n"
    )
    assert _duplicate_paragraphs(f"# Doc\n\n{paragraph}\n{paragraph}")
    # ...and a single copy is not a finding.
    assert not _duplicate_paragraphs(f"# Doc\n\n{paragraph}")


def test_scan_ignores_structure_that_recurs_by_design():
    """Repeated table rows, list items, and code samples are legitimate, not findings."""
    table = "| flag | value |\n| --- | --- |\n| `--ephemeral` | on |\n"
    assert not _duplicate_paragraphs(f"{table}\n{table}")

    code = (
        "```sh\n"
        "codex exec --json --sandbox read-only --cd . --ephemeral --ignore-user-config\n"
        "```\n"
    )
    assert not _duplicate_paragraphs(f"{code}\n{code}")

    bullets = (
        "- the resolved workspace's own `AGENTS.md`, which Codex loads implicitly on every run\n"
        "- the project skills root, whose names and descriptions reach the model up front\n"
    )
    assert not _duplicate_paragraphs(f"{bullets}\n{bullets}")
