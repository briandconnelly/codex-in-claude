"""The codex CLI contract: drift/auth signatures and flag-class invariants."""

from __future__ import annotations

import pytest

from codex_in_claude import cli_contract


def test_always_send_and_help_gated_are_disjoint():
    assert cli_contract.ALWAYS_SEND_FLAGS.isdisjoint(cli_contract.HELP_GATED_FLAGS)


def test_remote_plugin_disable_is_guarantee_bearing():
    # #287: the connector-disable flag is ALWAYS_SEND (fail-loud on drift), and the
    # feature name it targets is a stable constant referenced across the codebase.
    assert cli_contract.DISABLE_FEATURE_FLAG == "--disable"
    assert cli_contract.REMOTE_PLUGIN_FEATURE == "remote_plugin"
    assert cli_contract.DISABLE_FEATURE_FLAG in cli_contract.ALWAYS_SEND_FLAGS


def test_core_sandbox_values():
    assert cli_contract.SANDBOX_READ_ONLY in cli_contract.VALID_SANDBOXES
    assert cli_contract.SANDBOX_WORKSPACE_WRITE in cli_contract.VALID_SANDBOXES
    assert cli_contract.SANDBOX_DANGER_FULL in cli_contract.VALID_SANDBOXES


@pytest.mark.parametrize(
    "text",
    [
        "error: unexpected argument '--nope' found",
        "error: invalid value 'wat' for '--sandbox'",
        "unrecognized subcommand 'frobnicate'",
        "no such subcommand",
        # #287: a renamed/removed feature name behind `--disable <FEATURE>` — the exact wording
        # codex 0.144.1 prints — keeps the remote_plugin guarantee fail-closed as drift.
        "Error: Unknown feature flag: remote_plugin",
    ],
)
def test_is_contract_drift_true(text):
    assert cli_contract.is_contract_drift(text)


def test_is_contract_drift_false_for_normal_output():
    assert not cli_contract.is_contract_drift("done", "applied patch", None)


@pytest.mark.parametrize(
    "text",
    ["Not logged in", "please run `codex login`", "401 Unauthorized", "not authenticated"],
)
def test_is_auth_failure_true(text):
    assert cli_contract.is_auth_failure(text)


def test_is_auth_failure_false():
    assert not cli_contract.is_auth_failure("wrote 3 files", None)


@pytest.mark.parametrize(
    "text",
    [
        "Error: 429 Too Many Requests",
        "you have hit your usage limit",
        "rate limit exceeded",
        "quota exceeded for this account",
        "Retry-After: 30",
    ],
)
def test_is_rate_limited_true(text):
    assert cli_contract.is_rate_limited(text)


@pytest.mark.parametrize(
    "text",
    [
        "wrote 3 files",
        "see file429.py for the handler",  # 429 without word boundaries
        "error code 4290 from the linter",  # 4290 is not a bare 429
    ],
)
def test_is_rate_limited_false(text):
    assert not cli_contract.is_rate_limited(text, None)


@pytest.mark.parametrize(
    ("text", "expected_ms"),
    [
        ("Retry-After: 30", 30_000),
        ("retry after 5s", 5_000),
        ("please try again in 12 seconds", 12_000),
        ("429 too many requests", None),  # no parseable delay
        ("retry after 5 minutes", None),  # non-second unit: don't misread as seconds
        ("retry after a 5-minute cooldown", None),  # hyphenated non-second unit
        ("try again in 2-hour window", None),  # hyphenated non-second unit
        ("Retry-After: Wed, 18 Jun 2026 12:00:00 GMT", None),  # HTTP-date, not seconds
    ],
)
def test_parse_retry_after_ms(text, expected_ms):
    assert cli_contract.parse_retry_after_ms(text) == expected_ms


def test_known_model_slugs_match_slug_pattern():
    assert cli_contract.KNOWN_MODEL_SLUGS  # non-empty bundled fallback
    for slug in cli_contract.KNOWN_MODEL_SLUGS:
        assert cli_contract.MODEL_SLUG_PATTERN.match(slug), slug


def test_models_cache_filename_is_a_bare_name():
    # Joined under $CODEX_HOME — must never be absolute or contain a path separator.
    assert cli_contract.MODELS_CACHE_FILENAME == "models_cache.json"
    assert "/" not in cli_contract.MODELS_CACHE_FILENAME


def test_model_slug_pattern_rejects_junk():
    assert cli_contract.MODEL_SLUG_PATTERN.match("gpt-5.5")
    assert not cli_contract.MODEL_SLUG_PATTERN.match("bad slug!")
    assert not cli_contract.MODEL_SLUG_PATTERN.match("")


# --- Reasoning-effort config override (#309) --------------------------------------
# The real backend rejection captured from codex-cli 0.144.3 on 2026-07-13
# (`-c model_reasoning_effort=totally-bogus-effort` on a valid model):
_REAL_EFFORT_REJECTION = (
    '{"type": "error", "error": {"type": "invalid_request_error", "message": '
    '"[ReasoningEffortParam] [reasoning.effort] [invalid_enum_value] Invalid value: '
    "'totally-bogus-effort'. Supported values are: 'none', 'minimal', 'low', "
    "'medium', 'high', and 'xhigh'.\"}, \"status\": 400}"
)


def test_reasoning_effort_config_key():
    assert cli_contract.MODEL_REASONING_EFFORT_CONFIG_KEY == "model_reasoning_effort"


def test_effort_rejection_markers_never_include_the_config_key():
    # A future codex that rejects the config key ITSELF is contract drift and must
    # stay fail-loud; only the backend's request-level markers identify a bad VALUE.
    for marker in cli_contract.REASONING_EFFORT_REJECTION_MARKERS:
        assert "model_reasoning_effort" not in marker


@pytest.mark.parametrize(
    "text",
    [
        _REAL_EFFORT_REJECTION,
        "[reasoning.effort] [invalid_enum_value] Invalid value: 'wat'",
        "ReasoningEffortParam rejected the request",
    ],
)
def test_is_reasoning_effort_rejection_true(text):
    assert cli_contract.is_reasoning_effort_rejection(text)


@pytest.mark.parametrize(
    "text",
    [
        # The config key alone (a CLI-side key rejection) is drift, not a bad value.
        "error: invalid value 'wat' for 'model_reasoning_effort'",
        "error: unexpected argument '-c' found",
        "reasoning effort was fine",  # no dotted/param marker
        "done",
        "",
    ],
)
def test_is_reasoning_effort_rejection_false(text):
    assert not cli_contract.is_reasoning_effort_rejection(text, None)


def test_reasoning_effort_backend_rejection_also_matches_drift_patterns():
    # Pins WHY classify_failure must check the effort rejection before falling back
    # to cli_contract_changed: the backend message contains "Invalid value", which
    # the drift patterns match.
    assert cli_contract.is_contract_drift(_REAL_EFFORT_REJECTION)


def test_reasoning_effort_token_pattern():
    for token in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
        assert cli_contract.REASONING_EFFORT_TOKEN_PATTERN.match(token), token
    for junk in ("", " ", "two words", "a" * 33, "-leading", "tab\there"):
        assert not cli_contract.REASONING_EFFORT_TOKEN_PATTERN.match(junk), junk


def test_supported_efforts_cap_is_positive():
    assert cli_contract.SUPPORTED_EFFORTS_MAX_ENTRIES > 0
