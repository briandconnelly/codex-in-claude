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
