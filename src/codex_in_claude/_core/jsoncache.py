"""Generic bounded JSON file reader.

Lives in _core (no parent imports): reads and parses a JSON file defensively,
returning None on any problem rather than raising. Knows nothing about Codex or any
specific cache shape — callers layer their own validation on top.
"""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003
from typing import Any


def read_bounded_json(path: Path, max_bytes: int) -> Any | None:
    """Parse the JSON at `path`, or return None.

    Returns None when the path is missing, not a regular file, larger than
    `max_bytes`, unreadable, not valid UTF-8, not valid JSON, or nested deeply
    enough to blow the recursion limit. Never raises for those cases — a caller
    treats None as "no usable data" and falls back. `is_file()` follows symlinks,
    so a symlink is read but still size-capped and shape-validated downstream.

    The byte cap is enforced on the actual read (not a pre-check stat) to avoid
    TOCTOU races: reads `max_bytes + 1` bytes and rejects if `len > max_bytes`.
    """
    try:
        if not path.is_file():
            return None
        with path.open("rb") as fh:
            data = fh.read(max_bytes + 1)
        if len(data) > max_bytes:
            return None
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        # JSONDecodeError subclasses ValueError; RecursionError (not a ValueError)
        # fires on a deeply-nested document that stays under the byte cap.
        return None
