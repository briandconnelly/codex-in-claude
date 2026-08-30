"""Config defaults, clamps, env handling, and flag mappings."""

from __future__ import annotations

import dataclasses
import re
import unicodedata

import pytest

from codex_in_claude import config


def test_job_store_configures_worktree_cleanup(clean_env):
    import tempfile
    from pathlib import Path

    store = config.job_store()
    # The store may clean up only the throwaway-worktree temp area — THIS bridge's
    # pinned prefix, not the library default.
    assert store.cleanup_root == Path(tempfile.gettempdir())
    assert store.cleanup_prefix == config.WORKTREE_CONFIG.prefix == "cic-worktree-"


def test_worktree_config_pins_every_externally_visible_knob():
    # AGENTS.md: this bridge pins its worktree knobs explicitly rather than
    # inheriting pontonier defaults, so changing one is a behavior change, not a
    # refactor. The prefix leaks into temp paths a user sees and into job-store
    # cleanup; the identity authors the baseline commit every delegate diff is
    # taken against. Literal values on purpose — reading them back off
    # WORKTREE_CONFIG would make this test agree with any edit.
    assert config.WORKTREE_CONFIG.prefix == "cic-worktree-"
    assert config.WORKTREE_CONFIG.identity_name == "codex-in-claude"
    assert config.WORKTREE_CONFIG.identity_email == "codex-in-claude@local"
    # `extra_excludes` is the one knob deliberately left at the library default.
    assert config.WORKTREE_CONFIG.extra_excludes == ()
    # Completeness: a knob added upstream must be answered here — pinned or
    # consciously defaulted — rather than silently inheriting whatever pontonier
    # chooses. A new field trips this until someone decides.
    assert {f.name for f in dataclasses.fields(config.WORKTREE_CONFIG)} == {
        "prefix",
        "identity_name",
        "identity_email",
        "extra_excludes",
    }


def test_defaults_builtin(clean_env):
    d = config.defaults()
    assert d.tier == "consult"
    assert d.sandbox == "read-only"
    assert d.isolation == "inherit"
    assert d.model is None
    assert d.reasoning_effort is None
    assert d.timeout_seconds == config.DEFAULT_TIMEOUT_SECONDS


def test_default_timeout_seconds_is_300(clean_env):
    # #341: the built-in sync deadline was raised from 180 to 300 to recover the
    # observed mid-tier consult/review runs (246s/267s) that the old cap SIGKILLed.
    # A literal guard so the value cannot drift silently; the clamp bounds are unchanged.
    assert config.DEFAULT_TIMEOUT_SECONDS == 300
    assert (config.MIN_TIMEOUT_SECONDS, config.MAX_TIMEOUT_SECONDS) == (10, 600)


