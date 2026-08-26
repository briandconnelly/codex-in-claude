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
        "[ReasoningEffortParam] [reasoning.effort] [invalid_enum_value] Invalid value: 'wat'",
        # The two bracketed fields may be split across streams.
        "[reasoningeffortparam] something\n[reasoning.effort] something",
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
        # Maintainer-review regression (#313): an operator passthrough naming a
        # marker (`--enable reasoning.effort`, a profile so named) must not
        # impersonate the backend's structured `[…] […]` rejection signature.
        "Unknown feature flag: reasoning.effort",
        "error: unknown profile 'reasoning.effort' found in config",
        "ReasoningEffortParam rejected the request",  # unbracketed marker prose
        "[reasoning.effort] [invalid_enum_value] Invalid value: 'wat'",  # one field only
        "[ReasoningEffortParam] rejected",  # one field only
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


def test_reasoning_effort_token_pattern_rejects_trailing_newline():
    # Codex-review regression (#309): `$` with re.match admits "high\n"; the pattern
    # must anchor with \Z so a malformed cache token cannot carry a control character.
    assert not cli_contract.REASONING_EFFORT_TOKEN_PATTERN.match("high\n")


# --- The AGENTS.md scope in the canonical egress disclosure (#472) -----------------
#
# The published caveat used to scope the auto-loaded `AGENTS.md` to the resolved
# workspace alone. Two probed behaviors are wider than that, and both are unsafe-direction
# omissions — a caller reads this sentence to decide what it is about to send to OpenAI:
#
#   1. Inside a repository the load walks UP from the resolved workspace to the repository
#      root, so narrowing `workspace_root` to a subdirectory to bound egress still ships
#      every ancestor `AGENTS.md` inside that repository. Outside a repository there is no
#      walk at all — only the workspace's own file loads.
#   2. A user-global guidance file in `$CODEX_HOME` (`AGENTS.override.md` if present, else
#      `AGENTS.md`) loads on EVERY call from ANY workspace — the `AGENTS.md` twin of the
#      `$CODEX_HOME/skills/` hole (#358). Neither `--ignore-user-config` nor
#      `project_doc_max_bytes=0` suppresses it, though the latter does suppress the
#      project/ancestor files.
#
# COMPATIBILITY.md § "Implicit Codex context" owns the probe and its evidence; these
# guards only hold the published sentence to what was probed.
_AGENTS_SCOPE_REQUIRED = (
    # the workspace's own file — the pre-#472 claim, still true and still required
    "resolved workspace",
    # the ancestor walk AND the bound that stops it
    "ancestor",
    "repository",
    # the user-global guidance file, named by the path a reader can act on
    "$CODEX_HOME/AGENTS",
)


def _states_the_agents_md_scope(text: str) -> bool:
    """Does this text state all three AGENTS.md sources the probe found?

    Word-presence, with the same acknowledged limit as the repo's other disclosure
    matchers: it catches OMISSION — dropping the ancestor walk or the global file —
    but not a confident misstatement, which is why every code carrier is ALSO pinned
    to the exact constant and why `test_agents_md_scope_makes_no_overclaim` pins the
    absence of the specific inversions this change could drift into.
    """
    return all(marker in text for marker in _AGENTS_SCOPE_REQUIRED)


def test_skills_discovery_fact_states_every_agents_md_source():
    assert _states_the_agents_md_scope(cli_contract.SKILLS_DISCOVERY_FACT)
    # …and the FULL/whole-disclosure composites inherit it rather than restating it.
    assert _states_the_agents_md_scope(cli_contract.SKILLS_DISCOVERY_FACT_FULL)
    assert _states_the_agents_md_scope(cli_contract.IMPLICIT_CONTEXT_DISCLOSURE)


