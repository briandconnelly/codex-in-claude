"""Secret redaction in diffs."""

from __future__ import annotations

import pytest

from codex_in_claude._core import redaction


def test_secret_file_hunks_dropped():
    diff = "\n".join(
        [
            "diff --git a/.env b/.env",
            "+++ b/.env",
            "+SECRET_TOKEN=supersecretvalue1234567890",
            "diff --git a/main.py b/main.py",
            "+print('hi')",
        ]
    )
    out, redacted = redaction.redact(diff)
    assert ".env" in redacted
    assert "supersecretvalue" not in out
    assert "[redacted: secret-looking file not sent]" in out
    assert "print('hi')" in out  # non-secret file preserved


def test_inline_secret_value_redacted():
    diff = "\n".join(
        [
            "diff --git a/config.py b/config.py",
            "+api_key = 'abcdef0123456789abcdef0123'",
        ]
    )
    out, redacted = redaction.redact(diff)
    assert "abcdef0123456789" not in out
    assert "[redacted: secret value]" in out
    assert "config.py" in redacted


def test_aws_key_redacted():
    diff = "diff --git a/x b/x\n+key = AKIAIOSFODNN7EXAMPLE"
    out, _ = redaction.redact(diff)
    assert "AKIAIOSFODNN7EXAMPLE" not in out


def test_clean_diff_unchanged():
    diff = "diff --git a/x.py b/x.py\n+def f():\n+    return 1"
    out, redacted = redaction.redact(diff)
    assert redacted == []
    assert "return 1" in out


# --- unlabeled / vendor-shape secrets (#73) ---------------------------------
def test_jwt_redacted_in_diff():
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    # Unlabeled — no key=/token= adjacent, so only a JWT-shape pattern catches it.
    out, redacted = redaction.redact(f"diff --git a/x.py b/x.py\n+Cookie: {jwt}")
    assert jwt not in out
    # Exact output — no fragment of the token survives around the placeholder.
    assert "+Cookie: [redacted: secret value]" in out
    assert "x.py" in redacted


def test_vendor_key_prefixes_redacted():
    secrets = [
        "sk-abcdefABCDEF0123456789abcdefABCDEF",  # OpenAI legacy
        "sk-proj-abcdefABCDEF0123456789_-abcdefABCDEF",  # OpenAI project key (hyphenated)
        "sk_live_abcdefABCDEF0123456789",  # Stripe live
        "sk_test_abcdefABCDEF0123456789",  # Stripe test
        "AIzaSyA0123456789abcdefABCDEF0123456789",  # Google (AIza + 35)
    ]
    for secret in secrets:
        out = redaction.redact_text(f"the value is {secret} here")
        assert secret not in out, secret
        assert "[redacted: secret value]" in out
        # No fragment of the token may survive — surrounding prose stays intact.
        assert out == "the value is [redacted: secret value] here", secret


def test_oversized_google_key_fully_redacted():
    # A token longer than the canonical length must not leave a trailing suffix.
    out = redaction.redact_text("AIzaSyA0123456789abcdefABCDEF0123456789EXTRA stuff")
    assert "EXTRA" not in out
    assert out == "[redacted: secret value] stuff"


def test_unlabeled_connection_string_password_redacted():
    text = "DATABASE_URL=postgres://user:s3cr3tPassw0rd@db.example.com:5432/app"
    out = redaction.redact_text(text)
    assert "s3cr3tPassw0rd" not in out
    assert "[redacted: secret value]" in out
    # user, scheme, and host are preserved — only the password is stripped.
    assert "postgres://user:" in out
    assert "@db.example.com:5432/app" in out


def test_url_with_port_not_treated_as_credentials():
    # No userinfo `@`, so the port must not be mistaken for a password.
    text = "see https://example.com:8080/path for details"
    assert redaction.redact_text(text) == text


# --- free-text redaction (#58) ----------------------------------------------
def test_redact_text_replaces_inline_secret():
    text = 'The config sets api_key = "abcdef0123456789abcdef0123" for auth.'
    out = redaction.redact_text(text)
    assert "abcdef0123456789" not in out
    assert "[redacted: secret value]" in out


