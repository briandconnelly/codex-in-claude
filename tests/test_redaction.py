"""Secret redaction in diffs."""

from __future__ import annotations

import itertools
import re
import string
import time

import pytest

from codex_in_claude._core import redaction


def _any_marker_in(text: str) -> bool:
    """Whether EITHER redaction marker — the plain one or #446's partial variant — appears
    in ``text``. For tests whose contract is "this value must stay redacted", not which of
    the two markers it gets; the trailing/leading checks that pick between them are pinned
    separately (`test_shared_safe_terminators_keep_the_plain_marker_for_labelled_values`,
    `test_userinfo_only_safe_terminators_keep_the_plain_marker_for_wide_scope_candidates`,
    and the `(`-follower cases throughout this file)."""
    return redaction._SECRET_VALUE_MARKER in text or redaction._PARTIAL_SECRET_VALUE_MARKER in text


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


# --- #438: the connection-string scan starts at `://` ------------------------
# The pattern used to scan the SCHEME with an unbounded greedy `[a-zA-Z][\w+.-]*`
# ahead of the `://` literal. That run went to the end of every non-`/` stretch at
# every start position and then backtracked hunting the literal, which is quadratic
# and reachable from untrusted model output (redact_text/redact_tree). The scheme was
# never part of the REDACTED span — the old pattern captured it in group 1 and the
# replacement handed it straight back, and the new one leaves it outside the match
# entirely — so the scan now begins at `://`. Two consequences are pinned below: no
# password the old pattern redacted stops being redacted, and userinfo the old pattern
# could not reach is now covered.

# The pre-#438 pattern, kept as an ORACLE. The invariant that matters for a secret
# boundary is one-directional — anchoring may recognize MORE, never less — and the only
# way to test that is against the thing being replaced, so it is spelled out here rather
# than imported. Comparing against the old PATTERN on the raw line, rather than against
# the old pipeline's output, is deliberately the stricter check: an earlier pattern in
# the list may have already consumed the value, and the assertion below holds either way.
_PRE_438_CONNECTION_PATTERN = re.compile(r"([a-zA-Z][\w+.-]*://[^:@\s/]+:)[^@\s/]+(?=@)")

# Slots of the pattern's own grammar. A structured product over these finds the shapes
# that matter here; random text essentially never generates a well-formed userinfo.
_LEADS = ["", "x", "9", "-", "=", '"', "//", "cfg = "]
_SCHEMES = ["", "a", "postgres", "mongodb+srv", "s" * 40]
_SEPARATORS = ["://", ":/", "//", ":"]
_USERS = ["user", "u.s-e+r", "", "u:v"]
_PASSWORDS = ["pw", "hunter2secret", "", "z" * 40]
_HOSTS = ["host", "h:5432/db", ""]

# Lines carrying several candidates at once, where substitution order matters: `sub`
# never revisits consumed text, so a match that lands earlier than the old one did can
# swallow a later candidate. That is how #434 turned a widening into a leak.
_MULTI_CANDIDATE_LINES = [
    "postgres://u:firstpassword@h1 mysql://v:secondpassword@h2",
    "postgres://u:firstpassword@h1mysql://v:secondpassword@h2",
    "key=abcdefghijklmnopx://user:hunter2@host",
    'api_key = "abcdef0123456789abcd" postgres://u:tailpassword@h',
    "://a:one@h postgres://b:two@h 9://c:three@h",
    "Authorization: Bearer abcdef0123456789abcd postgres://u:pw12345678@h",
]


def _all_lines():
    for lead, scheme, sep, user, pw, host in itertools.product(
        _LEADS, _SCHEMES, _SEPARATORS, _USERS, _PASSWORDS, _HOSTS
    ):
        yield f"{lead}{scheme}{sep}{user}:{pw}@{host}"
        yield f"{lead}{scheme}{sep}{user}:{pw}{host}"  # no `@` — must stay untouched
    yield from _MULTI_CANDIDATE_LINES


def _sweep_leftovers(oracle, *, exempt_code=False):
    """Drive every generated line through the CURRENT pipeline against ``oracle``.

    Returns ``(hits, leftovers)`` — how many spans the oracle matched on the RAW
    input (the sweep's own liveness signal) and every span it can still match in
    the emitted output (each one a secret the oracle's era redacted and this one
    did not). Factored out so the controls below exercise byte-identical sweep
    logic: a control that re-implements the loop proves nothing about the loop
    the real test runs.
    """
    hits = 0
    leftovers = []
    for line in _all_lines():
        out, _ = redaction._redact_secret_values(line, exempt_code=exempt_code)
        hits += len(oracle.findall(line))
        found = oracle.search(out)
        if found is not None:
            leftovers.append((line, found.group(0)))
    return hits, leftovers


@pytest.mark.parametrize("exempt_code", [False, True])
def test_no_password_the_old_pattern_redacted_stops_being_redacted(exempt_code):
    """The #438 anchoring may only widen coverage, never narrow it.

    For every line, the pre-#438 pattern must find NOTHING left in what the current
    pipeline emits: any span it could still match there is a password the old code
    would have replaced and the new code did not. Asserting on the leftover span rather
    than searching the output for the password TEXT is deliberate — the text can occur
    innocently elsewhere on the line (`v:` is a substring of `mongodb+srv://`), which
    makes a containment check report failures that are not leaks and, worse, report
    successes when a real leak happens to repeat harmless text.
    """
    hits, leftovers = _sweep_leftovers(_PRE_438_CONNECTION_PATTERN, exempt_code=exempt_code)
    assert not leftovers, f"{leftovers[0][1]!r} survived redaction of {leftovers[0][0]!r}"
    # The sweep is only evidence while the oracle still fires; without this a drifting
    # slot list would turn the whole test into a tautology that cannot fail.
    assert hits > 900, f"oracle matched only {hits} spans — sweep went vacuous"


def test_long_run_redaction_is_not_quadratic():
    """A long unbroken run must not blow the deadline (#438).

    Measured on the author's machine: 15.0 s before the fix, 7.3 ms after (the
    remaining cost is the other patterns' linear scans). The 2 s budget therefore
    sits ~275x above the passing time and ~7x below the failing one, so it is a
    liveness assertion rather than a timing-sensitive one.
    """
    text = "cfg = " + "a" * 100_000 + "x=" + "b" * 40
    start = time.perf_counter()
    redaction.redact_text(text)
    assert time.perf_counter() - start < 2.0


def test_connection_string_redaction_unchanged_for_scheme_led_urls():
    # The exact output for a scheme-led URL is byte-for-byte what it was before #438:
    # scheme, user, and host survive; only the password is replaced.
    out = redaction.redact_text("postgres://user:s3cr3tPassw0rd@db.example.com:5432/app")
    assert out == "postgres://user:[redacted: secret value]@db.example.com:5432/app"


# The userinfo classes are NEGATED, so their domain is every character except a handful.
# The sweep above cannot police that: its slots are built from ordinary URL text, so it
# only ever probes those classes at a few dozen member characters and a narrowing that
# excludes some unusual one passes it. Dropping `]` from the password class, for example,
# leaks `postgres://u:pass]word@h` while leaving every other test in this file green.
# These two walk the whole printable domain instead, so any character quietly removed
# from either class fails here.
_NON_MEMBERS = "@/"  # plus whitespace, handled below


@pytest.mark.parametrize(
    "char", [c for c in string.printable if c not in _NON_MEMBERS and not c.isspace()]
)
def test_password_class_covers_every_character_it_claims(char):
    # `[^@\s/]+` — a password may contain anything but `@`, whitespace, and `/`.
    assert redaction.redact_text(f"x://u:ab{char}cd@h") == "x://u:[redacted: secret value]@h"


@pytest.mark.parametrize(
    "char", [c for c in string.printable if c not in _NON_MEMBERS + ":" and not c.isspace()]
)
def test_username_class_covers_every_character_it_claims(char):
    # `[^:@\s/]+` — same, and additionally not `:`, which separates user from password.
    out = redaction.redact_text(f"x://a{char}b:secretpw@h")
    assert out == f"x://a{char}b:[redacted: secret value]@h"


@pytest.mark.parametrize(
    "text",
    [
        "://user:hunter2pass@host",  # no scheme at all
        "9://user:hunter2pass@host",  # a run with no letter to start a scheme match
        "-://user:hunter2pass@host",
    ],
)
def test_connection_string_password_redacted_without_a_scheme(text):
    # Anchoring at `://` widens what is recognized: userinfo whose `://` is not
    # reachable by a letter-led run is now redacted too. Deliberate, and the safe
    # direction for a fail-closed boundary.
    #
    # LOAD-BEARING, not an illustration — do not fold into the sweep above. That sweep
    # asks whether the OLD pattern can still match the output, so it is blind to a
    # narrowing whose leftover the old pattern cannot match either. Re-adding a
    # left-context requirement (`(?<=[\w+.-])`) is exactly that: it leaks, and only this
    # test and the labelled-marker one below fail.
    out = redaction.redact_text(text)
    assert "hunter2pass" not in out
    assert out.endswith("@host")


def test_connection_string_password_with_colons_redacted():
    # The password class admits `:` — it stops at `@`, `/`, or whitespace — so a
    # multi-segment password is redacted whole rather than up to its first colon.
    out = redaction.redact_text("postgres://user:p1:p2:p3@host")
    assert out == "postgres://user:[redacted: secret value]@host"


def test_two_connection_strings_on_one_line_both_redacted():
    # Substitution never revisits consumed text, so a second URL on the same line
    # must still be reached after the first match is replaced.
    out = redaction.redact_text("postgres://u:firstpassword@h1 mysql://v:secondpassword@h2")
    assert "firstpassword" not in out
    assert "secondpassword" not in out
    assert out.count("[redacted: secret value]") == 2


def test_password_after_a_labelled_marker_is_redacted():
    """A connection string whose scheme was eaten by an earlier marker (#438).

    The labelled-secret pattern runs first and its value class includes letters, so
    it can consume the scheme and leave a marker ending in `]` — after which the old
    scheme-led pattern could not match, and the password went out intact. Anchoring
    at `://` is what closes that; this is a leak fix, not only a widening.

    LOAD-BEARING, like the no-scheme case above and for the same reason: the sweep's
    oracle cannot see a narrowing it also fails to match, and a left-context requirement
    would reintroduce this leak while leaving that sweep green.
    """
    out = redaction.redact_text("key=abcdefghijklmnopx://user:hunter2@host")
    assert "hunter2" not in out


def test_redaction_of_an_already_redacted_connection_string_is_idempotent():
    # The marker contains spaces and the password class stops at whitespace, so a
    # second pass cannot re-match and mangle it.
    text = "postgres://user:[redacted: secret value]@host"
    assert redaction.redact_text(text) == text