def test_agents_md_scope_matcher_rejects_the_pre_472_wording():
    """Guard the guard: the verbatim pre-#472 sentence must FAIL this matcher.

    Without this control a matcher that accepted everything would hold the constant
    green over the exact understatement #472 exists to correct.
    """
    pre_472 = (
        "Codex auto-loads the resolved workspace's AGENTS.md and discovers skills in its "
        ".agents/skills/ and user-global $CODEX_HOME/skills/ (default ~/.codex/skills/), "
        "reachable from outside the workspace."
    )
    assert _states_the_agents_md_scope(pre_472) is False
    # Partial corrections must fail too: the ancestor walk without the global file…
    ancestors_only = (
        "Codex auto-loads the resolved workspace's AGENTS.md and, in a repository, "
        "ancestor AGENTS.md files through its root."
    )
    assert _states_the_agents_md_scope(ancestors_only) is False
    # …and the global file without the ancestor walk.
    global_only = (
        "Codex auto-loads the resolved workspace's AGENTS.md plus a user-global "
        "$CODEX_HOME/AGENTS.override.md or AGENTS.md."
    )
    assert _states_the_agents_md_scope(global_only) is False


def test_agents_md_scope_makes_no_overclaim():
    """The widened sentence must not claim MORE egress than the probe found.

    A disclosure that overstates is not free: it is the failure that made the original
    #472 evidence table wrong (a probe measured the model reading a file itself and
    recorded it as auto-loading, publishing an above-the-repository-root claim that had
    to be retracted). Two probed negatives bound the claim — nothing above the repository
    root loads, and outside a repository no ancestor loads at all — so the phrasings that
    would assert either must stay out of the constant.
    """
    for overclaim in (
        "above the repository root",
        "above the git root",
        "every filesystem ancestor",
        "any directory above",
    ):
        assert overclaim not in cli_contract.SKILLS_DISCOVERY_FACT, overclaim
    # The ancestor claim is CONDITIONED on being in a repository, so a non-repository
    # reader cannot infer a filesystem-wide walk. Pin the condition, not just the words.
    fact = cli_contract.SKILLS_DISCOVERY_FACT
    assert "in a repository, ancestor" in fact


# --- strict-config rejection recognizer (#524) ------------------------------------
# The two live stderr shapes, captured verbatim from codex-cli 0.148.0 (2026-08-20,
# scratch $CODEX_HOME, zero spend — config parsing precedes auth). Kept as literals so a
# recognizer rewritten to match a paraphrase of the grammar fails here.
_STRICT_OVERRIDE_STDERR = (
    "Error loading config.toml: unknown configuration field `bogus_key_xyz` "
    "in -c/--config override\n"
)
_STRICT_FILE_STDERR = (
    "Error loading config.toml:\n"
    "/home/u/.codex/config.toml:1:1: unknown configuration field `some_unknown_junk_key`\n"
    "  |\n"
    "1 | some_unknown_junk_key = true\n"
    "  | ^^^^^^^^^^^^^^^^^^^^^\n"
)


def test_strict_config_override_form_is_recognized():
    rej = cli_contract.parse_strict_config_rejection(_STRICT_OVERRIDE_STDERR)
    assert rej is not None
    assert rej.origin == "override"
    assert rej.key == "bogus_key_xyz"
    assert rej.source_path is None
    assert rej.line is None


def test_strict_config_file_form_is_recognized():
    rej = cli_contract.parse_strict_config_rejection(_STRICT_FILE_STDERR)
    assert rej is not None
    assert rej.origin == "file"
    assert rej.key == "some_unknown_junk_key"
    assert rej.source_path == "/home/u/.codex/config.toml"
    assert rej.line == 1


def test_strict_config_file_form_carries_the_dotted_profile_path():
    # An unknown key inside a [profiles.X] table is reported with its full dotted path
    # (verified live: an UNSELECTED profile table is validated too).
    stderr = (
        "Error loading config.toml:\n"
        "/home/u/.codex/config.toml:2:1: unknown configuration field "
        "`profiles.myprof.some_unknown_junk_key`\n"
    )
    rej = cli_contract.parse_strict_config_rejection(stderr)
    assert rej is not None
    assert rej.key == "profiles.myprof.some_unknown_junk_key"


