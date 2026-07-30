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
# Labelled secrets: a `key`/`token`/`password`-ish label, a `:`/`=`, then a long value.
# Named because the code-reference exemption below applies to this pattern alone (#421).

# The sensitive-label alternation. Defined once because the pattern below uses it TWICE —
# as the label itself, and inside the guard that stops a bracketed match from swallowing a
# later label. Two literal copies would drift, and a guard that silently stopped covering a
# label the pattern still matches is precisely the failure this indirection prevents.
_LABEL_ALT = (
    r"(?:(?:api|access|secret|private)?_?(?:key|token|secret)|passw(?:or)?d|pwd|passphrase)"
)
# The value run, also used twice for the same reason.
_VALUE_CHARS = r"[A-Za-z0-9._~+/=-]"

# Everything between a sensitive label and its `:`/`=` separator: an optional closing quote
# (`"api_key":`, #432) and, nested inside it, an optional closing bracket
# (`cfg["password"]["key"] =`, #434).
#
# This is written ONCE and the guard's copy is DERIVED from it, because sharing the label
# names was not enough. The first #434 fix hand-wrote the guard as a bare
# `_LABEL_ALT\s*[:=]`, omitting this step entirely — so a swallowed label wearing a #432
# closing quote was invisible to it and its secret leaked, on exactly the input family #432
# exists for. Deriving the anonymous form rather than retyping it makes that drift
# unrepresentable: the two cannot disagree about the shape, only about capture groups.
_KEY_STEP = r"(?P<key_quote>\\?['\"](?P<key_bracket>\s*\])?)?\s*[:=]"
_KEY_STEP_ANON = re.sub(r"\(\?P<\w+>", "(?:", _KEY_STEP)

LABELLED_VALUE_PATTERN = re.compile(
    # Two optional quotes, both of which also match a JSON-escaped quote (\"), so a secret
    # inside an unparsed JSON string (raw_response.text) is redacted on both sides:
    # `key_quote` closes the KEY (`"api_key": …`, #432) and the unnamed one opens the VALUE
    # (#58). Without the first, the separator had to sit immediately after the label, so a
    # quoted JSON key never matched at all and a generic secret in JSON went out unmasked.
    # `key_quote` also steps over the `]` of a subscript (`cfg["password"]["key"] = …`,
    # #434), which is why `key_bracket` is NESTED inside it rather than sitting beside it:
    # reaching a `]` requires consuming a quote first, so a bracketed match ALWAYS has a
    # truthy `key_quote` and can never take the exemption below. Moving the bracket out of
    # `key_quote` would silently break that guarantee.
    # `key_quote` is named because `_is_code_reference` keys the #421 exemption off it.
    rf"(?i)({_LABEL_ALT}{_KEY_STEP}\s*(?:\\?['\"])?)"
    # A bracketed match refuses to start when its own value would contain a LATER sensitive
    # label and separator. Found by the #434 review: a bracketed candidate matches EARLIER
    # than the pre-#434 pattern did, and `sub` never revisits consumed text, so
    # `cfg["token"] = application_specific_api_key = "<secret>"` matched at `token"]`,
    # swallowed `application_specific_api_key`, and sent the real secret after the second
    # separator out intact — where the old pattern had redacted it. Failing the candidate
    # here makes the engine advance and find the `api_key` match instead.
    #
    # The guard looks INSIDE the value, not past its end, because `_VALUE_CHARS` contains
    # `=`: an unspaced `label=value` chain is absorbed whole, so a trailing `(?!\s*[:=])`
    # inspects the wrong position and misses it entirely.
    #
    # It also has to allow the value run to start INSIDE a quoted key: the value's own
    # opening quote consumes the swallowed key's opening `"`, so the run begins at the
    # label and ends on its closing quote — which is why `_KEY_STEP_ANON` and not a bare
    # separator (the second #434 review finding; the bare form leaked
    # `cfg["token"] = "aws_secret_access_key": "<secret>"`).
    #
    # Conditioned on `key_bracket` so NON-bracket matches keep byte-identical behavior.
    # Unconditional, it would also change them — `key:api_key=<secret>` would redact only
    # the tail rather than the whole chain — and that class is pre-existing, reachable on
    # the pre-#434 pattern too, so it is tracked separately as #436 rather than quietly
    # altered here.
    rf"(?(key_bracket)(?!{_VALUE_CHARS}*{_LABEL_ALT}{_KEY_STEP_ANON}))"
    rf"{_VALUE_CHARS}{{16,}}"
)

