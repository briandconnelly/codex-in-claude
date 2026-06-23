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
    `max_bytes`, unreadable, or not valid UTF-8 JSON. Never raises for those cases —
    a caller treats None as "no usable data" and falls back. `is_file()` follows
    symlinks, so a symlink is read but still size-capped and shape-validated downstream.
    """
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > max_bytes:
            return None
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(text)
    except ValueError:  # JSONDecodeError subclasses ValueError
        return None