def test_strict_config_recognizer_ignores_unrelated_text():
    for text in (
        None,
        "",
        "error: unexpected argument '--nope' found",
        "codex exited 1: something else entirely",
        # The phrase alone, without the anchored grammar, is not a strict rejection.
        "unknown configuration field somewhere in prose",
    ):
        assert cli_contract.parse_strict_config_rejection(text) is None


def test_strict_config_recognizer_requires_the_error_prefix():
    # A bare file-form line with no `Error loading config.toml` header is not the
    # strict grammar; requiring the header keeps a quoted echo from matching.
    orphan = "/home/u/.codex/config.toml:1:1: unknown configuration field `k`\n"
    assert cli_contract.parse_strict_config_rejection(orphan) is None


def test_strict_config_recognizer_survives_auth_markers_in_key_and_path():
    # The recognizer runs BEFORE the auth check precisely because codex echoes a foreign
    # key and path here, either of which can contain an AUTH_FAILURE_PATTERNS substring
    # ("401", "unauthorized"). It must still recognize the rejection, and the classifier
    # must not read those echoes as an auth failure.
    stderr = (
        "Error loading config.toml:\n"
        "/home/401/.codex/config.toml:3:1: unknown configuration field `unauthorized_key`\n"
    )
    rej = cli_contract.parse_strict_config_rejection(stderr)
    assert rej is not None
    assert rej.key == "unauthorized_key"
    assert rej.source_path == "/home/401/.codex/config.toml"
    # Positive control: those very echoes DO satisfy the auth matcher, so ordering — not
    # the matcher — is what protects this case.
    assert cli_contract.is_auth_failure(stderr) is True


def test_strict_config_recognizer_bounds_the_key_and_ignores_embedded_newline():
    # Defensive bounds on untrusted echoed text: an implausibly long key does not match
    # (drift, not a real key), and a backtick span never spills across lines.
    huge = (
        "Error loading config.toml: unknown configuration field "
        f"`{'k' * 5000}` in -c/--config override\n"
    )
    assert cli_contract.parse_strict_config_rejection(huge) is None


def test_strict_config_flag_is_guarantee_bearing():
    # #524: it converts a silent unknown-key drift on the plugin's guarantee-bearing `-c`
    # pins into a zero-spend startup failure, so it is ALWAYS_SEND (never help-gated).
    assert cli_contract.STRICT_CONFIG_FLAG == "--strict-config"
    assert cli_contract.STRICT_CONFIG_FLAG in cli_contract.ALWAYS_SEND_FLAGS
    assert cli_contract.STRICT_CONFIG_FLAG not in cli_contract.HELP_GATED_FLAGS


def test_plugin_owned_config_keys_are_exactly_the_pinned_three():
    # The set the override-form classifier matches against to prove a rejected key is
    # OURS. Literal values, so a typo'd constant fails here instead of silently
    # reclassifying a genuine drift as an operator problem.
    expected = {
        "model_reasoning_effort",
        "sandbox_workspace_write.network_access",
        "sandbox_workspace_write.writable_roots",
    }
    assert set(cli_contract.PLUGIN_OWNED_CONFIG_KEYS) == expected
    # Every one is a key the builder actually emits.
    keys = cli_contract.PLUGIN_OWNED_CONFIG_KEYS
    assert cli_contract.MODEL_REASONING_EFFORT_CONFIG_KEY in keys
    assert cli_contract.WORKSPACE_WRITE_NETWORK_ACCESS_CONFIG_KEY in keys
    assert cli_contract.WORKSPACE_WRITE_WRITABLE_ROOTS_CONFIG_KEY in keys