def test_redact_text_handles_github_token_and_aws_key():
    text = "token ghp_abcdefABCDEF0123456789abcdefABCDEF and AKIAIOSFODNN7EXAMPLE here"
    out = redaction.redact_text(text)
    assert "ghp_abcdefABCDEF0123456789" not in out
    assert "AKIAIOSFODNN7EXAMPLE" not in out


def test_redact_text_handles_json_escaped_quote():
    # raw_response.text is the unparsed JSON, where a quoted value is backslash-escaped
    # (password = \"secret\"). The redactor must still strip the value (#58 review gap).
    text = 'found password = \\"supersecretvalue1234567890\\" in config'
    out = redaction.redact_text(text)
    assert "supersecretvalue" not in out
    assert "[redacted: secret value]" in out


def test_redact_text_preserves_clean_prose_and_newlines():
    text = "Line one is fine.\nLine two returns 1.\n"
    assert redaction.redact_text(text) == text


def test_redact_text_passes_through_none_and_empty():
    assert redaction.redact_text(None) is None
    assert redaction.redact_text("") == ""


def test_exc_summary_preserves_non_empty_exception_detail_whitespace():
    assert (
        redaction.exc_summary(RuntimeError("  padded detail  "))
        == "RuntimeError:   padded detail  "
    )
    assert redaction.exc_summary(RuntimeError("   ")) == "RuntimeError"


def test_diff_redactor_matches_redact():
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n"
        '+api_key = "AKIAABCDEFGHIJKLMNOP"\n'
        "diff --git a/.env b/.env\n"
        "--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n+SECRET=topsecretvalue123456\n"
    )
    expected_text, expected_paths = redaction.redact(diff)
    r = redaction.DiffRedactor()
    out_lines: list[str] = []
    for line in diff.splitlines():
        out_lines.extend(r.feed(line))
    assert "\n".join(out_lines) == expected_text
    assert r.redacted == expected_paths


def test_diff_redactor_drops_secret_file_hunks():
    r = redaction.DiffRedactor()
    out: list[str] = []
    for line in ["diff --git a/.env b/.env", "--- a/.env", "+++ b/.env", "+TOKEN=abc"]:
        out.extend(r.feed(line))
    assert "diff --git a/.env b/.env" in out
    assert "[redacted: secret-looking file not sent]" in out
    assert "+TOKEN=abc" not in out  # the hunk body is dropped
    assert ".env" in r.redacted


def test_redact_tree_walks_nested_structures():
    tree = {
        "summary": 'password = "supersecretvalue1234567890"',
        "findings": [
            {"severity": "high", "evidence": "token: ghp_abcdefABCDEF0123456789abcdefABCDEF"}
        ],
        "questions": ["AKIAIOSFODNN7EXAMPLE?"],
        "count": 3,
    }
    out = redaction.redact_tree(tree)
    assert "supersecretvalue" not in out["summary"]
    assert "ghp_abcdefABCDEF" not in out["findings"][0]["evidence"]
    assert "AKIAIOSFODNN7EXAMPLE" not in out["questions"][0]
    # Short enum values and non-strings pass through unchanged.
    assert out["findings"][0]["severity"] == "high"
    assert out["count"] == 3


# --- code-reference exemption (#421) ----------------------------------------
# The labelled-value pattern matches any 16+ char identifier run after a `key`/`token`
# label, so ordinary source was masked out of reviewed diffs — hiding the code under
# review and downgrading `coverage` to `partial`. Diff lines exempt matches that are
# provably code references; `redact_text` prose stays conservative.

# Innocuous source observed being scrubbed on real reviews of this repo.
_CODE_LINES = [
    "+    token = _PLACEHOLDER_PREFIX + _placeholder_seed(text)",
    "+    token = _placeholder_seed(text)",
    "+    state_token = _worktree_state_token(cwd, norm_paths, state_excludes, timeout)",
    "+    key = collections.OrderedDict()",
    "+    idempotency_key: IdempotencyKeyParam = None,",
    "+    return {_STABILITY_META_KEY: _TOOL_STABILITY.get(name, _SERVER_STABILITY)}",
]