# --- #439: the JWT pattern is quadratic on repeated-anchor text --------------
# Same shape as #438's scheme run: an unbounded greedy class ahead of a literal
# (`\.`) that never arrives. On text built from repeated `eyJ`, every anchor
# position scans the unbounded first segment to the end of the run and
# backtracks hunting a `.` that is never there — quadratic, and reachable from
# untrusted model output the same way #438 was (`redact_text` via `redact_tree`,
# both called from `orchestration.py`).
#
# One repeated-anchor (seed, reps) pair per LIVE pattern, keyed on the pattern's
# own compiled source (the `_WHITESPACE_ONLY_SOURCES` keying discipline) rather
# than list position, so a pattern added later fails the
# completeness guard below loudly instead of silently going unmeasured. ~80k
# chars is enough to demonstrate liveness for every pattern except JWT: on this
# machine `"eyJ"*26666` (~80k, the #439 issue's own example) measured 1.56s —
# under the 2.0s budget. A smaller JWT rep count (35000, ~2.8s old) was tried
# first and rejected: only ~1.4x over the 2.0s budget, so a moderately faster
# runner could pass the quadratic implementation and the test would stop
# demonstrating the property its name claims. `"eyJ"*90000` (270k chars) was
# measured instead (old pattern swapped in temporarily, never committed;
# three runs: 17.887s/17.220s/18.196s old, 0.0949s/0.0884s/0.0924s fixed) —
# the old pattern lands at ~8.6-9.1x over budget on every run, comfortably
# above an 8x floor, while the fixed pattern stays at ~21-23x margin under it.
# JWT's rep count is therefore raised to 90000 rather than left at the ~80k-char
# default the other patterns use.
_QUADRATIC_SEEDS: dict[str, tuple[str, int]] = {
    redaction.CONNECTION_STRING_PASSWORD_PATTERN.pattern: ("://x", 20000),
    redaction.CONNECTION_STRING_USERNAME_TOKEN_PATTERN.pattern: ("://x", 20000),
    r"AKIA[0-9A-Z]{16}": ("AKIA", 20000),
    r"gh[pousr]_[A-Za-z0-9_]{20,}": ("ghp_", 20000),
    r"xox[baprs]-[A-Za-z0-9-]{20,}": ("xoxb-", 16000),
    r"(?i)(Authorization:\s*Bearer\s+)[A-Za-z0-9._~+/=-]{16,}": (
        "Authorization: Bearer ",
        3500,
    ),
    redaction.LABELLED_VALUE_PATTERN.pattern: ("key=", 20000),
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----": ("-----BEGIN ", 7300),
    r"eyJ[A-Za-z0-9_-]{8,512}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}": ("eyJ", 90000),
    r"sk-proj-[A-Za-z0-9_-]{20,}": ("sk-proj-", 10000),
    r"sk-[A-Za-z0-9]{20,}": ("sk-", 26666),
    r"sk_(?:live|test)_[A-Za-z0-9]{16,}": ("sk_live_", 10000),
    r"AIza[0-9A-Za-z_-]{35,}": ("AIza", 20000),
}


def test_repeated_anchor_input_is_not_quadratic_for_any_pattern():
    """No live pattern may blow the deadline on a long run of its own anchor (#439).

    Mirrors #438's liveness-not-timing philosophy (:202-213): the 2.0s budget is an
    absolute ceiling, not a timing-sensitive one — for the one pattern this issue is
    about it sits far below the old pattern's measured failing time and far above the
    fixed pattern's measured passing time (see the module comment above for both).
    The anti-drift guard runs first so a pattern added later without a seed fails
    loudly here instead of silently going unmeasured by this test.
    """
    live = list(redaction.SECRET_VALUE_PATTERNS)
    missing = [p.pattern for p in live if p.pattern not in _QUADRATIC_SEEDS]
    assert not missing, f"no repeated-anchor seed for {missing}"

    for pattern in live:
        seed, reps = _QUADRATIC_SEEDS[pattern.pattern]
        text = seed * reps
        start = time.perf_counter()
        redaction._redact_secret_values(text)
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, (
            f"{pattern.pattern!r} took {elapsed:.3f}s on {len(text)}-char {seed!r}*{reps}"
        )


# The pre-#439 pattern, kept as an ORACLE (the discipline the #438/#440 oracles above
# follow, :123-129): the invariant that matters for a secret boundary is one-directional
# — bounding the first segment may recognize fewer FIRST-SEGMENT lengths, but every
# actual JWT shape it used to catch must still be caught — and the only way to test that
# is against the thing being replaced, so it is spelled out here rather than imported.
_PRE_439_JWT_PATTERN = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")

# Grammar slots for the sweep, built from the fix's own terms (#439): seg1 lengths
# straddle the new 512 (post-anchor) cap from both sides, seg2/seg3 vary independently
# since they stay unbounded, two charset variants probe the class's `-_` members, and
# leads/trailers probe the anchor in and out of surrounding text (including inside a
# connection string, `://u:`, since the JWT shape can appear as a userinfo credential).
_JWT_SEG1_LENGTHS = [8, 20, 36, 60, 120, 300, 511, 512]
_JWT_SEG2_LENGTHS = [8, 43, 300, 5000]
_JWT_SEG3_LENGTHS = [8, 43, 86, 342]
_JWT_CHARSETS = [string.ascii_letters + string.digits, string.ascii_letters + string.digits + "-_"]
_JWT_LEADS = ["", "Cookie: ", "x", "token=", "://u:"]
_JWT_TRAILERS = ["", " tail", "@host"]


def _jwt_segment(charset: str, length: int) -> str:
    """A segment of the given length, cycling through ``charset`` deterministically."""
    return "".join(charset[i % len(charset)] for i in range(length))


def _jwt_lines():
    for seg1_len, seg2_len, seg3_len, charset, lead, trailer in itertools.product(
        _JWT_SEG1_LENGTHS,
        _JWT_SEG2_LENGTHS,
        _JWT_SEG3_LENGTHS,
        _JWT_CHARSETS,
        _JWT_LEADS,
        _JWT_TRAILERS,
    ):
        seg1 = _jwt_segment(charset, seg1_len)
        seg2 = _jwt_segment(charset, seg2_len)
        seg3 = _jwt_segment(charset, seg3_len)
        yield f"{lead}eyJ{seg1}.{seg2}.{seg3}{trailer}"


def _jwt_sweep_leftovers(oracle, *, exempt_code=False):
    """Drive every generated JWT-grammar line through the CURRENT pipeline against ``oracle``.

    Same shape as `_sweep_leftovers` above, over the JWT grammar instead of the
    connection-string one: returns how many spans the oracle matched on the RAW input
    (the sweep's own liveness signal) and every span it can still match in the emitted
    output — each one a JWT the pre-#439 pattern redacted and this one did not. Factored
    out so the sensitivity control below exercises byte-identical sweep logic.
    """
    hits = 0
    leftovers = []
    for line in _jwt_lines():
        out, _ = redaction._redact_secret_values(line, exempt_code=exempt_code)
        hits += len(oracle.findall(line))
        found = oracle.search(out)
        if found is not None:
            leftovers.append((line, found.group(0)))
    return hits, leftovers


@pytest.mark.parametrize("exempt_code", [False, True])
def test_no_jwt_the_old_pattern_redacted_stops_being_redacted(exempt_code):
    """Bounding seg1 at 512 (#439) may only narrow how LONG a first segment may be —
    every JWT shape the pre-#439 pattern redacted must still be redacted.

    For every generated line, the pre-#439 pattern must find NOTHING left in what the
    current pipeline emits: any span it could still match there is a JWT the old code
    would have replaced and the new code did not. Asserted per-secret (the leftover
    SPAN, via `oracle.search`), not per-line, for the same reason as the #438 sweep:
    a per-line containment check cannot tell a real leak from harmless repeated text.
    """
    hits, leftovers = _jwt_sweep_leftovers(_PRE_439_JWT_PATTERN, exempt_code=exempt_code)
    assert not leftovers, f"{leftovers[0][1]!r} survived redaction of {leftovers[0][0]!r}"
    # The sweep is only evidence while the oracle still fires; without this a drifting
    # grammar would turn the whole test into a tautology that cannot fail. Every one of
    # the 3,840 generated lines carries exactly one JWT-shaped span (measured), so the
    # floor is set with headroom below that rather than at it.
    assert hits > 3000, f"oracle matched only {hits} spans — sweep went vacuous"


def test_the_jwt_sweep_can_actually_see_a_lost_redaction(monkeypatch):
    """Control: prove the sweep above is a working instrument, not a green rubber stamp.

    Narrows the LIVE JWT matcher to the `(?<![A-Za-z0-9_-])` left-context variant the
    fix's own comment rejects (redaction.py:248-250) — the alternative that also kills
    the quadratic blowup but costs coverage of an embedded `xxxeyJ…` match. The SAME
    sweep body must report leftovers against it, on the `lead="x"` lines.

    Substituting by the matcher's own compiled SOURCE, not list position, mirrors the
    :363-376 discipline. There is no module-level name for the JWT pattern to key on
    (unlike `CONNECTION_STRING_PASSWORD_PATTERN`) — the fix keeps it anonymous in the
    list, per the brief's file scope — so the source string IS the name here, the same
    keying `_WHITESPACE_ONLY_SOURCES` already uses for the other anonymous patterns.
    """
    narrowed = re.compile(
        r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    )
    live_jwt_source = r"eyJ[A-Za-z0-9_-]{8,512}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    patched = [
        narrowed if p.pattern == live_jwt_source else p for p in redaction.SECRET_VALUE_PATTERNS
    ]
    # The guard below is a diagnostic, not extra rigor: if the substitution silently
    # no-op'd (e.g. `live_jwt_source` drifted from the shipped pattern), the final
    # assertion would fail anyway — this just says why.
    assert patched != list(redaction.SECRET_VALUE_PATTERNS), "the control narrowed nothing"
    monkeypatch.setattr(redaction, "SECRET_VALUE_PATTERNS", patched)
    _, leftovers = _jwt_sweep_leftovers(_PRE_439_JWT_PATTERN)
    assert leftovers, "the sweep reported no loss against a deliberately narrowed matcher"


# --- #439: boundary characterizations, counted in the regex's OWN terms ------
# `{8,512}` counts characters AFTER the literal `eyJ` anchor, so the longest matchable
# first segment is 515 total chars (`eyJ` + 512 post-anchor). Both cases below count
# post-anchor, per the brief: 513 is the first unmatchable length with no internal
# `eyJ` to retry from, and 513+ WITH an internal `eyJ` still matches — at the LATER
# anchor — leaving the true prefix as unredacted residue ahead of the marker.


def test_first_segment_over_512_post_anchor_chars_is_not_redacted():
    # 513 post-anchor chars, no internal `eyJ` anywhere in the run: the outer anchor's
    # first segment can never reach the `.` within the 512 cap, and there is no later
    # `eyJ` for the engine to retry from, so nothing matches at all. Byte-identical
    # output, not just "no marker" — the accepted x5c-certificate-chain boundary #439's
    # comment documents (redaction.py:248-250).
    text = "eyJ" + "a" * 513 + "." + "b" * 8 + "." + "c" * 8
    assert redaction.redact_text(text) == text


def test_first_segment_at_512_post_anchor_chars_is_redacted():
    # The boundary from the matching side: 512 is the last post-anchor length that
    # still fits `{8,512}`.
    text = "eyJ" + "a" * 512 + "." + "b" * 8 + "." + "c" * 8
    out = redaction.redact_text(text)
    assert out == "[redacted: secret value]"
    # The marker contains no `eyJ...\...\...` shape, so a second pass cannot re-match.
    assert redaction.redact_text(out) == out


def test_over_512_run_with_an_internal_eyj_matches_at_the_later_anchor():
    """A first segment over the 512 cap, but carrying its own `eyJ` 6 chars in.

    The OUTER `eyJ` (position 0) can never reach a `.` within its 512-char budget —
    the run to the first `.` is 516 chars, over the cap — so that anchor fails, exactly
    like the no-internal-`eyJ` case above. But the embedded `eyJ` at position 6 (an
    internal-`eyJ` offset well past the boundary's own ">=3 chars from position 0")
    starts its OWN attempt, whose first segment is only 510 post-anchor chars — under
    the cap — so it succeeds. The true prefix (`eyJaaa`, the outer anchor plus the
    three characters before the embedded one) is not part of that match, so it survives
    as leading residue ahead of the marker: exactly the mid-header match #439's comment
    describes as the accepted cost of the 512 boundary, pinned here with its exact
    output rather than left as a claim.

    This is also the canonical case #446's leading check exists for: the match is a
    whole-match candidate (the JWT pattern has no group), and the character right before
    its start (`a`, the last of the `eyJaaa` residue) is in the leading-continuation
    class, so the marker now says so rather than claiming the JWT was matched from its
    true start.
    """
    text = "eyJ" + "aaa" + "eyJ" + "a" * 510 + "." + "b" * 8 + "." + "c" * 8
    out = redaction.redact_text(text)
    assert out == "eyJaaa[redacted: possibly partial secret value]"
    # The residue (`eyJaaa`) is 6 chars, short of the {8,512} minimum, and the marker
    # itself carries no `.` to re-anchor on, so a second pass leaves it unchanged.
    assert redaction.redact_text(out) == out