def test_strict_config_recognizer_tolerates_carriage_returns():
    """A CRLF-terminated rejection still parses (#524).

    The server is POSIX-first, but `CODEX_IN_CLAUDE_ALLOW_UNSUPPORTED_PLATFORM=1`
    documents a consult-only non-POSIX mode, and a stray `\\r` before the line end would
    otherwise silently defeat the anchor — degrading a pin drift from
    `cli_contract_changed` to a generic `nonzero_exit`. Tolerating it in the trailing run
    costs no anchoring strength: `\\r` is accepted only where a space or tab already is.
    """
    override = (
        "Error loading config.toml: unknown configuration field `k` in -c/--config override\r\n"
    )
    parsed = cli_contract.parse_strict_config_rejection(override)
    assert parsed is not None
    assert parsed.origin == "override"
    assert parsed.key == "k"
    in_file = (
        "Error loading config.toml:\r\n"
        "/home/u/.codex/config.toml:2:1: unknown configuration field `j`\r\n"
    )
    parsed_file = cli_contract.parse_strict_config_rejection(in_file)
    assert parsed_file is not None
    assert parsed_file.key == "j"
    assert parsed_file.source_path == "/home/u/.codex/config.toml"
    # Anchoring is unchanged: trailing junk still does not match.
    assert (
        cli_contract.parse_strict_config_rejection(
            "Error loading config.toml: unknown configuration field `k` "
            "in -c/--config override X\r\n"
        )
        is None
    )


# --- retired config SETTING rejection (codex 0.149, #542) -------------------------
# Captured verbatim from codex-cli 0.149.1 (2026-08-25, scratch $CODEX_HOME, zero spend
# — config parsing precedes auth). The SAME config was accepted by 0.148.0, so this is a
# real upgrade-time break for any user whose config still selects the retired policy.
# Kept as a literal so a recognizer rewritten to match a paraphrase fails here.
_RETIRED_SETTING_STDERR = (
    'Error: approval_policy = "untrusted" is no longer supported; remove this setting\n'
)


def test_retired_config_setting_is_recognized():
    rej = cli_contract.parse_unsupported_config_setting(_RETIRED_SETTING_STDERR)
    assert rej is not None
    assert rej.key == "approval_policy"
    assert rej.value == '"untrusted"'


def test_retired_config_setting_does_not_match_the_strict_config_grammar():
    # The two grammars are distinct: this key is RECOGNIZED (only its value is retired),
    # so the unknown-field parser must not claim it, and vice versa.
    assert cli_contract.parse_strict_config_rejection(_RETIRED_SETTING_STDERR) is None
    assert cli_contract.parse_unsupported_config_setting(_STRICT_OVERRIDE_STDERR) is None
    assert cli_contract.parse_unsupported_config_setting(_STRICT_FILE_STDERR) is None


def test_retired_config_setting_negative_controls():
    for text in (
        "",
        "some unrelated failure",
        # the phrase alone, with no `key = value` head, is not a rejection we can act on
        "this feature is no longer supported\n",
    ):
        assert cli_contract.parse_unsupported_config_setting(text) is None


def test_retired_config_setting_is_anchored_to_a_line_start():
    """Mid-line text must not satisfy the grammar.

    codex prints this rejection as its own line. Without the anchor, any prose that
    happened to quote the phrase — a diff, a commit message, a model's own answer echoed
    into stderr — could manufacture the classification."""
    mid = 'blah blah Error: k = "v" is no longer supported; remove this setting'
    assert cli_contract.parse_unsupported_config_setting(mid) is None
    # Positive control: the identical text on its own line IS recognized, so the negative
    # above is about the anchor and not about the rest of the pattern failing to match.
    own_line = 'preamble\nError: k = "v" is no longer supported; remove this setting'
    rej = cli_contract.parse_unsupported_config_setting(own_line)
    assert rej is not None and rej.key == "k"


def test_retired_config_setting_value_never_spans_lines():
    # The value group is bounded to a single line, so a multi-line span cannot be pulled
    # into the echoed value (which reaches an envelope).
    assert (
        cli_contract.parse_unsupported_config_setting(
            'Error: k = "a\nb" is no longer supported; remove this setting'
        )
        is None
    )