@pytest.mark.parametrize("line", _CODE_LINES)
def test_code_reference_left_intact_in_diff(line: str):
    diff = f"diff --git a/app.py b/app.py\n{line}"
    out, paths = redaction.redact(diff)
    # Byte-identical: a partial replacement (e.g. leaving `d(text)` behind) also fails.
    assert out == diff
    assert paths == []


def test_code_reference_exemption_cannot_be_defeated_by_backtracking():
    # A trailing negative lookahead would let the greedy value match one char short to
    # satisfy the assertion, redacting `_placeholder_seed` and leaving `d(text)`.
    line = "+    token = _placeholder_seed(text)"
    out, _ = redaction.redact(f"diff --git a/app.py b/app.py\n{line}")
    assert "[redacted" not in out
    assert out.endswith(line)


# Realistic credential-bearing lines that MUST stay redacted. Each one defeats a
# specific condition of the exemption.
_MUST_REDACT = [
    # config/env/URL form — no whitespace after the separator
    "SECRET_TOKEN=supersecretvalue1234567890",
    "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIKSEVENGbPxRfiCYEXAMPLEKEY",
    "CI_JOB_TOKEN=opaquevaluewithnoprefix123",
    "api_key=AbCdEf0123456789XyZwVu(legacy)",
    "?token=AbCdEf0123456789XyZwVu&next=/",
    # password family is never exempt, even in code-expression context
    "password = correcthorsebatterystaple(2024)",
    "passphrase = myverylongdicewarephrase(v2)",
    "secret = somethinglongenoughhere(rotated)",
    # ...including a COMPOUND label, where the pattern matches only the trailing
    # `_key`/`_token` and so never sees the sensitive word by itself.
    "password_key = correcthorsebattery(2024)",
    "PASSWD_KEY = somethinglongenoughhere(x)",
    "client_secret_key = opaquevaluehere12345 + more",
    "app_passphrase_token = longvaluegoeshere(x)",
    # ...including when a further `_`-separated segment sits between the sensitive word and
    # the segment the pattern matched. These are what make `_` load-bearing in the lead
    # charset: in the cases above the scan reaches a sensitive word without crossing one.
    "password_reset_key = correcthorsebattery(2024)",
    "user_password_reset_token = correcthorsebattery(2024)",
    # ...and a DOTTED or HYPHENATED compound label, as properties/Spring/YAML config
    # writes it, where `.`/`-` must count as label characters rather than boundaries.
    "config.password.key = somethinglongvalue(x)",
    "db.passwd.token = anotherlongvaluehere(y)",
    "app-secret-key = opaquevaluegoeshere12(z)",
    "spring.datasource.password.key = mysecretvaluehere(q)",
    # ...and a path-style key, where `/` must count as a label character too.
    "database/password/key = correcthorsebattery(2024)",
    # `=` separator never grants the ` =` follower exemption
    "token = abcd1234abcd1234efgh = leftover",
    # quoted literals are never exempt
    'TOKEN = "AbCdEf0123456789XyZwVu"',
    "api_key = 'abcdef0123456789abcdef0123'",
    # ...including when the quoted string continues past the value, so that what follows
    # it *is* an exemption trigger. Without the quote check these would be exempted.
    'token = "abcdefghij1234567890 + trailing"',
    'token: "abcdefghij1234567890 = trailing"',
    # value carries non-identifier characters, so it is no code reference
    "token = AbCdEf+0123456789/XyZwVu=",
    # ...again positioned so the follower would otherwise exempt it.
    "token = abc+def/ghi=jkl123456789 + more",
    "token = AbCdEf+0123456789/XyZwVu=(x)",
]


@pytest.mark.parametrize("line", _MUST_REDACT)
def test_credential_still_redacted_in_diff(line: str):
    diff = f"diff --git a/app.py b/app.py\n+{line}"
    out, paths = redaction.redact(diff)
    assert "[redacted: secret value]" in out
    assert paths == ["app.py"]