SECRET_VALUE_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"(?i)(Authorization:\s*Bearer\s+)[A-Za-z0-9._~+/=-]{16,}"),
    LABELLED_VALUE_PATTERN,
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
    #
    # The scan starts AT the `://` and never looks left of it. It used to open with a
    # scheme run, `[a-zA-Z][\w+.-]*://`, which was quadratic (#438): that greedy class
    # holds every character a scheme does, so at each start position it ran to the end
    # of the surrounding word and then backtracked one character at a time hunting the
    # literal — work repeated at every position of a long run. 100 KB of unbroken text
    # took ~15s, and `redact_text` runs on untrusted model output, so a call could hang
    # past its deadline on data the caller never wrote. A possessive quantifier does not
    # fix it (it drops the backtrack, not the rescan) and bounding the run only trades
    # the blowup for a magic constant.
    #
    # Dropping the scheme costs no coverage, because the scheme was never part of the
    # REPLACED span, only of the surrounding match: the old pattern captured it in
    # group 1 and the replacement handed it straight back, and the new one leaves it
    # outside the match entirely. Either way it survives verbatim, so output is
    # byte-identical wherever the old pattern matched — pinned by a differential test
    # that runs the old pattern over the new pipeline's output and requires it to find
    # nothing. It does recognize strictly more — userinfo whose `://` no letter-led run
    # reaches (`://u:pw@h`, `9://u:pw@h`) — which is the safe direction here, and closes
    # a real leak: when the labelled pattern's marker had already eaten the scheme
    # (`key=<...>://user:pw@host`), the old pattern could no longer match and the
    # password went out intact.
    re.compile(r"(://[^:@\s/]+:)[^@\s/]+(?=@)"),
]


# --------------------------------------------------------------------------- #
# Code-reference exemption for the labelled pattern (#421)
# --------------------------------------------------------------------------- #
# LABELLED_VALUE_PATTERN matches any 16+ character identifier run after a `key`/`token`
# label, so ordinary source tripped it — `token = _helper(x)`, `key = OrderedDict()`,
# `idempotency_key: KeyParam = None`. On a review that masked the code under review out
# of the diff and, because any inline mask makes `coverage` partial, downgraded a `pass`
# to `unknown`.
#
# So a match is exempted only when it is provably a code reference rather than a
# credential. Every condition below removes a way a real credential gets written, and
# the exemption applies ONLY to a diff body line in a recognized SOURCE file (see
# DiffRedactor) — never to redact_text's arbitrary prose, and never to config or data,
# where none of this holds. The other patterns still run on an exempted line, so a value
# carrying a recognized vendor/JWT/PEM shape is caught anyway.
#
# The file-type gate is load-bearing, not belt-and-braces. Every condition here is a claim
# about CODE syntax: that an unquoted 16+ character run followed by `(` is an identifier
# being called, not a literal. In YAML, properties, or Markdown the identical text is a
# plain scalar — `key: correcthorsebatterystaple(2024)` is a password containing
# parentheses — and no line-local test can tell the two apart. Worse, YAML nests the
# sensitive label on a PRECEDING line (`secrets:` / `  key: …`), out of reach of any
# same-line scan. So data formats keep redaction unconditionally.