# --- #440: a credential in ANY userinfo position -----------------------------
# The matcher required a NON-EMPTY username before the password, so `://:pw@host`
# — the canonical Redis URL, since Redis had no usernames before ACLs in 6.0 —
# shipped its password verbatim. Widening that class to `*` closes it.
#
# Two test jobs, deliberately kept apart, because ONE of them cannot do the other's
# work. The differential sweep below guards the OLD branch: nothing the previous
# matcher redacted may stop being redacted. It is structurally incapable of policing
# the branch this change ADDS — its oracle spells the old `+`, so it never matches an
# empty username at all (measured: 0 oracle hits across every empty-username line in
# `_all_lines()`, against 360 on the named ones). Shipping only the sweep would have
# looked like coverage of the fix while being vacuous on exactly the fix. The new
# branch therefore gets EXACT-OUTPUT contract tests instead, which also catch the
# defect an oracle sweep cannot see at all: a PARTIAL replacement, whose marker
# contains spaces and so destroys the very URL syntax the oracle needs to match.

# The pre-#440 matcher, kept as an ORACLE for the branch it did cover — spelled out
# rather than imported, for the reason the #438 oracle above is.
_PRE_440_CONNECTION_PATTERN = re.compile(r"(://[^:@\s/]+:)[^@\s/]+(?=@)")


@pytest.mark.parametrize("exempt_code", [False, True])
def test_no_password_the_pre_440_pattern_redacted_stops_being_redacted(exempt_code):
    """Widening the username class may only ADD coverage (#440).

    The repo's own history is that a widening turns a false negative into a LEAK:
    `re.sub` never revisits consumed text, so a match landing earlier than before can
    swallow a later candidate (#432, #434). Here it cannot — the widened match's value
    class excludes `/`, so it can never contain the `//` of a later `://` — and this
    sweep is the empirical check on that reasoning.
    """
    hits, leftovers = _sweep_leftovers(_PRE_440_CONNECTION_PATTERN, exempt_code=exempt_code)
    assert not leftovers, f"{leftovers[0][1]!r} survived redaction of {leftovers[0][0]!r}"
    assert hits > 900, f"oracle matched only {hits} spans — sweep went vacuous"


def test_the_differential_sweep_can_actually_see_a_lost_redaction(monkeypatch):
    """Control: prove the sweep above is a working instrument, not a green rubber stamp.

    Narrowing the live matcher — re-adding the left-context requirement #438 removed —
    must make the SAME sweep body report leftovers. Without this, a sweep that had gone
    vacuous and a sweep over a correct matcher are indistinguishable.

    Scope, stated because a passing control invites over-reading (and because the
    complementary experiment fails): this proves sensitivity to a narrowing that drops
    WHOLE candidates. It does NOT prove sensitivity to a narrowed character CLASS —
    dropping `]` from the password class leaks `postgres://u:pass]word@h` and this sweep
    stays green, because the leftover then contains a marker whose spaces the oracle
    cannot match across. That class is covered only by the printable-domain walks above,
    which is why those are not redundant with this.
    """
    narrowed = re.compile(r"(?<=[\w])(://[^:@\s/]*:)[^@\s/]+(?=@)")
    target = redaction.CONNECTION_STRING_PASSWORD_PATTERN
    patched = [narrowed if p is target else p for p in redaction.SECRET_VALUE_PATTERNS]
    # Substituting by NAME, not by list position. An earlier version keyed on
    # `SECRET_VALUE_PATTERNS[-1]`; appending a matcher pointed it at the wrong pattern,
    # and it failed loudly — but only by luck, because narrowing THAT matcher happened to
    # produce no leftovers. Had the appended pattern been one whose narrowing also stranded
    # a password, the control would have gone green while exercising a matcher this sweep
    # is not about. A name cannot drift onto the wrong pattern that way.
    #
    # The guard below is a diagnostic, not extra rigor: if the substitution silently no-op'd,
    # the final assertion would fail anyway — this just says why.
    assert patched != list(redaction.SECRET_VALUE_PATTERNS), "the control narrowed nothing"
    monkeypatch.setattr(redaction, "SECRET_VALUE_PATTERNS", patched)
    _, leftovers = _sweep_leftovers(_PRE_440_CONNECTION_PATTERN)
    assert leftovers, "the sweep reported no loss against a deliberately narrowed matcher"


# Exact-output cases for the branch #440 adds. Exact rather than `not in`, so a
# partial or over-broad replacement fails as loudly as a missed one.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The canonical Redis URL from the issue.
        (
            "redis://:onlypass@localhost:6379",
            "redis://:[redacted: secret value]@localhost:6379",
        ),
        # A scheme-led URL, for symmetry with the named-username case.
        (
            "DATABASE_URL=rediss://:s3cr3tPassw0rd@db.example.com:6380/0",
            "DATABASE_URL=rediss://:[redacted: secret value]@db.example.com:6380/0",
        ),
        # No scheme at all — the `://` anchor is what the match hangs on (#438).
        ("://:hunter2pass@host", "://:[redacted: secret value]@host"),
        # A scheme the labelled pattern already ate, leaving a marker (#438's leak). The
        # labelled value stops at the first `:` (not in `_VALUE_CHARS`), so its follower is
        # that `:` — not a safe terminator (#446), since a `:` can legitimately continue a
        # LABELLED value elsewhere (`key=user:p1:p2:p3`) even though here it is a URL scheme
        # separator the trailing check cannot tell apart from that.
        (
            "key=abcdefghijklmnopx://:hunter2@host",
            "key=[redacted: possibly partial secret value]://:[redacted: secret value]@host",
        ),
        # Two empty-username candidates: `sub` must reach the second.
        (
            "a://:firstpass@h1 b://:secondpass@h2",
            "a://:[redacted: secret value]@h1 b://:[redacted: secret value]@h2",
        ),
        # Mixed shapes on one line, in both orders — the empty-username match must not
        # swallow the named one, nor be swallowed by it.
        (
            "redis://:firstpass@h1 postgres://u:secondpass@h2",
            "redis://:[redacted: secret value]@h1 postgres://u:[redacted: secret value]@h2",
        ),
        (
            "postgres://u:firstpass@h1 redis://:secondpass@h2",
            "postgres://u:[redacted: secret value]@h1 redis://:[redacted: secret value]@h2",
        ),
        # A multi-segment password: the class admits `:`, so it goes whole.
        ("redis://:p1:p2:p3@host", "redis://:[redacted: secret value]@host"),
    ],
)
def test_empty_username_connection_string_exact_output(text, expected):
    assert redaction.redact_text(text) == expected


# The empty-username arm admits everything the named arm does EXCEPT `?` and `#`.
_EMPTY_USER_NON_MEMBERS = _NON_MEMBERS + "?#"


@pytest.mark.parametrize(
    "char",
    [c for c in string.printable if c not in _EMPTY_USER_NON_MEMBERS and not c.isspace()],
)
def test_empty_username_password_class_covers_every_character_it_claims(char):
    # The password run is reachable through a SECOND route once the username may be
    # empty. A narrowing on that route alone would leave the named-username walk above
    # green, so the domain is walked here too — minus the two characters that route
    # deliberately excludes, which the next test pins from the other side.
    assert redaction.redact_text(f"x://:ab{char}cd@h") == "x://:[redacted: secret value]@h"


@pytest.mark.parametrize("char", ["?", "#"])
def test_empty_username_run_stops_at_a_query_or_fragment(char):
    """`://:` is also an empty host with a port, so this arm must stop at `?`/`#`.

    `custom://:8080?email=a@b` has no userinfo at all — the `@` sits in the query — and
    admitting these characters masked it as a credential, hiding the host. Per RFC 3986
    a raw `?`/`#` cannot appear in userinfo, so an `@` after one never delimits one.

    The NAMED arm keeps admitting both (pinned by the walk above): narrowing it would
    stop redacting a password containing a raw `?`, which is a real loss, and this
    change is only allowed to widen.
    """
    assert redaction.redact_text(f"x://:ab{char}cd@h") == f"x://:ab{char}cd@h"
    assert redaction.redact_text(f"custom://:8080{char}email=a@b") == (
        f"custom://:8080{char}email=a@b"
    )
    # ...while the same shape WITH a username stays exactly as it was before #440.
    assert redaction.redact_text(f"x://u:ab{char}cd@h") == "x://u:[redacted: secret value]@h"


@pytest.mark.parametrize(
    "text",
    [
        "redis://:@localhost:6379",  # empty password — no secret to redact
        "redis://user:@localhost:6379",  # ditto, with a username
        "file:///etc/passwd",  # `://` then `/`, which the username class excludes
        "http://[::1]:6379/db",  # IPv6 host, no userinfo
        "https://example.com:8080/path",  # host:port, no `@`
        "ssh://git@github.com/user/repo.git",  # userinfo with no password field
        "git@github.com:user/repo.git",  # SCP-style, no `://`
    ],
)
def test_widened_username_class_leaves_non_credentials_alone(text):
    assert redaction.redact_text(text) == text


def test_empty_username_redaction_is_idempotent():
    text = "redis://:[redacted: secret value]@host"
    assert redaction.redact_text(text) == text


def test_widening_masks_a_regex_literal_that_looks_like_userinfo():
    """Characterization, not an endorsement: the accepted cost of the widening.

    The connection-string matcher gets no code-reference exemption — that applies to
    `LABELLED_VALUE_PATTERN` alone (#421/#431) — so source that literally spells
    password-only userinfo is masked. Recorded here so the tradeoff is a decision with
    a test on it rather than a surprise, and so anyone tempted to "fix" it sees that
    weakening a credential matcher is the thing being traded away. The same class
    already existed for the named-username form (`sect://a:2@1`).
    """
    assert redaction.redact_text('pattern = r"://:.+@"') == (
        'pattern = r"://:[redacted: secret value]@"'
    )


# --- #440: a token in the USERNAME slot with an empty password ---------------
# The password matcher above preserves the username, so a credential stored THERE
# left the machine intact: `https://<token>:@host` shipped the token verbatim.
#
# Only the `username:@` shape is matched, and only at 16+ characters. Both limits
# are load-bearing, and both were set by what the alternatives destroy:
#
#   * A trailing bare `:` means a password field that is present and deliberately
#     empty — the token-as-username idiom (Stripe's `https://sk_test_x:@api...`).
#     But it does NOT by itself imply a token: RFC 1738 spells `ftp://foo:@host`
#     as username `foo` with an empty password, so an UNGATED match on this shape
#     masks `ftp://anonymous:@host` and `postgres://readonly:@db/app`.
#   * The length gate is what separates those. 16 is not a new constant — it is
#     already this file's credential threshold, in `LABELLED_VALUE_PATTERN`.
#
# The BARE `://token@host` form is deliberately NOT matched, at any threshold.
# Length cannot establish credential semantics in that position: a 16+ gate masks
# `git+ssh://deployment-automation@git.example.com/repo` (a documented pip VCS
# URL), `ssh://continuous-integration@build.example.com`, `docker://prometheus-
# operator@sha256:...` (a NAME@DIGEST ref, not userinfo at all), and the email
# `https://first.last+alerts@example.com` — every one an identity, none a secret.
# Raising the threshold only changes which identities get destroyed. That position
# is already covered for every credential shape this module RECOGNIZES — the
# vendor patterns match `ghp_`/`AKIA`/`sk-`/`xoxb-` there regardless of position —
# so the residue is a generic opaque string, which is exactly what cannot be told
# apart from a long username. Leaving it is this module's documented best-effort
# boundary, not an oversight.
#
# `?` and `#` are excluded from the class though the password class admits them.
# The roles differ: here the run is the text being REPLACED and must stop at the
# end of the authority, or a query carrying an `@` gets masked as userinfo —
# `https://example.com?email=first.last+alerts@example.org` collapses to
# `https://[redacted: secret value]@example.org`, hiding the host. A password, by
# contrast, may legitimately contain `?` and `#`, so narrowing THAT class would
# lose real coverage.

_TOKEN_16 = "s3cr3tOpaqueToke"  # exactly 16 chars — the gate's lower boundary
_TOKEN_15 = "s3cr3tOpaqueTok"  # 15 — must NOT match