# Config and data files, where `key: value(2024)` is a plain scalar rather than a call, so
# the exemption's syntax argument does not hold. Each is a full diff, since the nested case
# needs its label on a preceding line.
_CONFIG_DIFFS = [
    # nested YAML — the sensitive label is on the PREVIOUS line, out of reach of any
    # line-local scan, so file type is the only thing that can save this
    "diff --git a/conf.yml b/conf.yml\n+secrets:\n+  key: correcthorsebatterystaple(2024)",
    # label words separated by whitespace or `/`, which no label charset can absorb
    "diff --git a/app.conf b/app.conf\n+database password key: correcthorsebattery(2024)",
    "diff --git a/app.conf b/app.conf\n+database/password/key: correcthorsebattery(2024)",
    # a bare `key: value(x)` in data formats, with no sensitive word anywhere
    "diff --git a/conf.yaml b/conf.yaml\n+  token: correcthorsebatterystaple(2024)",
    "diff --git a/settings.json b/settings.json\n+  key: correcthorsebatterystaple(2024)",
    "diff --git a/app.properties b/app.properties\n+key = correcthorsebatterystaple(2024)",
    "diff --git a/notes.md b/notes.md\n+token = correcthorsebatterystaple(2024)",
    # no path at all (a bare diff fragment): fail closed rather than guess
    "+    token = correcthorsebatterystaple(2024)",
]


@pytest.mark.parametrize("diff", _CONFIG_DIFFS)
def test_non_source_file_gets_no_code_exemption(diff: str):
    out, _ = redaction.redact(diff)
    assert "[redacted: secret value]" in out


def test_vendor_shape_still_redacted_inside_exempt_context():
    # The exemption only suppresses the labelled pattern; every vendor/JWT/PEM shape
    # still runs, so a recognized secret in code-expression context is caught anyway.
    diff = "diff --git a/app.py b/app.py\n+    token = sk-abcdefghijklmnopqrstuv(x)"
    out, paths = redaction.redact(diff)
    assert "sk-abcdefghijklmnopqrstuv" not in out
    assert paths == ["app.py"]


def test_redact_text_does_not_exempt_code_references():
    # Prose carries no syntax guarantee, so free text stays conservatively redacted.
    text = "token = _placeholder_seed(text)"
    assert redaction.redact_text(text) != text
    assert "[redacted: secret value]" in redaction.redact_text(text)


def test_authorization_header_line_is_not_treated_as_source():
    # DiffRedactor scans bare `Authorization:` lines too, but a header is not source, so
    # it gets prose's conservative treatment rather than the code-reference exemption.
    line = "Authorization: token = _placeholder_seed(text)"
    out, _ = redaction.redact(f"diff --git a/app.py b/app.py\n{line}")
    assert "[redacted: secret value]" in out


# --- quoted keys (#432) ------------------------------------------------------
# The labelled pattern required its separator IMMEDIATELY after the label, but JSON puts
# the key's closing quote there first, so a quoted key never matched. That silently
# exempted the carrier this pattern exists for: a credential with no vendor shape
# (AWS_SECRET_ACCESS_KEY, CI_JOB_TOKEN, an internal HMAC secret) sitting in JSON config,
# a fixture, or a captured API response.

# Values chosen to match NO other pattern in SECRET_VALUE_PATTERNS. A vendor-shaped probe
# (`sk-…`, `ghp_…`) is stripped by its own pattern regardless, so a test using one passes
# against the bug it guards (#417). test_probe_value_is_not_self_redacting is the control
# that keeps this property honest.
_PROBES = [
    "abcdefghij1234567890",
    "AbCdEf0123456789XyZwVu",
    "opaquevaluewithnoprefix123",
    "wJalrXUtnFEMIKbPxRfiCYEXAMPLEKEY",
]


@pytest.mark.parametrize("value", _PROBES)
def test_probe_value_is_not_self_redacting(value: str):
    # Positive control for every #432 test below: each probe must survive redaction on its
    # own. Without this, an "the secret is gone" assertion proves nothing about the label
    # pattern — another pattern could be doing the work.
    assert redaction.redact_text(value) == value


_QUOTED_KEY_LINES = [
    '"api_key": "abcdefghij1234567890"',
    '  "client_secret": "AbCdEf0123456789XyZwVu",',
    "'api_key': 'abcdefghij1234567890'",
    '"AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMIKbPxRfiCYEXAMPLEKEY"',
    # whitespace between the key's closing quote and the separator, as JSON permits
    '"password" : "opaquevaluewithnoprefix123"',
    # a JSON blob inside an unparsed string, where both quotes arrive backslash-escaped —
    # the case `raw_response.text` carries (#58 handled only the value's opening quote)
    '{\\"api_key\\": \\"abcdefghij1234567890\\"}',
]


