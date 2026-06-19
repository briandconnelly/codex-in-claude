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