@pytest.mark.parametrize("exempt_code", [False, True])
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The boundary itself, from both sides. This shape's replaced span always ends
        # right at the literal `:` of its own `:@` closing lookahead (#446): that `:` is
        # not a safe terminator (it can legitimately continue a LABELLED value elsewhere,
        # `key=user:p1:p2:p3`), so every match of THIS pattern gets the partial marker even
        # though the token itself is always matched whole — a known, accepted
        # over-caution of a trailing check that cannot see per-pattern guarantees.
        (
            f"https://{_TOKEN_16}:@host/path",
            "https://[redacted: possibly partial secret value]:@host/path",
        ),
        (f"https://{_TOKEN_15}:@host/path", f"https://{_TOKEN_15}:@host/path"),
        # A longer opaque token.
        (
            "https://s3cr3tOpaqueToken123456789:@api.example.com/v1",
            "https://[redacted: possibly partial secret value]:@api.example.com/v1",
        ),
        # Percent-encoding and punctuation inside the admitted class.
        (
            "https://tok%2Fen.with-punct_123~+=:@host",
            "https://[redacted: possibly partial secret value]:@host",
        ),
        # No scheme at all — the `://` anchor carries the match (#438).
        (f"://{_TOKEN_16}:@host", "://[redacted: possibly partial secret value]:@host"),
        # A scheme already eaten by the labelled pattern's marker. Both markers flip: the
        # labelled value also stops at a `:` (the scheme separator), for the same reason.
        (
            f"key=abcdefghijklmnopx://{_TOKEN_16}:@host",
            "key=[redacted: possibly partial secret value]"
            "://[redacted: possibly partial secret value]:@host",
        ),
        # Two candidates of this shape: `sub` must reach the second.
        (
            f"https://{_TOKEN_16}:@h1 https://s3cr3tOtherToken99:@h2",
            "https://[redacted: possibly partial secret value]:@h1 "
            "https://[redacted: possibly partial secret value]:@h2",
        ),
        # Mixed with the existing password shape, in BOTH orders — neither match may
        # swallow the other. Only the username-token marker flips: the password shape's
        # replaced span ends at `@`, a safe terminator, so it keeps the plain marker.
        (
            f"https://{_TOKEN_16}:@h1 postgres://u:s3cr3tPass@h2",
            "https://[redacted: possibly partial secret value]:@h1 "
            "postgres://u:[redacted: secret value]@h2",
        ),
        (
            f"postgres://u:s3cr3tPass@h1 https://{_TOKEN_16}:@h2",
            "postgres://u:[redacted: secret value]@h1 "
            "https://[redacted: possibly partial secret value]:@h2",
        ),
        # All three userinfo shapes on one line. Only the username-token marker (`:@`
        # follower) flips; both password-shape markers end at `@` and stay plain.
        (
            f"https://{_TOKEN_16}:@h1 redis://:s3cr3tPw@h2 postgres://u:s3cr3tPass@h3",
            "https://[redacted: possibly partial secret value]:@h1 "
            "redis://:[redacted: secret value]@h2 "
            "postgres://u:[redacted: secret value]@h3",
        ),
    ],
)
def test_username_token_with_empty_password_exact_output(text, expected, exempt_code):
    # Both exemption modes: the code-reference exemption is keyed to
    # LABELLED_VALUE_PATTERN alone, so this matcher must behave identically in each.
    out, _ = redaction._redact_secret_values(text, exempt_code=exempt_code)
    assert out == expected


@pytest.mark.parametrize("exempt_code", [False, True])
@pytest.mark.parametrize(
    "text",
    [
        # Short empty-password identities — RFC 1738's own reading of this syntax.
        "ftp://anonymous:@host/pub",
        "https://alice:@example.com",
        "postgres://readonly:@db/app",
        # Bare-username identities, deliberately out of scope at any length.
        "ssh://git@github.com/user/repo.git",
        "git+ssh://deployment-automation@git.example.com/repo",
        "ssh://continuous-integration@build.example.com/x",
        "https://first.last+alerts@example.com",
        # A Docker NAME@DIGEST reference, which is not userinfo at all.
        "docker://prometheus-operator@sha256:abcdef0123456789abcdef",
        # A query string carrying an email — the `?`/`#` exclusion is what saves it.
        "https://example.com?email=first.last+alerts@example.org",
        "https://example.com#anchor=a.b.c.d.e.f.g.h.i.j@x",
        # A long host with no userinfo at all.
        "https://a-very-long-hostname-indeed.example.com:8443/path",
        # A VCS revision after a `/`, which the class stops at.
        "git+ssh://git@example.com/org/repo.git@a1b2c3d4e5f6a7b8",
    ],
)
def test_username_token_matcher_leaves_identities_alone(text, exempt_code):
    out, redacted = redaction._redact_secret_values(text, exempt_code=exempt_code)
    assert out == text
    assert redacted is False


@pytest.mark.parametrize("char", ["@", " ", "\t", "/", "?", "#"])
def test_username_token_run_is_terminated_by_every_authority_boundary(char):
    # Each of these must break the 16+ run, so the padded value never reaches the
    # gate as one token and this matcher does not fire. `?`/`#` are the load-bearing
    # pair: without them a query string carrying an `@` is masked as userinfo.
    text = f"https://abcdefgh{char}ijklmnopq:@host"
    assert redaction.redact_text(text) == text


def test_a_colon_in_the_run_makes_it_an_ordinary_password_url():
    # `:` also terminates the run, but it is not a mere non-match: it turns the text
    # into `user:password@host`, which the EXISTING matcher redacts. Pinned with its
    # real output rather than folded into the sweep above, so that a regression which
    # silently stopped redacting here could not hide behind an "unchanged" assertion.
    assert redaction.redact_text("https://abcdefgh:ijklmnopq:@host") == (
        "https://abcdefgh:[redacted: secret value]@host"
    )


def test_username_token_gate_counts_serialized_characters_not_decoded_ones():
    """Characterization of a known, accepted limit of the 16-character gate.

    The quantifier counts the characters as written, so percent-encoding inflates a
    short identity past the gate: `anonymous` is 9 and stays, but its `%HH` encoding is
    27 and is masked. Decoding first would need the matcher to become a callable, which
    is a disproportionate amount of machinery for an input this rare, and the error runs
    in the fail-closed direction — an identity is over-masked, no credential is exposed.
    Pinned so the semantics are a recorded decision rather than an accident, and so that
    anyone who does add decoding has a test that tells them what they changed.
    """
    assert redaction.redact_text("ftp://anonymous:@host/pub") == "ftp://anonymous:@host/pub"
    # This shape's marker always gets the partial variant (#446): its replaced span ends
    # right at the `:@` closing lookahead's `:`, which is not a safe terminator.
    assert redaction.redact_text("ftp://%61%6E%6F%6E%79%6D%6F%75%73:@host/pub") == (
        "ftp://[redacted: possibly partial secret value]:@host/pub"
    )


def test_username_token_redaction_is_idempotent():
    # The marker's own `:` truncates the run to `[redacted` (9 chars), under the
    # gate, so a second pass cannot re-match and nest a marker inside a marker.
    once = redaction.redact_text(f"https://{_TOKEN_16}:@h1 redis://:s3cr3tPw@h2")
    assert redaction.redact_text(once) == once
    # The username-token shape's marker (`:@` follower) is partial (#446); the
    # empty-username password shape's marker (`@` follower) stays plain.
    assert once.count("[redacted: possibly partial secret value]") == 1
    assert once.count("[redacted: secret value]") == 1


def test_username_token_redacted_in_a_source_diff_and_path_recorded():
    diff = "\n".join(
        [
            "diff --git a/app.py b/app.py",
            "+++ b/app.py",
            f'+CLIENT = Api("https://{_TOKEN_16}:@api.example.com")',
        ]
    )
    out, paths = redaction.redact(diff)
    assert (
        '+CLIENT = Api("https://[redacted: possibly partial secret value]:@api.example.com")' in out
    )
    assert paths == ["app.py"]


def test_identity_urls_in_a_source_diff_record_no_redaction():
    # The blast radius argument: a false positive here would mask source, and any
    # inline mask makes review coverage partial (#319/#431). So the identity cases
    # must leave `redacted_paths` EMPTY, not merely leave the text alone.
    diff = "\n".join(
        [
            "diff --git a/app.py b/app.py",
            "+++ b/app.py",
            '+REPO = "git+ssh://deployment-automation@git.example.com/repo"',
            '+IMG = "docker://prometheus-operator@sha256:abcdef0123456789abcdef"',
            '+FTP = "ftp://anonymous:@host/pub"',
        ]
    )
    out, paths = redaction.redact(diff)
    assert "[redacted: secret value]" not in out
    assert paths == []


# --- #443: an earlier marker must not fragment a connection-string credential -
# `SECRET_VALUE_PATTERNS` is applied in order, one `re.sub` pass per pattern, and `sub`
# never revisits consumed text. When a substring-oriented matcher fired INSIDE a
# connection-string password, its marker — which contains a space and a colon — split the
# credential in two, and the connection-string runs stop at whitespace, so they could no
# longer match it. The tail shipped intact, behind a marker claiming the value was handled:
#
#     redis://u:token=s3cr3tvalue0123456789%2Ftailsegment@host
#     -> redis://u:token=[redacted: secret value]%2Ftailsegment@host
#
# #443 fixed that by running both connection-string matchers FIRST, so the complete userinfo
# span was consumed before anything could fragment it — a property of the ordered pipeline
# rather than of any one matcher, which is why no regex changed. #445 then replaced the
# ordering itself: the engine collects every pattern's candidate spans against the ORIGINAL
# text and merges the overlapping ones, so no matcher can consume text out from under another
# at all. The sweep BODY below is unchanged and still guards the same property — the loop, the
# corpus, and the assertion are as #443 wrote them; only its signature grew an `engine`
# parameter for the conversion below. What changed is that the property now holds for reasons
# that do not depend on this list's order, which is why the control substitutes the pre-#445
# ENGINE rather than the pre-#443 ordering.
#
# The instrument is a SENTINEL sweep, not the old-pattern oracle the #438/#440 sweeps use.
# That oracle is structurally incapable of seeing this bug: the regexes are unchanged, so an
# old-pattern oracle and the live matcher agree by construction, and — the deeper reason —
# the old pipeline cannot recognize its OWN partially redacted output, because the marker
# destroys the syntax the oracle needs in order to match. Every credential generated below
# therefore carries a unique tail that no matcher recognizes on its own; the assertion is
# that the tail disappears. The pre-#445 sequential engine, driven over the pre-#443 ordering
# — the exact pipeline that shipped the bug — is kept as the control.

_CS_MATCHERS = (
    redaction.CONNECTION_STRING_PASSWORD_PATTERN,
    redaction.CONNECTION_STRING_USERNAME_TOKEN_PATTERN,
)


def _pre_443_order():
    """The shipped list with the connection-string matchers moved back to the END.

    DERIVED from the live list rather than spelled out, so a matcher added later is
    carried into the control automatically and the control keeps testing the one thing
    #443 changed — the position of these two names. Spelling the old list out would
    freeze it at today's membership and quietly stop covering anything added after.
    """
    return [p for p in redaction.SECRET_VALUE_PATTERNS if p not in _CS_MATCHERS] + list(
        _CS_MATCHERS
    )


def _sequential_engine(patterns):
    """The PRE-#445 engine, kept as an ORACLE: one `re.sub` pass per pattern, each running
    over the PREVIOUS pass's output.

    Spelled out here rather than imported, the discipline every oracle in this file follows
    (:123-129): the thing being replaced cannot also be the thing that judges the
    replacement. The live engine is order-independent by construction — it computes each
    pattern's candidate spans against the original text and merges them — so monkeypatching
    `SECRET_VALUE_PATTERNS` into a different order can no longer make the pipeline strand
    anything, and a control that did so would report "no leaks" for the wrong reason. This is
    an engine that CAN still strand a tail.

    Returns a callable with `_redact_secret_values`' exact signature, so a sweep body takes it
    unchanged.
    """

    def engine(line: str, *, exempt_code: bool = False) -> tuple[str, bool]:
        redacted = False
        out = line
        for pattern in patterns:
            exempting = exempt_code and pattern is redaction.LABELLED_VALUE_PATTERN

            def repl(match: re.Match, *, exempting: bool = exempting) -> str:
                nonlocal redacted
                if exempting and redaction._is_code_reference(match):
                    return match.group(0)
                redacted = True
                if match.lastindex:
                    return f"{match.group(1)}[redacted: secret value]"
                return "[redacted: secret value]"

            out = pattern.sub(repl, out)
        return out, redacted

    return engine