@pytest.mark.parametrize("line", _QUOTED_KEY_LINES)
def test_quoted_key_secret_redacted_in_prose(line: str):
    out = redaction.redact_text(line)
    assert "[redacted: secret value]" in out
    for probe in _PROBES:
        assert probe not in out


def test_quoted_key_secret_redacted_in_json_diff():
    diff = 'diff --git a/config.json b/config.json\n+  "api_key": "abcdefghij1234567890"'
    out, paths = redaction.redact(diff)
    assert "abcdefghij1234567890" not in out
    assert paths == ["config.json"]


def test_unquoted_key_still_redacted():
    # Compatibility control: the form that already worked before #432 must keep working.
    out = redaction.redact_text('api_key: "abcdefghij1234567890"')
    assert "[redacted: secret value]" in out


# A quoted key inside a SOURCE file gets no code-reference exemption. Two independent
# reasons, either one sufficient: a quoted key marks data rather than an assignment the
# exemption can reason about (comments, docstrings, and string fixtures all carry JSON),
# and `_LABEL_LEAD_RE` cannot read `password` across `": {"`, so the sensitive-label guard
# never fires on the nested form. Widening the pattern without failing closed here would
# have turned the #421 exemption into a leak.
_QUOTED_KEY_IN_SOURCE = [
    '+# captured: {"password": {"key": correcthorsebatterystaple(2024)}}',
    "+    EXAMPLE = '{\"api_key\": opaquevaluewithnoprefix123(x)}'",
    '+cfg = {"password": {"key": helper_function_name_here(x)}}',
    '+    payload = {"api_key": "abcdefghij1234567890"}',
]


@pytest.mark.parametrize("line", _QUOTED_KEY_IN_SOURCE)
def test_quoted_key_never_gets_code_exemption(line: str):
    diff = f"diff --git a/app.py b/app.py\n{line}"
    out, paths = redaction.redact(diff)
    assert "[redacted: secret value]" in out
    assert paths == ["app.py"]


def test_neutral_quoted_key_is_untouched():
    # The widening keys on the LABEL, not on the quoting: an ordinary JSON key keeps its
    # value, so this does not degrade into "redact every quoted string in a config file".
    line = '+  "display_name": "abcdefghij1234567890"'
    diff = f"diff --git a/config.json b/config.json\n{line}"
    out, paths = redaction.redact(diff)
    assert out == diff
    assert paths == []


# --- bracket-subscripted keys (#434) -----------------------------------------
# #432 taught the label group to step over a key's closing QUOTE, but not over the `"]`
# that follows it in a subscript, so `cfg["password"]["key"] = <secret>` matched nothing
# at all. The bracket now lives INSIDE the `key_quote` group, which is what keeps the
# widening safe: reaching a `]` requires consuming a quote first, so `key_quote` is
# always truthy on a bracketed match and `_is_code_reference` rejects it outright. The
# invariant test below pins that structural property rather than trusting it.
#
# #434 argued this widening was unsafe on its own — that the match would be EXEMPTED as a
# code reference, turning a false negative into a leak, and that `_LABEL_LEAD_RE` had to
# learn to read across `"]["` in the same change. That analysis predates #432's own
# fail-closed guard and does not hold: the `key_quote` rejection fires first, so no
# `_LABEL_LEAD_RE` change is needed. The follower test below is the one that would catch a
# regression here, because it uses a value the exemption WOULD accept.

_BRACKET_KEY_LINES = [
    'cfg["password"]["key"] = abcdefghij1234567890',
    "cfg['passwd']['token'] = AbCdEf0123456789XyZwVu",
    # the separator is reached over `"]`, so a nested lookup is covered at any depth
    'settings["auth"]["api_key"] = opaquevaluewithnoprefix123',
    # whitespace before the closing bracket is valid syntax in every language this
    # pattern's source whitelist covers
    'cfg["password"]["key" ] = wJalrXUtnFEMIKbPxRfiCYEXAMPLEKEY',
    # a subscript inside an unparsed JSON string, where the quotes arrive escaped
    'cfg[\\"password\\"][\\"key\\"] = abcdefghij1234567890',
]