@pytest.mark.parametrize("field", ["key", "value"])
def test_retired_config_setting_fails_closed_on_an_over_cap_span(field):
    """An implausibly long key or value is drift or hostile text, not a real setting.

    The bound makes it return None, so the run keeps its ordinary `nonzero_exit`
    classification. That is the safe direction: a lost diagnosis, never a wrong one, and
    never an unbounded span echoed into an error envelope."""
    big = "x" * (cli_contract.STRICT_CONFIG_KEY_MAX_CHARS + 10)
    text = (
        f'Error: {big} = "v" is no longer supported; remove this setting'
        if field == "key"
        else f'Error: k = "{big}" is no longer supported; remove this setting'
    )
    assert cli_contract.parse_unsupported_config_setting(text) is None


@pytest.mark.parametrize("n", [1_000, 10_000, 100_000])
def test_retired_config_setting_pattern_has_no_catastrophic_backtracking(n):
    """The pattern runs in linear time on adversarial shapes built from its own slots.

    A stderr blob is untrusted text of substantial size, so a quadratic matcher here would
    be a denial of service on the failure path — the path a drifting codex takes."""
    import time

    for shape in (
        "Error: " + "a" * n,
        "Error: " + "a." * n,
        "Error: " + "k = v " * n + "is no longer supported; remove this setting",
    ):
        started = time.perf_counter()
        cli_contract.parse_unsupported_config_setting(shape)
        assert time.perf_counter() - started < 1.0


def test_retired_config_setting_requires_the_complete_observed_grammar():
    """The phrase alone is not the grammar — the full observed suffix is required.

    A non-greedy value group with no required suffix accepts a PREFIX: any line where the
    phrase appears inside an echoed value matches, with the value silently truncated at
    the phrase. That is not a cosmetic parse error. `parse_unsupported_config_setting`
    runs AHEAD of the auth/drift matchers precisely so an echoed key cannot fool them, so
    a prefix match steals the classification from the matcher that should have won."""
    stolen_from_auth = 'Error: token = "401 unauthorized is no longer supported" please login'
    # Positive control: this text really is an auth failure, so the assertion below is
    # about ordering being safe and not about an inert string.
    assert cli_contract.is_auth_failure(stolen_from_auth) is True
    assert cli_contract.parse_unsupported_config_setting(stolen_from_auth) is None

    for near_miss in (
        # phrase inside the value, no real suffix
        'Error: model_provider = "x is no longer supported" failed to initialize provider',
        # right shape, wrong/absent suffix
        'Error: k = "v" is no longer supported',
        'Error: k = "v" is no longer supported; do something else',
        'Error: k = "v" is no longer supported; remove this setting AND MORE',
    ):
        assert cli_contract.parse_unsupported_config_setting(near_miss) is None, near_miss

    # The real thing still parses — the anchor must not have broken the actual grammar.
    rej = cli_contract.parse_unsupported_config_setting(_RETIRED_SETTING_STDERR)
    assert rej is not None and rej.key == "approval_policy"
    assert rej.value == '"untrusted"'


# --- invalid config VALUE rejection (codex 0.149, #550) ----------------------------
# Captured verbatim from codex-cli 0.149.1 (2026-08-25, scratch $CODEX_HOME, zero spend —
# config parsing precedes auth). Two serde sub-grammars, both two-line with the key on the
# SECOND line, and both terminate stderr (a blank line follows). The identical text is
# printed whether the value came from config.toml or from a `-c` override. Kept as
# literals so a recognizer rewritten to match a paraphrase fails here.
_INVALID_VARIANT_STDERR = (
    "Error loading config.toml: unknown variant `bogus`, expected one of `untrusted`, "
    "`on-failure`, `on-request`, `granular`, `never`\n"
    "in `approval_policy`\n\n"
)
_INVALID_TYPE_STDERR = (
    'Error loading config.toml: invalid type: string "yes", expected a boolean\n'
    "in `sandbox_workspace_write.network_access`\n\n"
)
_INVALID_TYPE_INTEGER_STDERR = (
    "Error loading config.toml: invalid type: integer `3`, expected a sequence\n"
    "in `sandbox_workspace_write.writable_roots`\n\n"
)