# A tail that no matcher recognizes on its own (pinned below), containing no character a
# URI authority forbids. `%` is outside `_VALUE_CHARS`, so the labelled run cannot extend
# across it either — the sentinel can only leave the machine as a stranded fragment.
_TAIL_SENTINEL = "%2FTAILSENTINEL"

# One payload per matcher that can actually fire inside userinfo. Completeness over the
# LIVE pattern list is enforced below rather than trusted: a hand-maintained payload list
# silently stops covering a matcher added after it was written, which is exactly how a
# sweep goes vacuous while staying green (#438/#439).
_IN_AUTHORITY_PAYLOADS = [
    "token=s3cr3tvalue0123456789",
    "password:s3cr3tvalue0123456789",
    "ghp_" + "a" * 20,
    "xoxb-" + "a" * 20,
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "eyJabcdefgh.abcdefgh.abcdefgh",
    "sk-" + "a" * 20,
    "sk-proj-" + "a" * 20,
    "sk_live_" + "a" * 16,
    "AIza" + "a" * 35,
]

# The matchers exempt from the payload set, because a URI authority cannot contain raw
# whitespace — both connection-string runs exclude `\s` — so a matcher whose language
# requires whitespace can never fire inside userinfo and there is no collision to sweep for.
#
# An explicit ALLOWLIST keyed on the compiled source, deliberately, rather than a predicate
# that decides membership by probing. A probe can only show that ONE string this matcher
# accepts contains whitespace; it cannot show that every string does, so an alternation
# admitting a whitespace-free credential would be exempted on the strength of its other
# branch and silently lose its payload. Keying on the source means a new matcher is never
# swept in by accident and an edit to either of these fails loudly, which is the point:
# both are claims about the matcher's whole language and deserve a deliberate re-check.
_WHITESPACE_ONLY_SOURCES = frozenset(
    {
        # Assembled rather than written out: the literal is the `detect-private-key`
        # pre-commit hook's trigger substring, and spelling it whole fails the hook.
        "-----BEGIN " + r"[A-Z ]*PRIVATE KEY-----",
        r"(?i)(Authorization:\s*Bearer\s+)[A-Za-z0-9._~+/=-]{16,}",
    }
)

# One string each exempt matcher accepts, used to prove the classification below.
_WHITESPACE_REQUIRING_PROBES = [
    "-----BEGIN " + "PRIVATE KEY-----",
    "Authorization: Bearer abcdef0123456789abcd",
]


def _requires_whitespace(pattern) -> bool:
    """Whether this matcher is on the whitespace-only allowlist above."""
    return pattern.pattern in _WHITESPACE_ONLY_SOURCES


_COLLISION_LEADS = ["", "cfg = ", "DATABASE_URL=", 'api_key = "abcdef0123456789abcd" ']
_COLLISION_SCHEMES = ["", "redis", "mongodb+srv", "9"]
_COLLISION_HOSTS = ["host", "h:5432/db", ""]
# All three userinfo shapes: named username, empty username (#440), and a token in the
# username slot with an empty password (#440). The bug reaches every one of them.
_COLLISION_SHAPES = ["{s}://user:{c}@{h}", "{s}://:{c}@{h}", "{s}://{c}:@{h}"]
_COLLISION_TRAILERS = ["", " postgres://u:secondpassword@h2", "?x=1"]


def _collision_lines():
    for lead, scheme, host, shape, payload, trailer in itertools.product(
        _COLLISION_LEADS,
        _COLLISION_SCHEMES,
        _COLLISION_HOSTS,
        _COLLISION_SHAPES,
        _IN_AUTHORITY_PAYLOADS,
        _COLLISION_TRAILERS,
    ):
        yield lead + shape.format(s=scheme, c=payload + _TAIL_SENTINEL, h=host) + trailer


def _sentinel_leaks(*, exempt_code, engine=None):
    """Every generated line whose emitted output still carries the tail sentinel.

    ``engine`` selects the pipeline under test and defaults to the LIVE redactor — resolved
    at call time rather than bound as a default argument, so the default names whatever
    `redaction._redact_secret_values` is when the sweep runs. The control below passes
    `_sequential_engine(...)`, so both exercise byte-identical sweep logic: a control that
    re-implements the loop proves nothing about the loop the real test runs.
    """
    run = engine if engine is not None else redaction._redact_secret_values
    leaks = []
    for line in _collision_lines():
        out, _ = run(line, exempt_code=exempt_code)
        if _TAIL_SENTINEL in out:
            leaks.append((line, out))
    return leaks


@pytest.mark.parametrize("exempt_code", [False, True])
def test_no_marker_strands_a_connection_string_credential(exempt_code):
    """No earlier matcher may leave part of a userinfo credential behind (#443)."""
    leaks = _sentinel_leaks(exempt_code=exempt_code)
    assert not leaks, f"{leaks[0][1]!r} still carries the tail of {leaks[0][0]!r}"


@pytest.mark.parametrize("exempt_code", [False, True])
def test_the_collision_sweep_can_actually_see_a_stranded_tail(exempt_code):
    """Control: the pre-#445 pipeline must make the SAME sweep body report leaks.

    Without this, a sweep whose payloads had drifted out of collision range and a sweep over
    a correct pipeline are indistinguishable — both simply report nothing.

    The instrument changed with #445, and the reason is the point of the control. This used to
    monkeypatch the pre-#443 ORDER onto the live engine; under span merging that reports no
    leaks, because the merged span set is a per-pattern union over the original text and so
    cannot depend on the list's order. A control left in that form would have gone green while
    testing nothing. What can still strand a tail is the pre-#445 sequential engine — driven
    over the pre-#443 ordering, which is exactly the pipeline that shipped the bug — so the
    substitution moved from the ordering to the ENGINE.
    """
    leaks = _sentinel_leaks(exempt_code=exempt_code, engine=_sequential_engine(_pre_443_order()))
    assert leaks, "the sweep reported no stranded tail against the pre-#443/#445 pipeline"


def test_every_matcher_that_can_fire_inside_userinfo_has_a_collision_payload():
    """The sweep's anti-drift guard, over the LIVE pattern list.

    A matcher added ahead of the connection-string pair reopens #443 for its own shape, and
    a hardcoded payload list cannot know it exists — every test would stay green. So every
    pattern must either be exercised by a payload or be exempt for the stated reason.
    """
    uncovered = [
        pattern.pattern
        for pattern in redaction.SECRET_VALUE_PATTERNS
        if pattern not in _CS_MATCHERS
        and not _requires_whitespace(pattern)
        and not any(pattern.search(payload) for payload in _IN_AUTHORITY_PAYLOADS)
    ]
    assert not uncovered, f"no collision payload exercises {uncovered}"


def test_the_whitespace_only_exemption_is_earned_by_every_member():
    """Prove the allowlist above rather than trusting it.

    Each exempt matcher must (1) still be present in the shipped list, (2) accept a probe
    that contains whitespace, (3) reject that same probe once the whitespace is removed —
    which is what "requires whitespace" means operationally — and (4) have a probe the
    connection-string matcher cannot span when it is placed in the password slot, which is
    the property the exemption actually rests on.

    (4) is asserted against the PROBE, not the pattern source. An earlier version of this
    test interpolated `pattern.pattern` into the URI, so it asked whether a regex's source
    text looked like userinfo — a question with no bearing on the exemption, and one that
    happened to answer "no" for unrelated reasons.
    """
    exempt = [p for p in redaction.SECRET_VALUE_PATTERNS if _requires_whitespace(p)]
    assert len(exempt) == len(_WHITESPACE_ONLY_SOURCES), (
        "an allowlisted matcher is no longer in SECRET_VALUE_PATTERNS — reclassify it"
    )
    for pattern in exempt:
        matched = [probe for probe in _WHITESPACE_REQUIRING_PROBES if pattern.search(probe)]
        assert matched, f"no probe exercises the exempt matcher {pattern.pattern!r}"
        for probe in matched:
            assert not pattern.search("".join(probe.split())), (
                f"{pattern.pattern!r} matches a whitespace-free string — it is not exempt"
            )
            assert not redaction.CONNECTION_STRING_PASSWORD_PATTERN.search(f"x://u:{probe}@h")


def test_no_other_matcher_can_straddle_a_userinfo_boundary():
    """A pattern-class TRIPWIRE. It used to be #443's whole safety argument; #445 demoted it.

    Under the span-merge engine this property is no longer what keeps a credential whole: a
    candidate straddling a userinfo boundary is merged with the span it crosses, not stranded
    by it, so a matcher that violated this would produce a wider marker rather than a leak.
    Kept — and kept exactly as written — because it still says something worth knowing about
    the pattern set: a new matcher whose replaced run admits `:` or `@` is reaching across
    authority delimiters, which is a design smell in a value matcher and worth a deliberate
    look. It fails here instead of going unremarked.

    The argument it originally checked, recorded so the demotion is legible: running the
    connection-string matchers first was safe because a later candidate was either inside the
    span they replace (covered by the marker) or wholly outside it (`sub` rescans the whole
    string). The only dangerous case was a candidate STRADDLING the boundary — and the
    boundary characters are the `:` that opens the password run and the `@` that closes it. So
    the invariant was that no other matcher's REPLACED run may contain either.

    It belongs to the OTHER matchers, not to the connection-string ones: the password run
    admits `:` on purpose, so `postgres://user:p1:p2:p3@host` is redacted whole (pinned by
    `test_connection_string_password_with_colons_redacted`). A comment in `redaction.py`
    asserted the opposite and was corrected after a review caught it; this test exists so the
    argument is checked rather than narrated, and so a future matcher whose replaced run
    admits `:` or `@` fails here instead of quietly invalidating it.

    Scope, stated because this is corpus evidence rather than a proof over each matcher's whole
    language: it can only judge a matcher on the spans it actually produces here. What closes
    that gap is the per-pattern liveness assertion below — every matcher must FIRE on this
    corpus — together with the completeness guard, which already forces each one to have a
    payload embedded in these lines. A matcher that escaped this check would first have to fail
    that one. (An earlier mutation probe for this test added a matcher that never fired, so the
    guard passed while inspecting nothing; the liveness assertion is what that probe bought.)
    """
    others = [
        p
        for p in redaction.SECRET_VALUE_PATTERNS
        if p not in _CS_MATCHERS and not _requires_whitespace(p)
    ]
    offenders, fired = [], set()
    for line in _collision_lines():
        for pattern in others:
            for match in pattern.finditer(line):
                fired.add(pattern.pattern)
                whole = match.group(0)
                replaced = whole[len(match.group(1)) :] if match.lastindex else whole
                if ":" in replaced or "@" in replaced:
                    offenders.append((pattern.pattern, replaced))
    assert not offenders, f"{offenders[0][1]!r} from {offenders[0][0]!r} crosses a boundary"
    silent = [p.pattern for p in others if p.pattern not in fired]
    assert not silent, f"these matchers never fired, so nothing was checked for them: {silent}"


def test_every_collision_payload_is_recognized_without_the_connection_matchers():
    """Liveness: a payload no other matcher fires on creates no collision to detect."""
    others = [p for p in redaction.SECRET_VALUE_PATTERNS if p not in _CS_MATCHERS]
    dead = [
        payload for payload in _IN_AUTHORITY_PAYLOADS if not any(p.search(payload) for p in others)
    ]
    assert not dead, f"no matcher recognizes {dead} — those payloads collide with nothing"


def test_the_tail_sentinel_is_not_self_redacting():
    # If the sentinel were redacted on its own, every line would pass for the wrong reason.
    assert redaction.redact_text(_TAIL_SENTINEL) == _TAIL_SENTINEL


