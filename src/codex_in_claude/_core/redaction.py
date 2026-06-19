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


def redact(diff: str) -> tuple[str, list[str]]:
    """Redact secret-looking files and inline values. Returns (text, paths)."""
    out_lines: list[str] = []
    redacted: list[str] = []
    skipping = False
    current_path = ""
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            spec = line[len("diff --git ") :]
            current_path = _diff_path_from_header(line)
            skipping = bool(SECRET_PATH_RE.search(spec) or SECRET_PATH_RE.search(current_path))
            if skipping:
                redacted.append(current_path or spec)
                out_lines.append(line)  # keep the real header so reviewers see the file
                out_lines.append("[redacted: secret-looking file not sent]")
                continue
        if not skipping:
            scan_line = (
                line.startswith(("+", "-", " ")) and not line.startswith(("+++", "---"))
            ) or line.startswith("Authorization:")
            emit = line
            if scan_line:
                emit, changed = _redact_secret_values(line)
                if changed and current_path and current_path not in redacted:
                    redacted.append(current_path)
            out_lines.append(emit)
    return "\n".join(out_lines), redacted