def test_invalid_config_value_unknown_variant_is_recognized():
    rej = cli_contract.parse_invalid_config_value(_INVALID_VARIANT_STDERR)
    assert rej is not None
    assert rej.key == "approval_policy"
    assert rej.kind == "unknown_variant"
    assert rej.expected == "`untrusted`, `on-failure`, `on-request`, `granular`, `never`"


@pytest.mark.parametrize(
    ("text", "key", "expected"),
    [
        (_INVALID_TYPE_STDERR, "sandbox_workspace_write.network_access", "a boolean"),
        (_INVALID_TYPE_INTEGER_STDERR, "sandbox_workspace_write.writable_roots", "a sequence"),
    ],
)
def test_invalid_config_value_wrong_type_is_recognized(text, key, expected):
    # The key is a DOTTED nested path here — the shape the plugin's own pins take.
    rej = cli_contract.parse_invalid_config_value(text)
    assert rej is not None
    assert rej.key == key
    assert rej.kind == "invalid_type"
    assert rej.expected == expected


def test_invalid_config_value_never_carries_the_offending_value():
    """The rejected VALUE is deliberately not a field of the parse result.

    It is free-form text the user typed into the wrong key — plausibly a secret — and no
    pattern-based redactor can recognize an arbitrary one. The key and what codex EXPECTED
    are the actionable content; the value itself never reaches an envelope."""
    for text in (_INVALID_VARIANT_STDERR, _INVALID_TYPE_STDERR):
        rej = cli_contract.parse_invalid_config_value(text)
        assert rej is not None
        assert "bogus" not in repr(rej)
        assert "yes" not in repr(rej)


def test_invalid_config_value_is_distinct_from_the_other_two_config_grammars():
    # Three grammars, three recognizers: none may claim another's text.
    assert cli_contract.parse_invalid_config_value(_STRICT_OVERRIDE_STDERR) is None
    assert cli_contract.parse_invalid_config_value(_STRICT_FILE_STDERR) is None
    assert cli_contract.parse_invalid_config_value(_RETIRED_SETTING_STDERR) is None
    for text in (_INVALID_VARIANT_STDERR, _INVALID_TYPE_STDERR):
        assert cli_contract.parse_strict_config_rejection(text) is None
        assert cli_contract.parse_unsupported_config_setting(text) is None


def test_invalid_config_value_negative_controls():
    for text in (
        None,
        "",
        "some unrelated failure",
        # the header alone
        "Error loading config.toml:\n",
        # line 1 without the key line
        'Error loading config.toml: invalid type: string "yes", expected a boolean\n',
        # the key line without line 1
        "in `approval_policy`\n",
        # a serde message this recognizer does not know — only observed phrasings are
        # encoded, so a new one returns None and keeps its ordinary classification
        "Error loading config.toml: missing field `model`\nin `profiles.x`\n",
    ):
        assert cli_contract.parse_invalid_config_value(text) is None


def test_invalid_config_value_requires_the_key_on_the_very_next_line():
    # The key line is part of the grammar, not an optional trailer.
    apart = _INVALID_TYPE_STDERR.replace("\nin `", "\nsomething else\nin `")
    assert cli_contract.parse_invalid_config_value(apart) is None
    # CRLF-terminated lines still match (consult-only non-POSIX mode).
    crlf = _INVALID_TYPE_STDERR.replace("\n", "\r\n")
    rej = cli_contract.parse_invalid_config_value(crlf)
    assert rej is not None and rej.key == "sandbox_workspace_write.network_access"