# Extensions whose `label = value` / `label: Type` lines are code. Deliberately a
# whitelist: an unknown extension is treated as data and keeps redaction (fail closed).
_SOURCE_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cjs",
        ".cpp",
        ".cs",
        ".cxx",
        ".dart",
        ".go",
        ".groovy",
        ".h",
        ".hh",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".lua",
        ".m",
        ".mjs",
        ".mm",
        ".php",
        ".py",
        ".pyi",
        ".pyx",
        ".rb",
        ".rs",
        ".scala",
        ".swift",
        ".ts",
        ".tsx",
        ".vala",
        ".zig",
    }
)

# A dotted identifier path and nothing else: no `+ / = ~ -`, which real base64-ish
# secrets carry and Python/JS names cannot.
_CODE_REFERENCE_VALUE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
# Never exempt these: a human password may legitimately end right before `(`, as in
# `password = correcthorsebatterystaple(2024)`. Matched as a substring rather than a whole
# label, because LABELLED_VALUE_PATTERN can match a compound label's TAIL — the match for
# `password_key = ...` starts at `_key`, so testing only the matched label misses the
# `password` entirely and the value leaks.
_SENSITIVE_LABEL_RE = re.compile(r"(?i)passw(?:or)?d|pwd|passphrase|secret")
# The label characters running up to the match, so the label is judged whole. Includes the
# separators one logical key is written with — `.` and `-` (`config.password.key = …`,
# `app-secret-key = …` in properties, Spring, and YAML) and `/` (path-style keys).
# Whitespace stays a boundary, so the scan cannot run back across unrelated earlier text.
# A bracketed key (`cfg["password"]["key"] = …`) needs no handling here, and deliberately
# gets none. This scan still cannot read a label across `"]["`, so it never sees the
# `password` — but it does not have to: the labelled pattern reaches the separator over `"]`
# only by consuming a key quote (#434), so `key_quote` is truthy and the rejection at the top
# of `_is_code_reference` fires before this scan is ever consulted. #434 originally argued the
# opposite — that widening the pattern would let a bracketed match be exempted here, and so had
# to be paired with widening this scan. That predates #432's fail-closed guard and is wrong;
# measured, not assumed. The cost of relying on the guard instead is that a bracketed match can
# NEVER be exempted, so ordinary source assigning to a `key`-ish subscript is masked. Accepted:
# fail-closed is the right direction for a secret boundary.
_LABEL_LEAD_RE = re.compile(r"[A-Za-z0-9_.\-/]*\Z")
# Whitespace after the separator. `api_key=value` — config, env, shell, query string —
# never gets the exemption; PEP8-style `key = value` and `key: Type` do.
_SPACED_SEPARATOR_RE = re.compile(r"[:=]\s")


def _is_source_path(path: str) -> bool:
    """Whether a diff path names a file whose lines are code. Empty/extension-less paths
    are not, so a diff fragment without a `diff --git` header keeps full redaction."""
    _, dot, suffix = path.rpartition(".")
    return bool(dot) and f".{suffix.lower()}" in _SOURCE_SUFFIXES