def test_defaults_env_overrides(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_TIER_DEFAULT", "propose")
    clean_env.setenv("CODEX_IN_CLAUDE_MODEL", "gpt-5.4")
    clean_env.setenv("CODEX_IN_CLAUDE_REASONING_EFFORT", "high")
    clean_env.setenv("CODEX_IN_CLAUDE_TIMEOUT_SECONDS", "42")
    d = config.defaults()
    assert d.tier == "propose"
    assert d.sandbox == "workspace-write"  # tier default
    assert d.model == "gpt-5.4"
    assert d.reasoning_effort == "high"
    assert d.timeout_seconds == 42


def test_blank_reasoning_effort_env_is_unset(clean_env):
    # Same convention as CODEX_IN_CLAUDE_MODEL: a blank env value means "not set".
    clean_env.setenv("CODEX_IN_CLAUDE_REASONING_EFFORT", "")
    assert config.defaults().reasoning_effort is None


def test_invalid_tier_falls_back(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_TIER_DEFAULT", "nonsense")
    assert config.defaults().tier == "consult"


def test_sandbox_default_override_validated(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_SANDBOX_DEFAULT", "bogus")
    # invalid override -> falls back to the tier's sandbox
    assert config.defaults().sandbox == "read-only"


def test_clamp_timeout():
    assert config.clamp_timeout(1) == config.MIN_TIMEOUT_SECONDS
    assert config.clamp_timeout(99999) == config.MAX_TIMEOUT_SECONDS
    assert config.clamp_timeout(120) == 120


@pytest.mark.parametrize(
    "iso,expected",
    [
        ("inherit", []),
        ("ignore-config", ["--ignore-user-config"]),
        ("ignore-rules", ["--ignore-user-config", "--ignore-rules"]),
    ],
)
def test_isolation_flags(iso, expected):
    assert config.isolation_flags(iso) == expected


def test_isolation_flags_invalid():
    with pytest.raises(ValueError, match="unsupported isolation"):
        config.isolation_flags("nope")


def test_sandbox_for_tier():
    assert config.sandbox_for_tier("consult") == "read-only"
    assert config.sandbox_for_tier("propose") == "workspace-write"
    assert config.sandbox_for_tier("apply") == "workspace-write"


@pytest.mark.parametrize(
    "value,expected",
    [("${FOO}", True), ("${FOO_BAR2}", True), ("plain", False), ("${}", False), (None, False)],
)
def test_is_env_placeholder(value, expected):
    assert config.is_env_placeholder(value) is expected


def test_placeholder_env_vars(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_MODEL", "${MODEL}")
    clean_env.setenv("CODEX_IN_CLAUDE_TIMEOUT_SECONDS", "60")
    assert config.placeholder_env_vars() == ["CODEX_IN_CLAUDE_MODEL"]


@pytest.mark.parametrize(
    "version,expected",
    [("codex-cli 0.151.0", True), ("codex-cli 0.999.0", False), ("garbage", None), (None, None)],
)
def test_version_supported(version, expected, clean_env):
    assert config.version_supported(version) is expected


def test_supported_versions_env_override(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_SUPPORTED_VERSIONS", "0.999")
    assert config.version_supported("codex-cli 0.999.3") is True
    assert config.version_supported("codex-cli 0.151.0") is False


def test_supported_versions_bad_env_falls_back(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_SUPPORTED_VERSIONS", "garbage")
    assert config.version_supported("codex-cli 0.151.0") is True


def test_state_dir_default(clean_env, monkeypatch):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    p = config.state_dir()
    assert p.name == "jobs"
    assert "codex-in-claude" in str(p)


def test_state_dir_override(clean_env, tmp_path):
    clean_env.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "jobs"))
    assert config.state_dir() == tmp_path / "jobs"


def test_max_input_bytes_floor(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_MAX_INPUT_BYTES", "5")
    assert config.max_input_bytes() == 1_000


def test_max_delegate_diff_bytes_default(clean_env):
    assert config.max_delegate_diff_bytes() == config.DEFAULT_MAX_DELEGATE_DIFF_BYTES


def test_max_delegate_diff_bytes_override(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_MAX_DELEGATE_DIFF_BYTES", "12345")
    assert config.max_delegate_diff_bytes() == 12345


def test_max_delegate_diff_bytes_invalid_falls_back(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_MAX_DELEGATE_DIFF_BYTES", "notanint")
    assert config.max_delegate_diff_bytes() == config.DEFAULT_MAX_DELEGATE_DIFF_BYTES


def test_max_delegate_diff_bytes_floor(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_MAX_DELEGATE_DIFF_BYTES", "5")
    assert config.max_delegate_diff_bytes() == 1_000


def test_job_defaults(clean_env):
    assert config.job_ttl_seconds() == config.DEFAULT_JOB_TTL_SECONDS
    assert config.job_max_seconds() == config.DEFAULT_JOB_MAX_SECONDS
    assert config.job_max_count() == config.DEFAULT_JOB_MAX_COUNT


def test_job_knobs_clamp_low(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_JOB_TTL", "10")
    clean_env.setenv("CODEX_IN_CLAUDE_JOB_MAX_SECONDS", "5")
    clean_env.setenv("CODEX_IN_CLAUDE_JOB_MAX_COUNT", "0")
    assert config.job_ttl_seconds() == 60
    assert config.job_max_seconds() == 60
    assert config.job_max_count() == 1


def test_job_knobs_clamp_high(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_JOB_MAX_SECONDS", "999999")
    clean_env.setenv("CODEX_IN_CLAUDE_JOB_MAX_COUNT", "999999")
    assert config.job_max_seconds() == 7_200
    assert config.job_max_count() == 1_000


def test_job_knobs_env_override(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_JOB_TTL", "3600")
    clean_env.setenv("CODEX_IN_CLAUDE_JOB_MAX_SECONDS", "600")
    clean_env.setenv("CODEX_IN_CLAUDE_JOB_MAX_COUNT", "10")
    assert config.job_ttl_seconds() == 3600
    assert config.job_max_seconds() == 600
    assert config.job_max_count() == 10


def test_max_output_bytes_default(monkeypatch):
    monkeypatch.delenv("CODEX_IN_CLAUDE_MAX_OUTPUT_BYTES", raising=False)
    assert config.max_output_bytes() == 10 * 1024 * 1024


def test_max_output_bytes_env_override(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_OUTPUT_BYTES", "500000")
    assert config.max_output_bytes() == 500_000


def test_max_output_bytes_floor(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_OUTPUT_BYTES", "10")
    assert config.max_output_bytes() == 64 * 1024


def test_max_output_bytes_bad_value(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_OUTPUT_BYTES", "notanint")
    assert config.max_output_bytes() == 10 * 1024 * 1024


# --- CODEX_IN_CLAUDE_EXTRA_ARGS passthrough (#231) --------------------------------


def test_extra_args_unset_is_empty_and_valid(clean_env):
    ea = config.extra_args()
    assert ea.tokens == ()
    assert ea.descriptors == ()
    assert ea.option_count == 0
    assert ea.configured is False
    assert ea.valid is True
    assert ea.error is None


def test_extra_args_blank_is_unconfigured(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "   ")
    ea = config.extra_args()
    assert ea.configured is False
    assert ea.tokens == ()


def test_extra_args_config_and_profile(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "-c model_provider=litellm --profile work")
    ea = config.extra_args()
    assert ea.valid is True
    assert ea.tokens == ("-c", "model_provider=litellm", "--profile", "work")
    assert ea.option_count == 2
    # descriptors are the safe config flag+KEY + profile flag/name; never the -c value.
    assert "model_provider" in ea.descriptors
    assert "-c" in ea.descriptors  # the flag itself, so a flag-token drift is attributable
    assert "--profile" in ea.descriptors
    assert "work" in ea.descriptors
    assert "litellm" not in ea.descriptors


def test_extra_args_attached_long_forms(monkeypatch):
    monkeypatch.setenv(
        "CODEX_IN_CLAUDE_EXTRA_ARGS", "--config=model_provider=x --enable=foo --disable=bar"
    )
    ea = config.extra_args()
    assert ea.valid is True
    assert ea.tokens == (
        "--config",
        "model_provider=x",
        "--enable",
        "foo",
        "--disable",
        "bar",
    )
    assert ea.option_count == 3


def test_extra_args_short_profile(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "-p work")
    ea = config.extra_args()
    assert ea.valid is True
    assert ea.tokens == ("-p", "work")


def test_extra_args_unbalanced_quotes_invalid(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", '-c "model_provider=x')
    ea = config.extra_args()
    assert ea.configured is True
    assert ea.valid is False
    assert ea.tokens == ()
    assert "tokenize" in ea.error


def test_extra_args_rejects_unknown_flag(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "--json")
    ea = config.extra_args()
    assert ea.valid is False
    assert "unsupported" in ea.error


def test_extra_args_rejects_bare_positional(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "some-prompt-text")
    ea = config.extra_args()
    assert ea.valid is False


def test_extra_args_rejects_missing_value(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "-c")
    ea = config.extra_args()
    assert ea.valid is False
    assert "requires a value" in ea.error


def test_extra_args_rejects_config_without_equals(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "-c model_provider")
    ea = config.extra_args()
    assert ea.valid is False
    assert "KEY=VALUE" in ea.error


def test_extra_args_rejects_value_that_looks_like_flag(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "--profile --sandbox")
    ea = config.extra_args()
    assert ea.valid is False
    assert "looks like a flag" in ea.error


def test_extra_args_rejects_attached_short_form(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "-cmodel_provider=x")
    ea = config.extra_args()
    assert ea.valid is False


def test_extra_args_denies_sandbox_key(monkeypatch):
    monkeypatch.setenv(
        "CODEX_IN_CLAUDE_EXTRA_ARGS",
        "-c sandbox_workspace_write.network_access=true",
    )
    ea = config.extra_args()
    assert ea.valid is False
    assert "refused" in ea.error


def test_extra_args_denies_approval_policy_key(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "-c approval_policy=never")
    ea = config.extra_args()
    assert ea.valid is False


def test_extra_args_error_never_echoes_secret_value(monkeypatch):
    # An invalid trailing token must not leak a preceding secret -c value.
    monkeypatch.setenv(
        "CODEX_IN_CLAUDE_EXTRA_ARGS",
        "-c model_providers.x.api_key=sk-supersecretvalue --bogus",
    )
    ea = config.extra_args()
    assert ea.valid is False
    assert "sk-supersecretvalue" not in (ea.error or "")


def test_extra_args_denies_sandbox_key_with_leading_whitespace(monkeypatch):
    # Regression (#231 review): codex trims the -c key, so a leading space must not
    # let a security-sensitive key slip past the denylist.
    monkeypatch.setenv(
        "CODEX_IN_CLAUDE_EXTRA_ARGS",
        '-c " sandbox_workspace_write.network_access=true"',
    )
    ea = config.extra_args()
    assert ea.valid is False
    assert "refused" in ea.error


def test_extra_args_denies_sandbox_key_with_space_around_dot(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", '-c "sandbox_mode =danger-full-access"')
    ea = config.extra_args()
    assert ea.valid is False


def test_extra_args_denies_shell_environment_policy_key(monkeypatch):
    # host-env exfil vector: exposing the server env to commands codex runs.
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "-c shell_environment_policy.inherit=all")
    ea = config.extra_args()
    assert ea.valid is False


# --- #312: quoted-root spellings may not pass the security root denylist --------------
@pytest.mark.parametrize(
    "raw",
    [
        # Quoted TOML root segments that SURVIVE shlex (single-quoted whole value preserves
        # the inner double-quotes). In codex these are junk keys its config never reads
        # (its -c parser is literal — no quote stripping), so they were silently-accepted
        # no-ops, not a sandbox bypass; refusing them anyway gives the operator feedback
        # and keeps the root denial as conservative as the exact-key denials (#287).
        "-c '\"sandbox_workspace_write\".network_access=true'",
        "-c '\"shell_environment_policy\".inherit=all'",
        "-c '\"approval_policy\"=never'",
        "-c '\"sandbox_mode\"=danger-full-access'",
        "-c '\" Sandbox_Workspace_Write \".network_access=true'",  # quotes + whitespace + case
    ],
)
def test_extra_args_denies_quoted_root_spellings(monkeypatch, raw):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", raw)
    ea = config.extra_args()
    assert ea.valid is False
    assert "refused" in ea.error


def test_extra_args_allows_quoted_root_of_permitted_key(monkeypatch):
    # The quoted-root canonicalization must not widen the denylist: a quoted root that
    # normalizes to a PERMITTED key stays allowed (codex will treat it as a junk key,
    # which is the operator's business — only guarantee-relevant roots are refused).
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "-c '\"model_provider\"=azure'")
    ea = config.extra_args()
    assert ea.valid is True


# --- #287: an operator may not re-enable the remote_plugin connectors -----------------
@pytest.mark.parametrize(
    "raw",
    [
        "--enable remote_plugin",  # feature spelling, spaced
        "--enable=remote_plugin",  # feature spelling, attached
        "--enable Remote_Plugin",  # case-insensitive
        "-c features.remote_plugin=true",  # config spelling (== --enable)
        "--config features.remote_plugin=true",  # long config flag
        "--config=features.remote_plugin=true",  # attached long config flag
        "-c features.remote_plugin=false",  # any assignment refused, not just =true
        '-c " features . Remote_Plugin =true"',  # whitespace/case around the dotted key
        "-c features={remote_plugin=true}",  # TOML inline table via the bare parent key
        '-c "features = {remote_plugin = true}"',  # inline table with whitespace
        # `--disable remote_plugin` is refused too — the feature is wholly plugin-owned, and
        # allowing the redundant flag would let a plugin-guarantee-flag drift be misattributed
        # to the operator's passthrough (#287 review).
        "--disable remote_plugin",
        # Quoted TOML key segments that SURVIVE shlex (single-quoted whole value preserves
        # the inner double-quotes). In codex these are junk keys, NOT aliases of
        # features.remote_plugin — its -c parser is literal (no quote stripping, verified
        # against config_override.rs at rust-v0.144.3) — so denying them is conservative
        # over-matching, not a mirror of codex's resolution (#312).
        "-c 'features.\"remote_plugin\"=true'",
        "-c '\"features\".remote_plugin=true'",
    ],
)
def test_extra_args_denies_remote_plugin_reenable(monkeypatch, raw):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", raw)
    ea = config.extra_args()
    assert ea.valid is False
    assert "remote_plugin" in (ea.error or "")


@pytest.mark.parametrize(
    "raw",
    [
        "--enable some_other_feature",  # a different feature is unaffected
        "--disable some_other_feature",  # disabling a different feature is fine
        "-c features.some_other=true",  # a different features.* key is unaffected
    ],
)
def test_extra_args_allows_non_plugin_owned_features(monkeypatch, raw):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", raw)
    ea = config.extra_args()
    assert ea.valid is True


# --- #310: `model` is reserved for the first-class, meta-reported controls ------------
@pytest.mark.parametrize(
    "raw",
    [
        "-c model=gpt-5-codex",  # short config flag
        "--config model=gpt-5-codex",  # long config flag
        "--config=model=gpt-5-codex",  # attached long config flag
        '-c " model =gpt-5-codex"',  # whitespace around the key (codex trims the whole key)
        # Lookalike spellings, conservatively refused: codex's -c parser is a naive
        # '.'-split with literal, case-sensitive segments (no quote stripping — verified
        # against codex-rs 0.144.3 config_override.rs), so `Model` and `"model"` would be
        # junk keys codex never reads, not aliases of `model`. Denying them anyway costs
        # nothing and matches the #287 Remote_Plugin/quoted-segment treatment.
        "-c Model=gpt-5-codex",
        "-c '\"model\"=gpt-5-codex'",  # escaped quotes survive shlex
    ],
)
def test_extra_args_reserves_model_key(monkeypatch, raw):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", raw)
    ea = config.extra_args()
    assert ea.valid is False
    # The refusal must point the operator at the first-class replacements.
    assert "CODEX_IN_CLAUDE_MODEL" in ea.error
    # The -c VALUE is never echoed in an error envelope.
    assert "gpt-5-codex" not in ea.error


def test_extra_args_model_denial_is_not_the_remote_plugin_message(monkeypatch):
    # The reserved-key refusal must carry its own explanation, not the
    # remote_plugin security-guarantee text (#287) or the sandbox-roots text.
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "-c model=gpt-5-codex")
    ea = config.extra_args()
    assert ea.valid is False
    assert "remote_plugin" not in ea.error
    assert "sandbox" not in ea.error


@pytest.mark.parametrize(
    "raw",
    [
        "-c model_provider=azure",  # the passthrough's motivating use case (#231)
        "-c model_providers.x.base_url=http://localhost:8000/v1",  # provider table
        "-c model_verbosity=low",  # any other model_* key stays allowed
    ],
)
def test_extra_args_allows_other_model_keys(monkeypatch, raw):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", raw)
    ea = config.extra_args()
    assert ea.valid is True


# --- #309: `model_reasoning_effort` joins `model` in the reserved set -----------------
@pytest.mark.parametrize(
    "raw",
    [
        "-c model_reasoning_effort=high",  # short config flag
        "--config model_reasoning_effort=high",  # long config flag
        "--config=model_reasoning_effort=high",  # attached long config flag
        '-c " model_reasoning_effort =high"',  # whitespace around the key
        # Lookalike spellings, conservatively refused — same #287/#310 treatment: codex's
        # -c parser is literal and case-sensitive, so these are junk keys codex never
        # reads, but denying them costs nothing and keeps the denylist unprobeable.
        "-c Model_Reasoning_Effort=high",
        "-c '\"model_reasoning_effort\"=high'",  # escaped quotes survive shlex
    ],
)
def test_extra_args_reserves_reasoning_effort_key(monkeypatch, raw):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", raw)
    ea = config.extra_args()
    assert ea.valid is False
    # The refusal must point the operator at the first-class replacements for THIS key.
    assert "CODEX_IN_CLAUDE_REASONING_EFFORT" in ea.error
    assert "reasoning_effort" in ea.error
    # The -c VALUE is never echoed in an error envelope.
    assert "=high" not in ea.error


def test_extra_args_model_denial_names_model_controls_not_effort(monkeypatch):
    # Each reserved key's refusal names its own first-class controls.
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "-c model=gpt-5-codex")
    ea = config.extra_args()
    assert ea.valid is False
    assert "CODEX_IN_CLAUDE_MODEL" in ea.error
    assert "CODEX_IN_CLAUDE_REASONING_EFFORT" not in ea.error


# --- Reasoning-effort shape bounds (#309, Codex re-review) -----------------------------
@pytest.mark.parametrize(
    "value",
    ["", "high", "x" * 128, "an effort with spaces", "Ünïcode-ok", "\xa0"],
)
def test_reasoning_effort_shape_accepts(value):
    assert config.reasoning_effort_shape_error(value) is None


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        ("x" * 129, "128"),  # over the max length
        ("with\x00nul", "control character"),
        ("with\x07bell", "control character"),
        ("high\n", "control character"),  # trailing newline is NOT admitted here
        ("\x7f", "control character"),  # DEL
        ("high\x80", "control character"),  # C1 lower bound
        ("high\x85", "control character"),  # NEL — a C1 control (category Cc)
        ("high\x9b", "control character"),  # CSI — C1 upper bound
        ("high\ud800", "surrogate"),  # lone high surrogate — hostile to UTF-8/JSON
        ("\udfff", "surrogate"),  # surrogate range upper bound
    ],
)
def test_reasoning_effort_shape_rejects(value, fragment):
    reason = config.reasoning_effort_shape_error(value)
    assert reason is not None
    assert fragment in reason
    # The reason is value-free (safe for an error message).
    assert value not in reason


def test_reasoning_effort_shape_rejects_every_unicode_cc_control():
    # Maintainer-review regression (#313): the documented contract is "no control
    # characters", which is Unicode category Cc — C0, DEL, AND the C1 block
    # (U+0080-U+009F, e.g. NEL/CSI). Both the character-wise predicate and the
    # advertised JSON-Schema pattern must reject every one of them; the first
    # non-control neighbours (space, U+00A0) must pass both.
    cc = [chr(cp) for cp in range(0x100) if unicodedata.category(chr(cp)) == "Cc"]
    assert len(cc) == 65  # C0 (32) + DEL (1) + C1 (32); Cc has no members past U+00FF
    for ch in cc:
        assert config.reasoning_effort_shape_error(ch) == "contains a control character"
        assert re.fullmatch(config.REASONING_EFFORT_VALUE_PATTERN, ch) is None
    for ch in (" ", "\xa0"):
        assert config.reasoning_effort_shape_error(ch) is None
        assert re.fullmatch(config.REASONING_EFFORT_VALUE_PATTERN, ch)


def test_reasoning_effort_shape_rejects_every_surrogate():
    # Maintainer-review regression (#313): surrogate code points (category Cs,
    # U+D800-U+DFFF) are outside Cc but hostile to argv encoding and JSON
    # serialization — an unpaired one raises UnicodeEncodeError before Codex spawns
    # and breaks envelope serialization. The character-wise predicate rejects the
    # whole range; the neighbours just outside it must pass. (The advertised
    # JSON-Schema pattern deliberately does NOT name the range: under a non-`u`-flag
    # ECMA engine a surrogate class also matches the code UNITS of astral characters,
    # which are legitimate values — see the comment on REASONING_EFFORT_VALUE_PATTERN.)
    for cp in (0xD800, 0xDBFF, 0xDC00, 0xDFFF):
        assert config.reasoning_effort_shape_error(chr(cp)) == "contains a surrogate code point"
    for cp in (0xD7FF, 0xE000, 0x1F600):  # range neighbours + an astral character
        assert config.reasoning_effort_shape_error(chr(cp)) is None


# --- extra-args ownership helpers (#524) -------------------------------------------
def test_extra_args_records_config_keys_and_profile_names(monkeypatch):
    monkeypatch.setenv(
        config.EXTRA_ARGS_ENV, "-c model_provider=x --profile myprof --config other.key=1"
    )
    ea = config.extra_args()
    assert ea.valid
    assert ea.config_keys == ("model_provider", "other.key")
    assert ea.profile_names == ("myprof",)


def test_extra_args_owns_config_key_matches_only_its_own_keys(monkeypatch):
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, "-c model_provider=x")
    ea = config.extra_args()
    assert ea.owns_config_key("model_provider") is True
    # Case/quote/space variants normalize to the same key (the same conservative
    # over-matching the denylist uses).
    assert ea.owns_config_key(' "Model_Provider" ') is True
    # A plugin-owned pin is NOT the operator's, even though both ride `-c`.
    assert ea.owns_config_key("sandbox_workspace_write.network_access") is False
    assert ea.owns_config_key("model_provider_other") is False


def test_extra_args_owns_the_dotted_children_of_its_own_keys(monkeypatch):
    """A `-c t={k=v}` parent-table assignment is echoed by codex as the dotted CHILD
    path (`t.k`, probed on 0.149.1 for #550), so ownership must cover descendants of a
    configured key — but only whole dotted segments, never a name-prefix."""
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, "-c model_providers.acme={base_url=3}")
    ea = config.extra_args()
    assert ea.owns_config_key("model_providers.acme") is True
    assert ea.owns_config_key("model_providers.acme.base_url") is True
    assert ea.owns_config_key("model_providers.acme.wire.api") is True
    # Same segments, different case/quoting still match; a sibling or a name-prefix does not.
    assert ea.owns_config_key(' "Model_Providers".ACME.base_url ') is True
    assert ea.owns_config_key("model_providers.acmeX.base_url") is False
    assert ea.owns_config_key("model_providers") is False


def test_extra_args_ownership_is_false_when_unconfigured_or_invalid(monkeypatch):
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    ea = config.extra_args()
    assert ea.owns_config_key("model_provider") is False
    assert ea.owns_profile_file("/home/u/.codex/myprof.config.toml") is False
    # An invalid knob must not lend ownership either (its parse produced no keys).
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, "--bogus-flag v")
    bad = config.extra_args()
    assert bad.valid is False
    assert bad.owns_config_key("model_provider") is False


def test_extra_args_owns_profile_file_by_codex_naming_convention(monkeypatch):
    # Verified live on codex-cli 0.149.1: `--profile NAME` loads (and, under
    # --strict-config, validates) $CODEX_HOME/NAME.config.toml, while an UNSELECTED
    # NAME.config.toml is never read.
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, "--profile myprof")
    ea = config.extra_args()
    assert ea.owns_profile_file("/home/u/.codex/myprof.config.toml") is True
    assert ea.owns_profile_file("/home/u/.codex/config.toml") is False
    assert ea.owns_profile_file("/home/u/.codex/otherprof.config.toml") is False
    assert ea.owns_profile_file(None) is False


# --- #528: operator-supplied text is echoed through the shared echo sanitizer ---------


def _has_control(text: str) -> bool:
    return any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in text)


@pytest.mark.parametrize(
    ("raw", "expected_fragment"),
    [
        # The denied-ROOT branch matches on a PREFIX, so the rest of the key is arbitrary
        # and is echoed — one case per denied root.
        ("-c sandbox_\x07mode=x", "refused"),
        ("-c shell_environment_policy_\x1b[31mx=1", "refused"),
        ("-c approval_policy_\x07x=1", "refused"),
        # The unsupported-argument branch echoes an arbitrary token. It was the ONE path
        # `_safe_token` already covered — and `_safe_token` redacted and bounded without
        # stripping, so it leaked too (#528).
        ("--bogus\x07flag v", "unsupported argument"),
    ],
)
def test_extra_args_refusals_never_echo_a_control_character(raw, expected_fragment, monkeypatch):
    """Refusal messages interpolate the operator's own key or token, and reached the
    envelope raw.

    Only these branches can carry a control character at all: every other refusal either
    interpolates an allowlisted flag name, or fires on an EXACT match (`features`,
    `model_reasoning_effort`, `remote_plugin`) that a control character would break — such
    a key is junk codex's own config never reads, not an alias, so it is accepted as an
    ordinary passthrough key and echoed only as a descriptor (covered in test_codex.py).
    """
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, raw)
    ea = config.extra_args()
    # Non-vacuity: this input must actually take the refusal branch under test, or the
    # assertion below would hold for an input that never built a message at all.
    assert ea.error is not None, raw
    assert expected_fragment in ea.error
    assert not _has_control(ea.error), repr(ea.error)


@pytest.mark.parametrize("attack", ["\x1b[31m", "\x07", "\x7f", "\x85"])
def test_descriptors_keep_their_raw_identity_for_matching(attack, monkeypatch):
    """Descriptors are an IDENTITY, not display text, so they are deliberately NOT
    sanitized here.

    They are matched against codex's own rejection text, which quotes the operator's name
    with its RAW spelling. Stripping or bounding them at construction changes what they
    match: a control-bearing name (or one past a length bound) stops matching, and the
    operator's own bad passthrough is misattributed to plugin contract drift — the
    fail-loud path, with the wrong repair guidance. Sanitation belongs at emission, which
    `test_extra_args_rejected_strips_control_characters_from_descriptors` covers (#528).
    """
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, f"--profile ev{attack}il")
    ea = config.extra_args()
    assert ea.valid, ea.error
    assert f"ev{attack}il" in ea.descriptors


# --- #555: instruction-bearing config keys are refused in the passthrough -------------
@pytest.mark.parametrize(
    "raw",
    [
        "-c developer_instructions=BE_AGREEABLE",  # short flag (lands as the first developer turn)
        "--config developer_instructions=BE_AGREEABLE",  # long config flag
        "--config=developer_instructions=BE_AGREEABLE",  # attached long config flag
        '-c " developer_instructions =BE_AGREEABLE"',  # whitespace around the key
        "-c Developer_Instructions=BE_AGREEABLE",  # case lookalike (#310 treatment)
        "-c '\"developer_instructions\"=BE_AGREEABLE'",  # escaped quotes survive shlex
        "-c model_instructions_file=/tmp/BE_AGREEABLE.md",  # replaces the built-in instructions
        "-c experimental_instructions_file=/tmp/BE_AGREEABLE.md",  # deprecated alias
        "-c instructions=BE_AGREEABLE",  # documented "reserved for future use" — fail closed
    ],
)
def test_extra_args_refuses_instruction_keys(monkeypatch, raw):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", raw)
    ea = config.extra_args()
    assert ea.valid is False
    # The refusal explains itself and points at the tracked first-class parameter.
    assert "#556" in ea.error
    assert "instructions" in ea.error
    # The -c VALUE is never echoed in an error envelope.
    assert "BE_AGREEABLE" not in ea.error


def test_extra_args_instruction_denial_is_its_own_message(monkeypatch):
    # Not the sandbox-root, remote_plugin, or meta-reserved text: no first-class control
    # exists yet, so the message must not send the operator to an env var or parameter.
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "-c developer_instructions=x")
    ea = config.extra_args()
    assert ea.valid is False
    assert "sandbox" not in ea.error
    assert "remote_plugin" not in ea.error
    assert "CODEX_IN_CLAUDE_" not in ea.error


@pytest.mark.parametrize(
    "raw",
    [
        "-c model_provider=azure",  # the passthrough's motivating use case (#231)
        "-c model_instructions=x",  # prefix of a denied key, not the key: exact-match only
        "-c developer_instructions_extra=x",  # suffix lookalike stays allowed
        "-c mcp_servers.x.instructions=y",  # a nested `instructions` segment is a different key
    ],
)
def test_extra_args_instruction_denial_is_exact(monkeypatch, raw):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", raw)
    assert config.extra_args().valid is True