# RETIRED with #445: `test_the_connection_string_matchers_run_before_the_substring_matchers`
# asserted that both connection-string matchers occupy the first two positions of
# `SECRET_VALUE_PATTERNS`. That invariant is dead — the span-merge engine's output does not
# depend on the list's order — so the test could only fail on a change that breaks nothing,
# and it would have read as a live safety requirement to anyone touching the list. Deleted
# rather than converted: the property it guarded is now structural. The matchers still stand
# first in the shipped list (see that list's header for why). The PRESENCE half of the retired
# test is not lost — removing either matcher fails the exact-output pins in the #438/#440
# sections, which assert the redacted form of every userinfo shape.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The three userinfo shapes from the issue, each with a labelled payload.
        (
            "redis://u:token=s3cr3tvalue0123456789%2Ftailsegment@host",
            "redis://u:[redacted: secret value]@host",
        ),
        (
            "redis://:token=s3cr3tvalue0123456789%2Ftailsegment@host",
            "redis://:[redacted: secret value]@host",
        ),
        # This row's replaced span ends at the username-token shape's own `:@` closing
        # `:`, which is not a safe terminator (#446) — same over-caution as elsewhere in
        # this file for that shape, not a leftover fragment.
        (
            "redis://token=s3cr3tvalue0123456789%2Ftailsegment:@host",
            "redis://[redacted: possibly partial secret value]:@host",
        ),
        # A vendor prefix rather than a label, in each position it can fragment.
        (
            "redis://u:ghp_aaaaaaaaaaaaaaaaaaaabbbb%2Ftailsegment@host",
            "redis://u:[redacted: secret value]@host",
        ),
        (
            "https://sk-aaaaaaaaaaaaaaaaaaaaaaaa%2Ftailsegment:@api.example.com",
            "https://[redacted: possibly partial secret value]:@api.example.com",
        ),
        # A JWT, whose shape alone is enough to trip the earlier matcher.
        ("x://u:eyJabcdefgh.abcdefgh.abcdefgh%2Ftail@h", "x://u:[redacted: secret value]@h"),
    ],
)
def test_marker_no_longer_strands_a_connection_string_credential(text, expected):
    # Exact output, not `not in`: a partial replacement must fail as loudly as a missed one,
    # and the whole point of #443 is that a partial one LOOKS complete.
    out = redaction.redact_text(text)
    assert out == expected
    # Re-run the emitted line: the fix must not depend on a second pass, and must not
    # mangle its own output on one.
    assert redaction.redact_text(out) == out


def test_query_string_false_positive_is_pinned_until_442():
    """#442, a pre-existing false positive, pinned with its exact current output.

    The named-username password class admits `?` and `#`, which cannot appear raw in
    userinfo, so an ordinary URL whose query carries an `@` is masked as a credential: scheme
    and host survive, the port and query go with the marker.

    Accepted rather than fixed here: #442's remedy is to exclude `?`/`#` from that class,
    which NARROWS a documented guarantee (`test_password_class_covers_every_character_it_
    claims` forbids it) and needs its own false-positive corpus. Folding it in would bundle a
    narrowing into a security fix. The credential is redacted either way — what is at stake is
    how much surrounding host/port/query text is masked with it. Pinned so #442's eventual
    fixer sees the current output rather than re-deriving it, and so a change to that span
    fails here with its reason attached.

    History, because this test used to assert a DELTA and no longer can. #443's reorder
    ENLARGED #442's reach by removing an accidental brake — under the old sequential pipeline
    a labelled marker landed inside the query and stopped the connection matcher from matching
    at all — so the pre-#443 ordering produced a visibly narrower span and the delta was worth
    asserting. Under the span-merge engine (#445) both orderings emit the output below, since
    the merged span set does not depend on the list's order; the monkeypatched half would have
    become a tautology, so it is gone rather than kept as decoration.

    Partial, not plain (a #446 review-round-2 finding): the connection-string password
    candidate's span and `LABELLED_VALUE_PATTERN`'s candidate for `token=...` both end at
    the identical position (the `@`), so this interval's trailing edge is a TIE between a
    userinfo candidate (wide safe set, `@` genuinely terminal) and a labelled one (narrow
    safe set, `@` dropped) — and a tie fails toward the narrower set, the same fail-closed
    direction `leading_whole`'s tie-break uses. Scrutinized rather than taken at face value:
    the interval here is fully covered either way (the wider connection-string span, not the
    narrower labelled sub-span, sets the actual replaced text), so the credential is not
    truncated — the marker is conservative, not incorrect, on a case #442 already documents
    as an accepted imprecision in how much surrounding text this shape masks.
    """
    text = "https://host.example:8443?token=s3cr3tvalue0123456789@x.example"
    assert redaction.redact_text(text) == (
        "https://host.example:[redacted: possibly partial secret value]@x.example"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # `/` is outside every userinfo run, so the named arm cannot span this password...
        (
            "redis://u:token=s3cr3tvalue0123456789%2Fmore/tail@host",
            "redis://u:token=[redacted: possibly partial secret value]%2Fmore/tail@host",
        ),
        (
            "redis://u:ghp_aaaaaaaaaaaaaaaaaaaa/tailsegment@host",
            "redis://u:[redacted: possibly partial secret value]/tailsegment@host",
        ),
        # ...and `?`/`#` are outside the empty-username and username-token runs (#440).
        (
            "redis://:token=s3cr3tvalue0123456789?tailsegment@host",
            "redis://:token=[redacted: possibly partial secret value]?tailsegment@host",
        ),
        (
            "amqp://sk-abcdefghijklmnopqrst?tail:@host",
            "amqp://[redacted: possibly partial secret value]?tail:@host",
        ),
    ],
)
def test_a_credential_the_userinfo_runs_cannot_span_is_only_partly_redacted(text, expected):
    """Characterization of what #443's reorder does NOT close, so the boundary is explicit.

    The reorder closes every shape the connection-string matchers can SPAN. A credential
    carrying a character those runs exclude was never matchable by them in the first place —
    ordering cannot help — so an earlier matcher firing on its prefix still leaves the
    remainder beside a marker. #446 does not close that miss — no pattern can produce a
    candidate covering the excluded character, so the value is still only partly replaced —
    it closes the DISHONESTY of the marker claiming otherwise. Each follower here (`%`, `/`,
    `?`) is not one of the safe-terminator characters the trailing check treats as proof the
    value ended, so the emitted marker is `[redacted: possibly partial secret value]` rather
    than the plain one: the reader can no longer conclude the credential was fully handled from
    the marker alone. This is the trailing half of #446 (a leading-continuation miss is
    exercised in `test_a_mid_token_jwt_match_is_marked_partial`); none of these four rows has a
    leading-continuation predecessor, so only the trailing check is in play here.

    Exact output rather than a `not in`/`endswith` pair, per this file's convention: those
    weaker forms pass on any output that merely keeps the tail and a marker somewhere, so they
    would not notice the redaction moving to the wrong span, or the marker silently reverting
    to the plain (complete-claiming) one.

    The miss itself remains this module's documented best-effort boundary: no merge can widen
    a single matcher's span, so a userinfo credential carrying `/` (or, on the arms that stop
    there, `?`/`#`) is never fully redacted by this pipeline. #446's fix does not repair that —
    it cannot, the excluded character is still unreachable — it only stops the output from
    claiming completeness it does not have. #446's fixer CONVERTED this test rather than
    deleting it, retiring the now-vacuous pre-#443-order monkeypatch half along with it (under
    the #445 span-merge engine, order cannot affect output at all).
    """
    assert redaction.redact_text(text) == expected


# --- #445: no matcher may strand part of a secret another one covers whole ----
# The sequential `re.sub` pipeline let an earlier, NARROWER matcher consume a PREFIX of a
# value a later, wider matcher would have covered whole. `sub` never revisits consumed
# text, so the tail shipped beside a complete-looking marker:
#
#     token=ghp_<20 chars>-tailsegment  ->  token=[redacted: secret value]-tailsegment
#     password=AKIA<16 chars>tailsegment -> password=[redacted: secret value]tailsegment
#
# Both are the vendor matchers (whose value classes are narrower than `_VALUE_CHARS`)
# running ahead of `LABELLED_VALUE_PATTERN`. #443 patched ONE instance of this class by
# reordering; the engine now collects every candidate span from the ORIGINAL text and
# merges overlapping ones, which removes the interference class rather than trading which
# member of it bites.
#
# Exact output rather than `not in`, per this file's convention: a partial replacement is
# the whole defect, and it LOOKS complete. Both `exempt_code` modes, because the engine's
# exemption step moved (see the exemption test below); the positive controls beneath prove
# each stranded tail is not self-redacting, so a green assertion cannot come from the tail
# being independently covered.

_GHP_20 = "ghp_" + "a" * 20
_XOXB_20 = "xoxb-" + "a" * 20
_AKIA_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"

_STRANDED_TAIL_CASES = [
    # `-` is outside `gh[pousr]_[A-Za-z0-9_]{20,}` but inside `_VALUE_CHARS`.
    (f"token={_GHP_20}-tailsegment", "token=[redacted: secret value]"),
    # `AKIA[0-9A-Z]{16}` is a FIXED length, so a lowercase tail simply falls outside it.
    (f"password={_AKIA_KEY}tailsegment", "password=[redacted: secret value]"),
    # `.` is outside `xox[baprs]-[A-Za-z0-9-]{20,}` but inside `_VALUE_CHARS`.
    (f"token={_XOXB_20}.tailsegment", "token=[redacted: secret value]"),
    # The same collision in the USERNAME slot of a connection string, where the merge has
    # to keep the two independent credentials apart: the labelled span (username) and the
    # connection-string password span do not overlap, so both markers survive. The first
    # marker is partial (#446): the labelled value stops at the `:` separating username
    # from password, which is not a safe terminator. The second (the password itself,
    # ending at `@`) stays plain.
    (
        f"x://token={_GHP_20}-tail:pw@h",
        "x://token=[redacted: possibly partial secret value]:[redacted: secret value]@h",
    ),
]


@pytest.mark.parametrize("exempt_code", [False, True])
@pytest.mark.parametrize(("text", "expected"), _STRANDED_TAIL_CASES)
def test_a_narrow_matcher_does_not_strand_the_tail_of_a_wider_one(text, expected, exempt_code):
    out, redacted = redaction._redact_secret_values(text, exempt_code=exempt_code)
    assert out == expected
    assert redacted is True
    # Re-run the emitted line: the fix must not depend on a second pass, and must not
    # mangle its own output on one.
    again, _ = redaction._redact_secret_values(out, exempt_code=exempt_code)
    assert again == out


@pytest.mark.parametrize("tail", ["-tailsegment", "tailsegment", ".tailsegment", "-tail", "pw"])
def test_the_stranded_tail_is_not_self_redacting(tail: str):
    # Positive control for every case above (the discipline `_PROBES` follows): if a tail
    # were redacted on its own, the exact-output assertions would pass for the wrong reason.
    assert redaction.redact_text(tail) == tail


def test_adjacent_spans_get_two_markers():
    """Touching spans do NOT merge — that is today's behavior and it is preserved.

    The fold merges on strict overlap (`start < merged_end`), so two candidates that
    abut emit two markers, exactly as two successive `re.sub` passes did. Asserted with
    the adjacency VERIFIED rather than assumed: a corpus whose spans happened to overlap
    (or not touch at all) would make this a test of nothing.

    Both markers are partial (#446), and correctly so: two credential shapes glued
    together with no separator at all is indistinguishable, from the trailing/leading
    checks' point of view, from ONE longer secret a matcher's own character class split
    in two — precisely the shape #446 exists to flag. AKIA's span sees `g` (the vendor
    prefix's own first letter) right after it, not a safe terminator; the vendor
    pattern's span is a whole-match candidate with AKIA's last letter right before it, in
    the leading-continuation class. Neither check knows AKIA is fixed-length — that
    pattern-specific fact is exactly what this general, per-character heuristic cannot
    see, so it hedges instead of asserting completeness it cannot back up.
    """
    text = _AKIA_KEY + _GHP_20
    spans = sorted(
        (m.start(), m.end()) for p in redaction.SECRET_VALUE_PATTERNS for m in p.finditer(text)
    )
    # None of the matchers that fire here have a preserved group, so start/end IS the
    # replaced span; the guard is what makes the adjacency claim checkable.
    assert len(spans) == 2 and spans[0][1] == spans[1][0], f"adjacency is not genuine: {spans}"
    out, redacted = redaction._redact_secret_values(text)
    assert out == (
        "[redacted: possibly partial secret value][redacted: possibly partial secret value]"
    )
    assert redacted is True


