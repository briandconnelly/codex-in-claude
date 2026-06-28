"""Best-effort secret redaction for diffs before they leave the machine.

Defense-in-depth, NOT a guarantee: it covers the diff text this server gathers.
A run that lets Codex read files itself can still surface secrets the redactor
never saw. CLI-agnostic."""

from __future__ import annotations

import re
import shlex

# Files whose contents are too sensitive to send: their hunks are dropped (the
# header is kept so a reviewer still sees the file changed).
SECRET_PATH_RE = re.compile(
    r"(^|/)(\.env(\.|$)|\.envrc$|\.netrc$|\.pypirc$|.*\.env$|.*\.pem$|.*\.key$|id_rsa|id_ed25519|.*\.p12$)",
    re.IGNORECASE,
)

# Inline secret-value shapes redacted within otherwise-sendable lines.
SECRET_VALUE_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"(?i)(Authorization:\s*Bearer\s+)[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(
        # The optional opening quote also matches a JSON-escaped quote (\") so a secret
        # quoted inside an unparsed JSON string (raw_response.text) is still redacted (#58).
        r"(?i)((?:(?:api|access|secret|private)?_?(?:key|token|secret)|passw(?:or)?d|pwd|passphrase)\s*[:=]\s*(?:\\?['\"])?)[A-Za-z0-9._~+/=-]{16,}"
    ),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # Unlabeled secrets caught by shape alone (#73), independent of an adjacent label.
    # JWT: three base64url segments after the `eyJ` ("{" base64) header marker.
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    # Vendor key prefixes: OpenAI (sk-, sk-proj-), Stripe (sk_live_/sk_test_),
    # Google (AIza). `{n,}` rather than a fixed length so a longer/variant token
    # can't leave a trailing suffix unredacted.
    re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35,}"),
    # Connection-string userinfo: redact the password between `://user:` and `@host`,
    # keeping scheme, user, and host. The `@` lookahead avoids matching `host:port`.
    re.compile(r"([a-zA-Z][\w+.-]*://[^:@\s/]+:)[^@\s/]+(?=@)"),
]


def _diff_path_from_header(line: str) -> str:
    spec = line[len("diff --git ") :]
    try:
        parts = shlex.split(spec)
    except ValueError:
        parts = spec.split()
    if len(parts) >= 2:
        path = parts[1]
        return path[2:] if path.startswith("b/") else path
    return spec


def _redact_secret_values(line: str) -> tuple[str, bool]:
    redacted = False
    out = line
    for pattern in SECRET_VALUE_PATTERNS:

        def repl(match: re.Match) -> str:
            nonlocal redacted
            redacted = True
            if match.lastindex:
                return f"{match.group(1)}[redacted: secret value]"
            return "[redacted: secret value]"

        out = pattern.sub(repl, out)
    return out, redacted


def redact_text(text: str | None) -> str | None:
    """Best-effort inline secret-value redaction for free-text (no diff/file logic).

    Applies only the inline ``SECRET_VALUE_PATTERNS`` — the same value replacement
    used on diff body lines — to arbitrary prose Codex returns (summaries, answers,
    raw_response text, finding fields). File-hunk dropping does not apply to prose,
    so only inline values are replaced with ``[redacted: secret value]``. ``None``
    and empty strings pass through unchanged. Defense-in-depth, NOT a guarantee
    (consistent with this module's contract)."""
    if not text:
        return text
    out, _ = _redact_secret_values(text)
    return out


def redact_tree(value: object) -> object:
    """Deep-apply ``redact_text`` to every string *value* in a nested list/dict/str.

    Used to sanitize a parsed structured payload (summary, findings, questions,
    assumptions, next_steps) in one pass; non-string leaves (ints, enums, None)
    are returned untouched, and short enum/path values never match a secret
    pattern, so structure and semantics are preserved. Dict KEYS are left as-is
    (they are field names, not secret-bearing content); only the mapped values are
    recursed into."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_tree(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_tree(item) for key, item in value.items()}
    return value


class DiffRedactor:
    """Incremental, line-oriented secret redactor for a unified diff. Carries the
    per-file skip state across calls so it can be driven over a streamed diff (one
    logical line at a time) without materializing the whole text. ``feed`` returns
    zero or more output lines for the given input line. Mirrors ``redact`` exactly."""

    def __init__(self) -> None:
        self.redacted: list[str] = []
        self._skipping = False
        self._current_path = ""

    def feed(self, line: str) -> list[str]:
        if line.startswith("diff --git "):
            spec = line[len("diff --git ") :]
            self._current_path = _diff_path_from_header(line)
            self._skipping = bool(
                SECRET_PATH_RE.search(spec) or SECRET_PATH_RE.search(self._current_path)
            )
            if self._skipping:
                self.redacted.append(self._current_path or spec)
                return [line, "[redacted: secret-looking file not sent]"]
        if self._skipping:
            return []
        scan_line = (
            line.startswith(("+", "-", " ")) and not line.startswith(("+++", "---"))
        ) or line.startswith("Authorization:")
        if scan_line:
            emit, changed = _redact_secret_values(line)
            if changed and self._current_path and self._current_path not in self.redacted:
                self.redacted.append(self._current_path)
            return [emit]
        return [line]


def redact(diff: str) -> tuple[str, list[str]]:
    """Redact secret-looking files and inline values. Returns (text, paths)."""
    redactor = DiffRedactor()
    out_lines: list[str] = []
    for line in diff.splitlines():
        out_lines.extend(redactor.feed(line))
    return "\n".join(out_lines), redactor.redacted
