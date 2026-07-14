# tests/test_codex_models.py
import json
from pathlib import Path

from codex_in_claude import cli_contract, codex_models


def _write_cache(home: Path, payload: dict) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / cli_contract.MODELS_CACHE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


def test_reads_cache_when_present(tmp_path, monkeypatch):
    _write_cache(
        tmp_path,
        {
            "fetched_at": "2026-06-23T00:04:15Z",
            "client_version": "0.141.0",
            "models": [
                {"slug": "gpt-5.5", "display_name": "GPT-5.5"},
                {"slug": "gpt-5.4", "display_name": "GPT-5.4"},
            ],
        },
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    cat = codex_models.read_model_catalog()
    assert cat.source == "cache"
    assert [m.slug for m in cat.models] == ["gpt-5.5", "gpt-5.4"]
    assert cat.models[0].display_name == "GPT-5.5"
    assert cat.fetched_at == "2026-06-23T00:04:15Z"
    assert cat.cache_client_version == "0.141.0"
    assert cat.advisory


def test_falls_back_to_static_when_cache_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))  # empty dir, no cache file
    cat = codex_models.read_model_catalog()
    assert cat.source == "static"
    assert {m.slug for m in cat.models} == set(cli_contract.KNOWN_MODEL_SLUGS)
    assert cat.fetched_at is None


def test_default_home_used_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _write_cache(tmp_path / ".codex", {"models": [{"slug": "gpt-5.5"}]})
    cat = codex_models.read_model_catalog()
    assert cat.source == "cache"
    assert [m.slug for m in cat.models] == ["gpt-5.5"]


def test_unexpandable_codex_home_falls_back_to_static(monkeypatch):
    # CODEX_HOME=~missing_user makes Path.expanduser() raise RuntimeError; the catalog
    # must fall back instead of letting that escape the defensive path.
    monkeypatch.setenv("CODEX_HOME", "~definitely_not_a_real_user_zzzz")
    cat = codex_models.read_model_catalog()
    assert cat.source == "static"
    assert {m.slug for m in cat.models} == set(cli_contract.KNOWN_MODEL_SLUGS)


def test_malformed_shape_falls_back_to_static(tmp_path, monkeypatch):
    _write_cache(tmp_path, {"models": "not-a-list"})
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert codex_models.read_model_catalog().source == "static"