def test_exemption_is_judged_against_the_original_line_not_the_accumulator():
    """The one behavioral delta of the span-merge rewrite, pinned — it fails CLOSED.

    `_is_code_reference` reads the text around the match (`match.string`). Under the
    sequential engine that string was the PARTIALLY SUBSTITUTED accumulator, so an earlier
    matcher's marker could erase the evidence the exemption is judged on. Here the `AKIA`
    matcher used to replace `secret` + key out of `_LABEL_LEAD_RE`'s reach: the lead scan
    stops at the marker's `]`, never saw the word `secret`, and the labelled match was then
    exempted as a code reference — emitting
    `+secret[redacted: secret value]_key = helper_function_name(x)`.

    Candidates are now collected by `finditer` over the ORIGINAL line, so the lead scan
    reads `secretAKIA…` and the sensitive-label guard fires: BOTH values are redacted. The
    delta only ever removes exemptions, which is the safe direction for this boundary.

    Both markers are partial (#446), for two independent reasons: the AKIA marker's
    follower is `_` (not a safe terminator, and its own leading check also fires — the
    `t` of `secret` sits right before its whole-match span), and the labelled marker's
    value stops at `(`, the same call-follower shape this file's `(` cases concentrate
    around.
    """
    line = "+secretAKIAABCDEFGHIJKLMNOP_key = helper_function_name(x)"
    diff = f"diff --git a/app.py b/app.py\n{line}"
    out, paths = redaction.redact(diff)
    assert out == (
        "diff --git a/app.py b/app.py\n"
        "+secret[redacted: possibly partial secret value]_key = "
        "[redacted: possibly partial secret value](x)"
    )
    assert paths == ["app.py"]


# --- #445 review / #456: the replaced-span projection must fail closed --------
# The projection `(end(1), end(0))` is only correct when group 1 is a LEADING, PARTICIPATING
# prefix of the match. That holds for all four grouped patterns shipped today — and it is a
# property of those patterns, not of the engine, so a pattern added later (or substituted by a
# caller) can violate it. Both violation modes leak under an unguarded projection, which is
# why the engine validates rather than assumes and falls back to the FULL match span.
#
# These probes monkeypatch `SECRET_VALUE_PATTERNS` to the malformed matcher ALONE. With the
# live list present the real `AKIA` matcher would redact the key anyway and the test would
# pass without exercising the projection at all — the same "passes against the bug it guards"
# trap `_PROBES` exists for. Each test asserts the malformed pattern really does violate the
# assumption before asserting the output, so a probe that drifted into well-formedness fails
# loudly instead of going vacuous.


def test_a_group_that_is_not_at_the_match_start_falls_back_to_the_full_span(monkeypatch):
    """Violation mode 1: group 1 sits in the MIDDLE of the match.

    `end(1)` then lands mid-match, so everything before the group — here an entire AWS access
    key — is copied through as "preserved" text while the marker covers only the tail.
    """
    malformed = re.compile(r"AKIA[0-9A-Z]{16}(_hint:)[A-Za-z0-9]{16,}")
    text = "AKIAABCDEFGHIJKLMNOP_hint:abcdefghij1234567890"
    probe = malformed.search(text)
    assert probe is not None and probe.lastindex and probe.start(1) != probe.start(0), (
        "the probe no longer violates the leading-group assumption"
    )
    monkeypatch.setattr(redaction, "SECRET_VALUE_PATTERNS", [malformed])
    out, redacted = redaction._redact_secret_values(text)
    assert out == "[redacted: secret value]"
    assert redacted is True


def test_a_non_participating_group_1_falls_back_to_the_full_span(monkeypatch):
    """Violation mode 2: `lastindex` is truthy but group 1 never participated.

    An alternation whose SECOND branch matched leaves `span(1) == (-1, -1)` while `lastindex`
    reports the branch that did match. The projection then carries a negative start, which
    slices from the wrong end of the line: the secret survives intact and a stray marker
    appears beside it.
    """
    malformed = re.compile(r"(?:(pre:)|(alt:))SECRETVALUE0123456789")
    text = "xx alt:SECRETVALUE0123456789 yy"
    probe = malformed.search(text)
    assert probe is not None and probe.lastindex and probe.span(1) == (-1, -1), (
        "the probe no longer leaves group 1 non-participating"
    )
    monkeypatch.setattr(redaction, "SECRET_VALUE_PATTERNS", [malformed])
    out, redacted = redaction._redact_secret_values(text)
    assert out == "xx [redacted: secret value] yy"
    assert redacted is True


# --- #446: partial-marker honesty ---------------------------------------------
# A credential carrying a character the connection-string runs exclude (`/`, or `?`/`#` on
# the arms that stop there) can never be SPANNED by those matchers, so an earlier matcher
# firing on its prefix leaves the remainder beside a marker that claims completeness it does
# not have — `test_a_credential_the_userinfo_runs_cannot_span_is_only_partly_redacted` pins
# that exact shape. These tests cover the two checks that decide which marker a merged
# interval gets, and the properties the two markers must hold relative to each other.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # `@` is genuinely terminal for a USERINFO candidate (`user:pass@host`, RFC 3986),
        # but NOT for a LABELLED one: `_VALUE_CHARS` excludes `@` only because the alphabet
        # is a generic catch-all, not because a real secret can't contain one.
        (
            "password=" + "a" * 16 + "@tailsegment",
            "password=[redacted: possibly partial secret value]@tailsegment",
        ),
        (
            "token=" + "a" * 16 + "&tailsegment",
            "token=[redacted: possibly partial secret value]&tailsegment",
        ),
        (
            "key=" + "a" * 16 + ";tailsegment",
            "key=[redacted: possibly partial secret value];tailsegment",
        ),
    ],
)
def test_labelled_value_followed_by_a_dropped_wide_char_is_marked_partial(text, expected):
    """Regression pins for the Codex review's MEDIUM finding: the ORIGINAL global
    `_SAFE_TERMINATORS` treated `@`/`&`/`;`/`\\` as safe for every candidate, so
    `password=<16+ chars>@tailsegment` emitted the PLAIN marker while `@tailsegment` could
    be live credential text — the exact honesty failure this whole PR exists to remove. Each
    of these must now be partial. `test_shared_safe_terminators_keep_the_plain_marker_for_
    labelled_values` and `test_userinfo_only_safe_terminators_keep_the_plain_marker_for_wide_
    scope_candidates` cover the full member-by-member sweep; these three are the concrete
    motivating cases named in the review.
    """
    assert redaction.redact_text(text) == expected


def test_userinfo_shapes_keep_the_plain_marker_where_their_grammar_closed_them():
    """The companion regression: narrowing the LABELLED/Bearer safe set must not touch a
    userinfo/connection-string candidate, whose own grammar makes `@` genuinely terminal.
    Both userinfo shapes stay plain, unaffected by the narrowing.
    """
    assert redaction.redact_text("postgres://user:s3cr3tPassw0rd@db.example.com:5432/app") == (
        "postgres://user:[redacted: secret value]@db.example.com:5432/app"
    )
    assert redaction.redact_text("redis://:onlypass@localhost:6379") == (
        "redis://:[redacted: secret value]@localhost:6379"
    )


def test_a_mid_token_jwt_match_is_marked_partial():
    """The leading check (a plan-review round-2 finding), pinned directly.

    `xxxeyJ…`: the JWT pattern has no group, so its candidate is a whole-match one, and its
    true start (`eyJ`, not the leading `xxx`) is immediately preceded by `x` — a member of
    the leading-continuation class — so the match may have begun mid-token. Marked partial.

    Its complement, `token=ghp_<20 a's>`: the vendor pattern's whole-match candidate for
    `ghp_...` ties, at the exact same span, with `LABELLED_VALUE_PATTERN`'s prefix-preserving
    candidate for the same value (both start right after `=`). The fold in
    `_redact_secret_values` ORs whole-match flags across a tie at a merged interval's leftmost
    start (the fail-closed direction — prefer marking partial), so that tie by ITSELF would
    make `leading_whole` true here, same as if only the whole-match vendor candidate existed.
    What keeps this plain is the leading check's OTHER half: the character right before the
    value is `=`, which the leading-continuation class deliberately excludes (round-3
    finding) — an assignment delimiter legitimately abuts a COMPLETE vendor token, unlike
    every other member of `_VALUE_CHARS`, which can be an interior character of a longer
    secret. (The tie-break's AND-vs-OR choice is unobservable through this case specifically
    because of that exclusion; `test_tie_fold_prefers_partial_when_any_tied_candidate_is_
    whole_match` isolates and pins the tie-break itself with a synthetic probe.)
    """
    jwt_body = "a" * 30 + "." + "b" * 10 + "." + "c" * 10
    partial_text = f"xxxeyJ{jwt_body}"
    out = redaction.redact_text(partial_text)
    assert out == "xxx[redacted: possibly partial secret value]"

    complete_text = "token=" + "ghp_" + "a" * 20
    out2 = redaction.redact_text(complete_text)
    assert out2 == "token=[redacted: secret value]"


@pytest.mark.parametrize(
    "patterns_in_order",
    [
        "whole-first",
        "prefix-first",
    ],
)
def test_tie_fold_prefers_partial_when_any_tied_candidate_is_whole_match(
    patterns_in_order, monkeypatch
):
    """Direct unit-level pin for the tie-break's OR semantics (the fail-closed direction:
    prefer marking partial), isolated from every real pattern.

    On today's shipped patterns, AND/OR/"keep whichever candidate sorts first" are
    observationally IDENTICAL end-to-end — every real tie between a whole-match and a
    prefix-preserving candidate is also decided independently by the trailing check or the
    `=` leading-continuation exclusion (see the fold's comment in `_redact_secret_values` and
    `test_a_mid_token_jwt_match_is_marked_partial`'s docstring). This probe manufactures a tie
    that neither of those can reach: the text ends exactly where the value ends (so the
    trailing check is silent — absent follower is unconditionally complete), and the
    character right before the tied start is a LETTER, not `=` (so the leading-continuation
    check's character test passes either way). Only the tie-break itself decides the outcome.

    ``whole`` has no group (a whole-match candidate, `lastindex` falsy); ``prefixed`` has a
    leading group ending at the identical position (a prefix-preserving candidate) — verified
    to share the exact same replaced span before trusting the probe. Run in BOTH list orders:
    the result must not depend on which pattern `SECRET_VALUE_PATTERNS` lists first, which is
    the whole reason OR (not "keep first candidate") was chosen — matching the #445
    order-invariance this engine is built to keep.
    """
    text = "pfxSECRETVALUE0123456789"
    whole = re.compile(r"SECRETVALUE0123456789")
    prefixed = re.compile(r"(pfx)SECRETVALUE0123456789")
    whole_span = redaction._replaced_span(whole.search(text))
    prefixed_span = redaction._replaced_span(prefixed.search(text))
    assert whole_span == prefixed_span, (
        f"the probe's tie is no longer real: {whole_span} != {prefixed_span}"
    )

    patterns = [whole, prefixed] if patterns_in_order == "whole-first" else [prefixed, whole]
    monkeypatch.setattr(redaction, "SECRET_VALUE_PATTERNS", patterns)
    out, redacted = redaction._redact_secret_values(text)
    assert out == "pfx[redacted: possibly partial secret value]"
    assert redacted is True


def test_partial_marker_does_not_contain_the_plain_marker():
    # Pinned so an `in`-style assertion elsewhere (there are many in this file) cannot
    # silently pass against the wrong marker: if the partial marker ever grew the plain
    # one as a substring, `_any_marker_in`-style checks would stop distinguishing them.
    assert redaction._SECRET_VALUE_MARKER not in redaction._PARTIAL_SECRET_VALUE_MARKER


def test_partial_marker_is_idempotent():
    # Re-running redact_text over emitted partial-marker text must be a fixed point — the
    # same guarantee the plain marker already carries throughout this file.
    text = "redis://u:token=s3cr3tvalue0123456789%2Fmore/tail@host"
    once = redaction.redact_text(text)
    assert "[redacted: possibly partial secret value]" in once
    assert redaction.redact_text(once) == once