def test_invalid_config_value_is_anchored_to_the_whole_blob():
    """The observed grammar is the ENTIRE stderr: no preamble, nothing after the key line.

    This recognizer runs ahead of the auth/drift/rate-limit substring matchers, so a
    config-shaped pair merely QUOTED ahead of a genuine diagnostic must not steal the
    classification from the matcher that should have won."""
    for tail in ("please run codex login\n", "usage limit reached\n", "unexpected argument\n"):
        stolen = _INVALID_TYPE_STDERR.rstrip("\n") + "\n" + tail
        # Positive control: a downstream matcher really does claim the tail.
        assert (
            cli_contract.is_auth_failure(stolen)
            or cli_contract.is_rate_limited(stolen)
            or cli_contract.is_contract_drift(stolen)
        )
        assert cli_contract.parse_invalid_config_value(stolen) is None, tail
    assert cli_contract.parse_invalid_config_value("preamble\n" + _INVALID_TYPE_STDERR) is None
    # Trailing whitespace/newlines are the one tolerated trailer — the real output ends in
    # a blank line, and a bare newline or none at all must parse too.
    for end in ("\n\n", "\n", "", "  \r\n"):
        text = _INVALID_TYPE_STDERR.rstrip("\n") + end
        assert cli_contract.parse_invalid_config_value(text) is not None, repr(end)


@pytest.mark.parametrize("field", ["key", "expected", "variant", "actual"])
def test_invalid_config_value_fails_closed_on_an_over_cap_span(field):
    """An implausibly long span is drift or hostile text, not a real rejection.

    Returning None keeps the run's ordinary `nonzero_exit` classification — a lost
    diagnosis, never a wrong one, and never an unbounded span echoed into an envelope."""
    big = "x" * (cli_contract.STRICT_CONFIG_KEY_MAX_CHARS + 10)
    text = {
        "key": _INVALID_TYPE_STDERR.replace("sandbox_workspace_write.network_access", big),
        "expected": _INVALID_TYPE_STDERR.replace("a boolean", big),
        "variant": _INVALID_VARIANT_STDERR.replace("`bogus`", f"`{big}`"),
        "actual": _INVALID_TYPE_STDERR.replace('"yes"', f'"{big}"'),
    }[field]
    assert cli_contract.parse_invalid_config_value(text) is None


def test_invalid_config_value_key_and_expected_boundaries_at_the_cap():
    # Exactly at the cap parses; one past it does not — the bound is exact, not fuzzy.
    cap = cli_contract.STRICT_CONFIG_KEY_MAX_CHARS
    at = _INVALID_TYPE_STDERR.replace("sandbox_workspace_write.network_access", "k" * cap)
    over = _INVALID_TYPE_STDERR.replace("sandbox_workspace_write.network_access", "k" * (cap + 1))
    assert cli_contract.parse_invalid_config_value(at) is not None
    assert cli_contract.parse_invalid_config_value(over) is None


@pytest.mark.parametrize("n", [1_000, 10_000, 100_000])
def test_invalid_config_value_pattern_has_no_catastrophic_backtracking(n):
    """Linear time on adversarial shapes built from the pattern's own slots."""
    import time

    head = "Error loading config.toml: "
    for shape in (
        head + "unknown variant `" + "a" * n,
        head + "unknown variant `a`, expected one of " + "`a`, " * n,
        head + "invalid type: " + ", expected " * n,
        head + "invalid type: " + "a, " * n + "expected a boolean\nin `k`",
        head + 'invalid type: string "x", expected a boolean\nin `' + "a." * n + "`",
    ):
        started = time.perf_counter()
        cli_contract.parse_invalid_config_value(shape)
        assert time.perf_counter() - started < 1.0


def test_invalid_config_value_actual_with_an_embedded_expected_clause_still_parses():
    # serde prints the offending string verbatim, so it can itself contain ", expected ".
    text = (
        'Error loading config.toml: invalid type: string "a, expected b", expected a boolean\n'
        "in `k`\n"
    )
    rej = cli_contract.parse_invalid_config_value(text)
    assert rej is not None and rej.expected == "a boolean"
