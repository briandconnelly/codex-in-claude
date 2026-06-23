"""Read Codex's on-disk model catalog for advisory `model`-slug discovery.

Codex-specific glue around the generic _core.jsoncache reader: resolves $CODEX_HOME,
reads models_cache.json, validates its shape defensively, and falls back to the bundled
KNOWN_MODEL_SLUGS when the cache is absent/unreadable. Discovery only — the result is
explicitly advisory; `codex exec` validates the real slug.
"""

from __future__ import annotations

import os
from pathlib import Path

from codex_in_claude import cli_contract
from codex_in_claude._core.jsoncache import read_bounded_json
from codex_in_claude.schemas import ModelCatalogResult, ModelInfo

_ADVISORY = (
    "Advisory model list for the `model` param — not authoritative. Codex validates "
    "the slug at run time; an unlisted slug may still work and a listed one may be "
    "unavailable to your account."
)
_UNAVAILABLE = (
    "No model catalog found: Codex has not written its on-disk cache yet (a fresh "
    "install populates it on first use) and no bundled fallback is configured. Pass a "
    "known Codex model slug directly; it is validated at run time."
)


def _codex_home() -> Path | None:
    """$CODEX_HOME if set, else ~/.codex (matching the codex CLI's own resolution).

    Returns None if the path cannot be expanded (e.g. CODEX_HOME=~missing_user, where
    expanduser() raises RuntimeError) so the caller falls back instead of crashing.
    """
    env = os.environ.get("CODEX_HOME")
    try:
        return Path(env).expanduser() if env else Path.home() / ".codex"
    except RuntimeError:
        return None


def _parse_models(raw: object) -> tuple[list[ModelInfo], str | None, str | None] | None:
    """Validate the cache's expected shape, or None if it has drifted.

    Returns (models, fetched_at, client_version). Drops entries whose slug fails
    MODEL_SLUG_PATTERN and caps the list at MODELS_CACHE_MAX_ENTRIES; returns None when
    the top-level shape is wrong or no valid entry survives (caller falls back to static).
    """
    if not isinstance(raw, dict):
        return None
    entries = raw.get("models")
    if not isinstance(entries, list):
        return None
    models: list[ModelInfo] = []
    for entry in entries[: cli_contract.MODELS_CACHE_MAX_ENTRIES]:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug")
        if not isinstance(slug, str) or not cli_contract.MODEL_SLUG_PATTERN.match(slug):
            continue
        display = entry.get("display_name")
        display = display if isinstance(display, str) and len(display) <= 128 else None
        models.append(ModelInfo(slug=slug, display_name=display))
    if not models:
        return None
    fetched_at = raw.get("fetched_at")
    fetched_at = fetched_at if isinstance(fetched_at, str) and len(fetched_at) <= 64 else None
    version = raw.get("client_version")
    version = version if isinstance(version, str) and len(version) <= 64 else None
    return models, fetched_at, version


def read_model_catalog() -> ModelCatalogResult:
    """The advisory model catalog: live cache if usable, else bundled static, else none."""
    home = _codex_home()
    raw = (
        read_bounded_json(
            home / cli_contract.MODELS_CACHE_FILENAME,
            cli_contract.MODELS_CACHE_MAX_BYTES,
        )
        if home is not None
        else None
    )
    parsed = _parse_models(raw) if raw is not None else None
    if parsed is not None:
        models, fetched_at, version = parsed
        return ModelCatalogResult(
            source="cache",
            models=models,
            fetched_at=fetched_at,
            cache_client_version=version,
            advisory=_ADVISORY,
        )
    if cli_contract.KNOWN_MODEL_SLUGS:
        return ModelCatalogResult(
            source="static",
            models=[ModelInfo(slug=s) for s in cli_contract.KNOWN_MODEL_SLUGS],
            advisory=_ADVISORY,
        )
    return ModelCatalogResult(source="none", advisory=_ADVISORY, unavailable_reason=_UNAVAILABLE)
