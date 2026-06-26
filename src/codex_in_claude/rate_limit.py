"""Persist, load, and interpret the latest Codex rate-limit snapshot.

Capture is opportunistic: paid calls already parse the token_count event for token
usage, so we lift the sibling rate_limits block at no extra spend, persist the latest,
and interpret it against each window's own resets_at when read — so a stale cache
can't mislead: an unobserved (reset-passed or missing) window never reports as
available, while conservative limited/exhausted verdicts from open windows survive."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from codex_in_claude import config

if TYPE_CHECKING:
    from codex_in_claude.schemas import RateLimitSnapshot

CACHE_VERSION = 1


def save(
    snapshot: RateLimitSnapshot,
    *,
    now_epoch: int,
    path: Path | None = None,
    home: str | None = None,
) -> None:
    """Persist the latest snapshot, best-effort and atomically. Never raises: a write
    failure must never fail the underlying paid call. Uses a unique temp file +
    os.replace (mirroring _worker._atomic_write) so a concurrent paid call or a
    codex_status read never observes a truncated file. Last writer wins."""
    target = path or config.rate_limit_snapshot_file()
    home_str = home if home is not None else str(config.codex_home())
    payload = {
        "version": CACHE_VERSION,
        "captured_at": now_epoch,
        "codex_home": home_str,
        "snapshot": snapshot.model_dump(mode="json"),
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    except OSError:
        return
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload))
        Path(tmp).replace(target)
    except OSError:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()


def _load_raw(path: Path | None = None) -> dict | None:
    target = path or config.rate_limit_snapshot_file()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(data, dict) and data.get("version") == CACHE_VERSION:
        return data
    return None