def test_review_locked_sets_have_not_drifted():
    """Literal pin for the sets plan-review rounds settled (brief: "do not 'simplify' them").

    The parametrized sweeps below (`test_shared_safe_terminators_keep_the_plain_marker_for_
    labelled_values`, `test_userinfo_only_safe_terminators_keep_the_plain_marker_for_wide_
    scope_candidates`) read their domain FROM these constants themselves
    (`sorted(redaction._LABELLED_SAFE_TERMINATORS)` etc.), so neither can catch a member
    quietly DROPPED from the constant it sweeps — a dropped member simply shrinks that
    sweep's own parametrize list along with it, and the sweep still passes on whatever
    remains (the derived-expectations-are-tautological failure mode: a test whose input
    domain IS the value under test can't fail when that value shrinks). What the sweeps DO
    guard is that every member CURRENTLY in a set actually behaves as a safe terminator FOR
    THE CANDIDATE TYPE it is claimed safe for — i.e. that the trailing check's logic is
    correct for each one, not that the set's own membership is unchanged. Only a literal,
    independently-spelled pin can catch a set itself changing, which is what this test is
    for.
    """
    assert frozenset(" \t\n\r\v\f\"'),]}>") == redaction._SHARED_SAFE_TERMINATORS
    assert frozenset("@&;\\") == redaction._USERINFO_ONLY_SAFE_TERMINATORS
    assert frozenset(" \t\n\r\v\f\"'\\@&,;)]}>") == redaction._SAFE_TERMINATORS
    assert frozenset(" \t\n\r\v\f\"'),]}>") == redaction._LABELLED_SAFE_TERMINATORS
    assert redaction._LEADING_CONTINUATION_RE.pattern == "[A-Za-z0-9._~+/-]"


@pytest.mark.parametrize("char", sorted(redaction._LABELLED_SAFE_TERMINATORS))
def test_shared_safe_terminators_keep_the_plain_marker_for_labelled_values(char):
    """Exercises each `_LABELLED_SAFE_TERMINATORS` member against the NARROW-scope candidate
    type it is claimed safe for: a LABELLED value (#446, a Codex review finding — the
    original single global safe set wrongly claimed `@`/`&`/`;`/`\\` were also safe here; see
    `_LABELLED_SAFE_TERMINATORS`'s header). Every member of this set is ALSO a member of the
    wide `_SAFE_TERMINATORS` (it is the shared base both derive from), so this sweep doubles
    as coverage for the wide set's shared members too — a separate sweep for those would be
    redundant. `test_userinfo_only_safe_terminators_keep_the_plain_marker_for_wide_scope_
    candidates` below covers the members that are safe ONLY outside this narrow scope.
    """
    text = f"token=abcdefghijklmnop{char}tail"
    out = redaction.redact_text(text)
    assert out == f"token=[redacted: secret value]{char}tail"


@pytest.mark.parametrize("char", sorted(redaction._USERINFO_ONLY_SAFE_TERMINATORS))
def test_userinfo_only_safe_terminators_keep_the_plain_marker_for_wide_scope_candidates(char):
    """Exercises each `_USERINFO_ONLY_SAFE_TERMINATORS` member (`@`, `&`, `;`, `\\`) against
    a WIDE-scope candidate — i.e. anything that is not LABELLED/Bearer, which still uses
    `_SAFE_TERMINATORS` unchanged (#446 narrowed only the LABELLED/Bearer case).

    A vendor whole-match pattern (`AKIA`), not a real connection-string shape, is the
    construction: `AKIA[0-9A-Z]{16}` is FIXED-length and excludes all four characters, so
    each genuinely CAN be the immediate follower of a real `AKIA` match — unlike for a
    connection-string candidate, where `&`/`;`/`\\` are already ADMITTED into the
    password/token character classes (measured: neither class excludes them), so a real
    occurrence is consumed into the match rather than ever appearing as an immediate
    follower — only `@` is naturally reachable there, via the `(?=@)`/`(?=:@)` closing
    lookahead, and that shape is already pinned elsewhere in this file (e.g.
    `test_connection_string_redaction_unchanged_for_scheme_led_urls`). Testing all four via
    one always-reachable construction keeps this sweep uniform rather than mixing a
    userinfo-shaped case for `@` with something else for the other three.
    """
    text = f"AKIA{'A' * 16}{char}tail"
    out = redaction.redact_text(text)
    assert out == f"[redacted: secret value]{char}tail"


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
    # The value's span ends right at the closing `\"`'s `\` — not a safe terminator for a
    # LABELLED candidate (#446's narrowed set), since `\` can also be a real interior
    # character a generic value alphabet cannot express — so this gets the partial marker;
    # the point of this test (#58) is that the value is stripped at all.
    assert _any_marker_in(out)


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
    # Several of these values are immediately followed by `(` — not a safe terminator
    # (#446) — so some rows get the partial marker rather than the plain one; this test's
    # contract is only that the credential stays redacted, not which marker it gets.
    assert _any_marker_in(out)
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
    # Every value here is immediately followed by `(` — not a safe terminator (#446) — so
    # the marker is the partial variant; this test's contract is only that redaction
    # happens at all, not which marker it gets.
    assert _any_marker_in(out)


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
    # `(` right after the value is not a safe terminator (#446), so this gets the partial
    # marker; the contract here is only that redaction happened.
    assert _any_marker_in(redaction.redact_text(text))


def test_authorization_header_line_is_not_treated_as_source():
    # DiffRedactor scans bare `Authorization:` lines too, but a header is not source, so
    # it gets prose's conservative treatment rather than the code-reference exemption.
    line = "Authorization: token = _placeholder_seed(text)"
    out, _ = redaction.redact(f"diff --git a/app.py b/app.py\n{line}")
    assert _any_marker_in(out)


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
    # The double-escaped-quote row's value ends right at the closing `\"`'s `\` — not a
    # safe terminator for a LABELLED candidate (#446's narrowed set) — so it gets the
    # partial marker; every other row's value ends at a plain `"`, which stays safe.
    assert _any_marker_in(out)
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
    # Three of these four values are immediately followed by `(` — not a safe terminator
    # (#446) — so those rows get the partial marker; this test's contract is only that no
    # quoted key ever gets the code exemption, not which marker the value ends up with.
    assert _any_marker_in(out)
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
    # `(` right after the value is not a safe terminator (#446), so this gets the partial
    # marker; the point of this test is the fail-closed rejection, not the marker text.
    assert _any_marker_in(out)
    assert "helper_function_name_here" not in out
    assert paths == ["app.py"]


def test_bracket_consuming_match_always_rejects_the_code_exemption():
    # The structural invariant the widening rests on, asserted directly rather than
    # inferred from behavior: a `]` can only be reached by first consuming a quote into
    # `key_quote`, so no match can consume a bracket while leaving that group empty.
    quoted = [
        'a["key"] = ' + "x" * 20,
        "a['key'] = " + "x" * 20,
        'a[\\"key\\"] = ' + "x" * 20,
        'a["key" ] = ' + "x" * 20,
        'a["password"]["key"] = ' + "x" * 20,
    ]
    seen_bracket = 0
    for probe in quoted:
        match = redaction.LABELLED_VALUE_PATTERN.search(probe)
        assert match is not None, probe
        if "]" in match.group(0):
            seen_bracket += 1
            assert match.group("key_quote"), probe
            assert not redaction._is_code_reference(match), probe
    # Guard the guard: if the pattern stopped matching brackets entirely, every assertion
    # above would vacuously pass.
    assert seen_bracket == len(quoted)

    # The probes above all carry a quote, so they stay green under a rewrite that moves the
    # bracket OUT of `key_quote` — which is exactly the unsafe refactor this test claims to
    # catch. These UNQUOTED subscripts are what distinguishes the two: under the real
    # pattern they must not match at all, because reaching the `]` requires a quote. Under
    # the rewrite they match with an empty `key_quote`, and `a[key] = helper(x)` is then
    # exempted as a code reference — reopening the leak the nesting exists to prevent.
    for probe in ("a[key] = " + "x" * 20, "a[key] = helper_function_name_here(x)"):
        match = redaction.LABELLED_VALUE_PATTERN.search(probe)
        if match is not None and "]" in match.group(0):
            raise AssertionError(
                f"bracket consumed with key_quote={match.group('key_quote')!r}: {probe}"
            )


def test_bracket_match_does_not_swallow_a_later_sensitive_label():
    # A bracketed candidate matches EARLIER than the pre-#434 pattern did, which let it
    # consume a following sensitive label as its own value: `sub` never revisits consumed
    # text, so the real secret after the second separator went out intact where the old
    # pattern had redacted it (found by the #434 Codex review). Guarded here because the
    # corpus A/B and the monotonicity fuzz both missed it — neither generated a value that
    # itself ends in a second label.
    for line, secret in [
        (
            'cfg["token"] = application_specific_api_key = "abcdefghij1234567890"',
            "abcdefghij1234567890",
        ),
        (
            'cfg["key"] = my_application_password = "opaquevaluewithnoprefix123"',
            "opaquevaluewithnoprefix123",
        ),
        # No space around the chained separator. This is the form that defeated the FIRST
        # attempt at this guard: the value character class contains `=`, so `label=value`
        # is absorbed into the value whole and a guard that only looks PAST the value's end
        # inspects the wrong position. It must therefore look inside the value.
        (
            '["secret"] : application_specific_api_key="abcdefghij1234567890",',
            "abcdefghij1234567890",
        ),
        (
            'cfg["a"]["passphrase"] = my_application_password=\'opaquevaluewithnoprefix123\')',
            "opaquevaluewithnoprefix123",
        ),
        # ...and with the whitespace-tolerant bracket form
        (
            'obj["cache_key" ]:my_application_password="wJalrXUtnFEMIKbPxRfiCYEXAMPLEKEY"',
            "wJalrXUtnFEMIKbPxRfiCYEXAMPLEKEY",
        ),
        # The swallowed label wears a #432 closing QUOTE. This defeated the second attempt,
        # whose guard hand-wrote the label→separator step as a bare separator and so could
        # not see a quoted key at all — leaking the exact input family #432 was written for.
        # It looks impossible for the value run to reach a JSON key, since `"` is not a
        # value character; it is reachable because the pattern's own value-opening quote
        # consumes the key's OPENING quote, so the run starts inside the key.
        (
            'cfg["token"] = "aws_secret_access_key": "wJalrXUtnFEMIKbPxRfiCYEXAMPLEKEY"',
            "wJalrXUtnFEMIKbPxRfiCYEXAMPLEKEY",
        ),
        (
            'settings["auth"]["token"] = \\"aws_secret_access_key\\": \\"abcdefghij1234567890\\"',
            "abcdefghij1234567890",
        ),
        # ...and with the bracket step too, not just the quote
        (
            'cfg["token"] = "cfg["]["stripe_api_key"]: "opaquevaluewithnoprefix123"',
            "opaquevaluewithnoprefix123",
        ),
    ]:
        out = redaction.redact_text(line)
        assert secret not in out, out
        # and the redaction must land on the real value, not on the identifier before it.
        # One row's value ends right at a closing `\"`'s `\`, not a safe terminator for a
        # LABELLED candidate (#446's narrowed set), so it gets the partial marker.
        assert _any_marker_in(out)


def test_neutral_bracket_key_is_untouched():
    # The widening still keys on the LABEL. An ordinary subscript keeps its value, so this
    # does not degrade into "redact every subscripted assignment in a source file".
    line = '+cfg["display_name"]["value"] = abcdefghij1234567890'
    diff = f"diff --git a/app.py b/app.py\n{line}"
    out, paths = redaction.redact(diff)
    assert out == diff
    assert paths == []


def test_swallow_guard_leaves_non_bracket_matches_alone():
    # The guard is CONDITIONED on `key_bracket`, and nothing else pinned that: making it
    # unconditional passed every other test in this file. It is not a leak either way —
    # both forms redact the secret — but an unconditional guard silently changes which SPAN
    # a non-bracket chain redacts, and that class is pre-existing (reachable on the
    # pre-#434 pattern too), so it belongs to #436 rather than to this change.
    #
    # Conditional (correct here): the whole chain is masked, exactly as before #434.
    # Unconditional: `key:api_key=[redacted…]`, a narrower span and a behavior change to
    # inputs this issue never touched.
    assert redaction.redact_text("key:api_key=leftovervalue123456789") == (
        "key:[redacted: secret value]"
    )


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
    # `(` right after the value is not a safe terminator (#446), so this gets the partial
    # marker; the point of this test is that it is masked at all, not the marker text.
    assert _any_marker_in(out)
    assert paths == ["app.py"]