def test_junk_entries_are_filtered(tmp_path, monkeypatch):
    _write_cache(
        tmp_path,
        {"models": [{"slug": "gpt-5.5"}, {"slug": "bad slug!"}, {"no_slug": 1}, "nope"]},
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    cat = codex_models.read_model_catalog()
    assert cat.source == "cache"
    assert [m.slug for m in cat.models] == ["gpt-5.5"]


def test_oversize_cache_falls_back(tmp_path, monkeypatch):
    # Exceed the byte cap directly — the size check rejects before parsing, so the
    # content need not be valid JSON and we avoid building a multi-MB document.
    oversize = b"x" * (cli_contract.MODELS_CACHE_MAX_BYTES + 1)
    (tmp_path / cli_contract.MODELS_CACHE_FILENAME).write_bytes(oversize)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert codex_models.read_model_catalog().source == "static"


def test_source_none_when_no_cache_and_no_static(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(cli_contract, "KNOWN_MODEL_SLUGS", ())
    cat = codex_models.read_model_catalog()
    assert cat.source == "none"
    assert cat.unavailable_reason
    assert cat.models == []


# --- Reasoning-effort discovery (#309) ---------------------------------------------
def test_reasoning_effort_fields_parsed_from_cache(tmp_path, monkeypatch):
    # The real 0.144 cache shape: supported_reasoning_levels is a list of OBJECTS
    # {effort, description, ...}; only the effort tokens are surfaced (advisory).
    _write_cache(
        tmp_path,
        {
            "models": [
                {
                    "slug": "gpt-5.6-sol",
                    "default_reasoning_level": "medium",
                    "supported_reasoning_levels": [
                        {"effort": "low", "description": "Fast responses"},
                        {"effort": "medium", "description": "Balanced"},
                        {"effort": "max", "description": "Maximum depth"},
                        {"effort": "ultra", "description": "With delegation"},
                    ],
                },
            ]
        },
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    cat = codex_models.read_model_catalog()
    m = cat.models[0]
    assert m.default_reasoning_effort == "medium"
    assert m.supported_reasoning_efforts == ["low", "medium", "max", "ultra"]


def test_reasoning_effort_fields_none_when_absent(tmp_path, monkeypatch):
    _write_cache(tmp_path, {"models": [{"slug": "gpt-5.5"}]})
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    m = codex_models.read_model_catalog().models[0]
    assert m.default_reasoning_effort is None
    assert m.supported_reasoning_efforts is None


def test_reasoning_effort_junk_entries_dropped_and_deduped(tmp_path, monkeypatch):
    _write_cache(
        tmp_path,
        {
            "models": [
                {
                    "slug": "gpt-5.5",
                    "default_reasoning_level": "medium",
                    "supported_reasoning_levels": [
                        {"effort": "low"},
                        {"effort": "low"},  # duplicate: kept once, order preserved
                        {"effort": ""},  # empty: dropped
                        {"effort": "two words"},  # fails the token pattern: dropped
                        {"effort": 42},  # non-string: dropped
                        {"description": "no effort key"},  # missing effort: dropped
                        "nope",  # non-dict entry: dropped
                        {"effort": "high"},
                    ],
                },
            ]
        },
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    m = codex_models.read_model_catalog().models[0]
    assert m.supported_reasoning_efforts == ["low", "high"]


def test_reasoning_effort_all_junk_is_none_not_empty(tmp_path, monkeypatch):
    # A non-empty advertised list that yields nothing usable is unusable data (None),
    # distinct from an explicitly empty list ([]).
    _write_cache(
        tmp_path,
        {
            "models": [
                {"slug": "a-model", "supported_reasoning_levels": [{"effort": "bad token!"}]},
                {"slug": "b-model", "supported_reasoning_levels": []},
                {"slug": "c-model", "supported_reasoning_levels": "not-a-list"},
            ]
        },
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    models = {m.slug: m for m in codex_models.read_model_catalog().models}
    assert models["a-model"].supported_reasoning_efforts is None
    assert models["b-model"].supported_reasoning_efforts == []
    assert models["c-model"].supported_reasoning_efforts is None


def test_reasoning_effort_list_is_capped(tmp_path, monkeypatch):
    levels = [{"effort": f"e{i}"} for i in range(cli_contract.SUPPORTED_EFFORTS_MAX_ENTRIES + 10)]
    _write_cache(
        tmp_path,
        {"models": [{"slug": "gpt-5.5", "supported_reasoning_levels": levels}]},
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    m = codex_models.read_model_catalog().models[0]
    assert len(m.supported_reasoning_efforts) == cli_contract.SUPPORTED_EFFORTS_MAX_ENTRIES


def test_default_reasoning_effort_validated_independently(tmp_path, monkeypatch):
    # A junk default is dropped without touching the supported list, and a valid
    # default survives even when absent from (or lacking) an advertised list.
    _write_cache(
        tmp_path,
        {
            "models": [
                {
                    "slug": "a-model",
                    "default_reasoning_level": "bad default!",
                    "supported_reasoning_levels": [{"effort": "low"}],
                },
                {"slug": "b-model", "default_reasoning_level": "xhigh"},
            ]
        },
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    models = {m.slug: m for m in codex_models.read_model_catalog().models}
    assert models["a-model"].default_reasoning_effort is None
    assert models["a-model"].supported_reasoning_efforts == ["low"]
    assert models["b-model"].default_reasoning_effort == "xhigh"
    assert models["b-model"].supported_reasoning_efforts is None


def test_static_fallback_has_no_reasoning_effort_data(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    cat = codex_models.read_model_catalog()
    assert cat.source == "static"
    for m in cat.models:
        assert m.default_reasoning_effort is None
        assert m.supported_reasoning_efforts is None