def _is_code_reference(match: re.Match) -> bool:
    """Whether a LABELLED_VALUE_PATTERN match is a code reference, not a credential."""
    label = match.group(1)
    # A quoted KEY is never a code reference, so every form #432 newly reaches fails closed.
    # Two reasons, either sufficient. A quoted key marks data, not an assignment this
    # function can reason about — and source files carry data freely, in comments,
    # docstrings, and string fixtures, where `{"key": secretvalue(2024)}` is a literal
    # rather than the call the follower test below would read it as. And the nested form
    # defeats the sensitive-label guard outright: `_LABEL_LEAD_RE` stops at the `"`, so
    # `{"password": {"key": …}}` matches at `key` and never sees `password`. That is the
    # same compound-label weakness #421's review found, and it is why this test is a plain
    # rejection rather than another condition to weigh.
    if match.group("key_quote"):
        return False
    if not _SPACED_SEPARATOR_RE.search(label):
        return False
    # Judge the whole logical label, including the identifier characters the pattern
    # matched no part of (`password_key` -> the match starts at `_key`).
    lead = _LABEL_LEAD_RE.search(match.string[: match.start()])
    if _SENSITIVE_LABEL_RE.search(f"{lead.group(0) if lead else ''}{label}"):
        return False
    if label.endswith(("'", '"')):  # a quoted literal is a value, not a reference
        return False
    value = match.group(0)[len(label) :]
    if not _CODE_REFERENCE_VALUE_RE.match(value):
        return False
    # What follows the value. Read from the match end — which is the true end of the
    # value, since the greedy `{16,}` has no trailing assertion to backtrack against. A
    # trailing lookahead would instead let it match one char short to satisfy the
    # assertion, redacting `_placeholder_seed` and leaving `d(text)` behind.
    rest = match.string[match.end() :]
    if rest.startswith("(") or re.match(r"\s+\+", rest):
        return True  # a call, or an operand in an expression
    # A default after an annotation — `idempotency_key: KeyParam = None`. Only ever
    # written with a `:` separator; allowing it after `=` would exempt
    # `token = abcd1234abcd1234efgh = leftover`, leaking a real value.
    return bool(re.match(r"\s+=", rest)) and ":" in label


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


def _redact_secret_values(line: str, *, exempt_code: bool = False) -> tuple[str, bool]:
    """Replace inline secret-looking values. ``exempt_code`` leaves provable code
    references intact — only sound for a line of source (a diff body line), so callers
    handling arbitrary prose must leave it False (#421)."""
    redacted = False
    out = line
    for pattern in SECRET_VALUE_PATTERNS:
        exempting = exempt_code and pattern is LABELLED_VALUE_PATTERN

        def repl(match: re.Match, *, exempting: bool = exempting) -> str:
            nonlocal redacted
            if exempting and _is_code_reference(match):
                return match.group(0)
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


def exc_summary(exc: BaseException) -> str:
    """Return an exception class plus non-empty redacted detail, if any."""
    name = type(exc).__name__
    detail = redact_text(str(exc)) or ""
    return f"{name}: {detail}" if detail.strip() else name


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
    zero or more output lines for the given input line. Mirrors ``redact`` exactly.

    REQUIRES a diff in git's standard format, where every file's body is preceded by its
    own ``diff --git`` header: the per-file state (whether to skip the hunk, and whether
    the file is source for the #421 code exemption) is set from that header and persists
    until the next one. Both start closed on a fresh instance, so a headerless stream is
    redacted conservatively — but feeding file B's body without B's header would judge it
    under file A's verdict. Every caller here passes output straight from ``git diff`` or
    ``git show``, which always emits the headers."""

    def __init__(self) -> None:
        self.redacted: list[str] = []
        self._skipping = False
        self._current_path = ""
        self._source_file = False

    def feed(self, line: str) -> list[str]:
        if line.startswith("diff --git "):
            spec = line[len("diff --git ") :]
            self._current_path = _diff_path_from_header(line)
            self._source_file = _is_source_path(self._current_path)
            self._skipping = bool(
                SECRET_PATH_RE.search(spec) or SECRET_PATH_RE.search(self._current_path)
            )
            if self._skipping:
                self.redacted.append(self._current_path or spec)
                return [line, "[redacted: secret-looking file not sent]"]
        if self._skipping:
            return []
        body_line = line.startswith(("+", "-", " ")) and not line.startswith(("+++", "---"))
        scan_line = body_line or line.startswith("Authorization:")
        if scan_line:
            # A labelled match may be exempted as a code reference only on a diff BODY
            # line of a recognized source file (#421). A bare `Authorization:` header is
            # not source, and neither is YAML/JSON/properties/Markdown content, so both
            # get the same conservative treatment as free-text prose.
            emit, changed = _redact_secret_values(line, exempt_code=body_line and self._source_file)
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