@pytest.mark.parametrize("line", _BRACKET_KEY_LINES)
def test_bracket_subscripted_key_secret_redacted_in_prose(line: str):
    out = redaction.redact_text(line)
    assert "[redacted: secret value]" in out
    for probe in _PROBES:
        assert probe not in out


@pytest.mark.parametrize("line", _BRACKET_KEY_LINES)
def test_bracket_subscripted_key_never_gets_code_exemption(line: str):
    # The same lines inside a SOURCE file, where the #421 exemption is live. Redaction
    # must not weaken just because the extension says "code".
    diff = f"diff --git a/app.py b/app.py\n+{line}"
    out, paths = redaction.redact(diff)
    assert "[redacted: secret value]" in out
    assert paths == ["app.py"]


def test_bracket_key_with_exemption_triggering_follower_is_redacted():
    # The case that actually exercises the disputed path. Every other probe here ends the
    # line, which `_is_code_reference` rejects anyway on the follower test alone — so a
    # broken `key_quote` guard would still pass them. This value is a bare dotted
    # identifier followed by `(`, which the exemption WOULD accept if it were ever
    # consulted, so only the fail-closed rejection keeps it redacted.
    line = '+cfg["password"]["key"] = helper_function_name_here(x)'
    diff = f"diff --git a/app.py b/app.py\n{line}"
    out, paths = redaction.redact(diff)
    assert "[redacted: secret value]" in out
    assert "helper_function_name_here" not in out
    assert paths == ["app.py"]


def test_bracket_consuming_match_always_rejects_the_code_exemption():
    # The structural invariant the widening rests on, asserted directly rather than
    # inferred from behavior: a `]` can only be reached through `key_quote`, so no match
    # can consume one while leaving that group empty. If a later edit moves the bracket
    # outside the group, this fails even if every behavioral test above still passes.
    probes = [
        'a["key"] = ' + "x" * 20,
        "a['key'] = " + "x" * 20,
        'a[\\"key\\"] = ' + "x" * 20,
        'a["key" ] = ' + "x" * 20,
        'a["password"]["key"] = ' + "x" * 20,
    ]
    seen_bracket = 0
    for probe in probes:
        match = redaction.LABELLED_VALUE_PATTERN.search(probe)
        assert match is not None, probe
        if "]" in match.group(0):
            seen_bracket += 1
            assert match.group("key_quote"), probe
            assert not redaction._is_code_reference(match), probe
    # Guard the guard: if the pattern stopped matching brackets entirely, every assertion
    # above would vacuously pass.
    assert seen_bracket == len(probes)


def test_neutral_bracket_key_is_untouched():
    # The widening still keys on the LABEL. An ordinary subscript keeps its value, so this
    # does not degrade into "redact every subscripted assignment in a source file".
    line = '+cfg["display_name"]["value"] = abcdefghij1234567890'
    diff = f"diff --git a/app.py b/app.py\n{line}"
    out, paths = redaction.redact(diff)
    assert out == diff
    assert paths == []


def test_bracket_key_redacts_ordinary_code_by_design():
    # An ACCEPTED regression, pinned so it stays a deliberate policy choice rather than a
    # surprise. A bracketed match can never take the #421 exemption, so ordinary source
    # assigning to a `key`/`token`-ish subscript is masked out of a reviewed diff. The
    # label alternatives are unanchored, so this reaches innocent suffixes too
    # (`obj["monkey"]`). Measured over 3124 real source files: 3 newly redacted lines,
    # two of which the unsubscripted form already redacts today. Fail-closed is the right
    # direction for this boundary; revisit only with evidence the cost is material (#434).
    line = '+token["refresh_token"] = self.refresh_token_generator(user, scope)'
    diff = f"diff --git a/app.py b/app.py\n{line}"
    out, paths = redaction.redact(diff)
    assert "[redacted: secret value]" in out
    assert paths == ["app.py"]
