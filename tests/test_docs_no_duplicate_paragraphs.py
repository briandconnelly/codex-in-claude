"""No Markdown doc repeats a prose paragraph verbatim.

#514 is the evidence: PR #511 re-added a paragraph that #508 had already placed in
`COMPATIBILITY.md`, leaving two verbatim consecutive copies that survived review and the
full gate. Every existing doc guard is presence-based (`"..." in text`), and a presence
check cannot see a second copy — it passes at one occurrence and at ten.

The scan is deliberately narrow. It looks only at prose paragraphs outside fenced code, so
a repeated table row, list item, heading, or code sample is not a finding: those
legitimately recur. What it catches is the shape #514 had — a whole paragraph of prose
duplicated.

Narrow is not the same as leaky, and three blind spots a Codex review surfaced are closed
here. Each was measured against this repo's own docs, because "the corpus is clean" says
nothing about what the scan never looked at:

- **Bold-led prose was invisible.** Skipping any block whose first character is `*` also
  skipped every paragraph opening `**Rule:** ...` — 432 blocks in these docs, which is a
  house style, not an edge case. List markers now require the space Markdown requires.
- **Single-line paragraphs were invisible.** Demanding two physical lines made the guard
  depend on how a paragraph happened to wrap; 573 were out of scope, and the #514
  paragraph itself would have slipped through had it been written unwrapped. Length alone
  decides now.
- **Fence tracking could not survive a nested example.** A boolean toggled by any fence
  marker closes a four-backtick fence on the three-backtick sample inside it, and closes a
  backtick fence on a tilde one. Fences now close only on their own delimiter at their own
  length or longer, per CommonMark, and a fence boundary ends the paragraph it interrupts.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories that hold no authored docs of ours.
_SKIP_DIRS = {".git", ".venv", "node_modules", "dist", "build", "__pycache__", ".ruff_cache"}

# A fence opens with at least three backticks or tildes and closes only on the same
# character, at least as long (CommonMark 4.5). A longer outer fence is the standard way
# to show a fenced example inside a fenced block, and this repo's docs do it.
_FENCE_OPEN = re.compile(r"^(?P<delim>`{3,}|~{3,})")

# Length alone qualifies a paragraph: long enough that a verbatim repeat is authorial
# duplication rather than a recurring short phrase. There is deliberately no line-count
# threshold — see the module docstring.
_MIN_CHARS = 120

# Structural blocks recur by design, so they are not duplication findings. Matched as
# Markdown syntax rather than by bare first character: `*` opens a list only when a space
# follows, and `**Rule:** ...` is prose. Ordered lists may open at any digit — 35 blocks
# here open at 2. or higher — and either delimiter.
_STRUCTURAL_BLOCK = re.compile(
    r"""
    ^(?:
        [-*+][ \t]        # unordered list item
      | \d+[.)][ \t]      # ordered list item, opening at any digit
      | \#{1,6}(?:[ \t]|$)  # ATX heading
      | >                 # block quote
      | \|                # table row
    )
    """,
    re.VERBOSE,
)


def _prose_paragraphs(text: str) -> list[str]:
    """Whitespace-normalized prose paragraphs, code fences and structure removed."""
    blocks: list[list[str]] = []
    current: list[str] = []
    open_fence: str | None = None

    def flush() -> None:
        nonlocal current
        if current:
            blocks.append(current)
            current = []

    for raw in text.splitlines():
        line = raw.strip()
        match = _FENCE_OPEN.match(line)
        if open_fence is None:
            if match:
                # A fence interrupts whatever paragraph preceded it, blank line or not.
                flush()
                open_fence = match.group("delim")
                continue
        else:
            closes = (
                match is not None
                and match.group("delim")[0] == open_fence[0]
                and len(match.group("delim")) >= len(open_fence)
            )
            if closes:
                open_fence = None
            continue
        if line:
            current.append(line)
        else:
            flush()
    flush()

    paragraphs = []
    for block in blocks:
        if _STRUCTURAL_BLOCK.match(block[0]):
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

    # An ordered list may open at any digit, not only 1. — this repo's docs hold blocks
    # that start at 2., 3., and 0., and a literal "1." skip would scan them as prose.
    for marker in ("1.", "2.", "9)", "0."):
        numbered = (
            f"{marker} Check whether the repo already carries a config, and keep it if so\n"
            f"{marker} Reinstall the Git shims so every hook runs from the new toolchain\n"
        )
        assert not _duplicate_paragraphs(f"{numbered}\n{numbered}"), marker

    # Every unordered marker Markdown defines, `+` included.
    for marker in ("-", "*", "+"):
        listed = (
            f"{marker} the resolved workspace's own `AGENTS.md`, loaded implicitly every run\n"
            f"{marker} the project skills root, whose descriptions reach the model up front\n"
        )
        assert not _duplicate_paragraphs(f"{listed}\n{listed}"), marker


def test_bold_led_prose_is_prose_not_a_list_item():
    """A `*` skip on the bare character hides this repo's most common paragraph shape.

    `**Rule:** ...` opens 432 blocks in these docs. A list marker needs the space
    Markdown requires, so bold-led prose stays in scope and a duplicate of it is caught.
    """
    paragraph = (
        "**Rule: bridge policy stays here.** Codex CLI assumptions, the MCP surface and its\n"
        "result semantics, and this bridge's pinned knobs. Use of a pontonier type does not\n"
        "make the policy around it shared.\n"
    )
    assert _duplicate_paragraphs(f"# Doc\n\n{paragraph}\n{paragraph}")
    assert not _duplicate_paragraphs(f"# Doc\n\n{paragraph}")


def test_a_single_line_paragraph_is_still_a_paragraph():
    """Whether a paragraph wraps is a source-formatting accident, not a semantic one.

    Requiring two physical lines put 573 paragraphs of these docs out of scope, and the
    #514 paragraph itself would have slipped through had it been written unwrapped.
    """
    unwrapped = (
        "`recommended_plugins` is `stable`/default-off at `0.148.0` and is left unreserved on "
        "the same reasoning as above — adjacency in the feature table is not evidence that it "
        "bypasses the `remote_plugin` guarantee.\n"
    )
    assert _duplicate_paragraphs(f"# Doc\n\n{unwrapped}\n{unwrapped}")

    # Short repeated lines stay out — a recurring cross-reference is not duplication.
    short = "See [`docs/UPGRADING-CODEX.md`](docs/UPGRADING-CODEX.md).\n"
    assert not _duplicate_paragraphs(f"# Doc\n\n{short}\n{short}")


def test_a_fence_closes_only_on_its_own_delimiter():
    """A longer outer fence wrapping a fenced example must not close on the inner one.

    Asserted on what the scanner *extracts*, not on whether a duplicate is reported. A
    broken fence toggle exposes fenced content as prose, and a test phrased as "no
    duplicate found" passes against that happily — the exposed content simply has nothing
    to collide with. The inner line here is over the length threshold on purpose, so it
    becomes a visible paragraph the moment the fence tracking is wrong.
    """
    long_line = (
        "codex exec --json --sandbox read-only --cd . --ephemeral --ignore-user-config "
        "--ignore-rules --skip-git-repo-check --disable remote_plugin --output-last-message f"
    )
    assert len(long_line) >= _MIN_CHARS  # or the assertions below cannot fail

    nested = f"````markdown\n```sh\n{long_line}\n```\n````\n"
    assert _prose_paragraphs(nested) == [], "an inner fence closed the longer outer fence"

    mixed = f"```markdown\n~~~sh\n{long_line}\n~~~\n```\n"
    assert _prose_paragraphs(mixed) == [], "a tilde marker closed a backtick fence"

    # ...and a fence that genuinely closes still releases the prose that follows it.
    paragraph = (
        "The sandbox bounds writes, not reads. Neither `read-only` nor `workspace-write`\n"
        "confines what Codex may read: it can read files outside the workspace entirely.\n"
    )
    assert _duplicate_paragraphs(f"{nested}\n{paragraph}\n{paragraph}")


def test_a_fence_ends_the_paragraph_it_interrupts():
    """Prose butted against a fence with no blank line is its own paragraph.

    Without this, the text before and after a fence merges into one block, so neither
    half can ever match its own duplicate elsewhere in the file.
    """
    paragraph = (
        "The sandbox bounds writes, not reads. Neither `read-only` nor `workspace-write`\n"
        "confines what Codex may read: it can read files outside the workspace entirely.\n"
    )
    fence = "```sh\ncodex --version\n```\n"
    # No blank line anywhere: paragraph, fence, paragraph.
    assert _duplicate_paragraphs(f"{paragraph}{fence}{paragraph}")
