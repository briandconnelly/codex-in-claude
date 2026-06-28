"""Secret redaction in diffs."""

from __future__ import annotations

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
