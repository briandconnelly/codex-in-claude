"""Secret redaction in diffs."""

from __future__ import annotations

import itertools
import re
import string
import time

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
        # A scheme the labelled pattern already ate, leaving a marker (#438's leak).
        (
            "key=abcdefghijklmnopx://:hunter2@host",
            "key=[redacted: secret value]://:[redacted: secret value]@host",
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


@pytest.mark.parametrize(
    "char", [c for c in string.printable if c not in _NON_MEMBERS and not c.isspace()]
)
def test_empty_username_password_class_covers_every_character_it_claims(char):
    # The password class is unchanged by #440, but it is now reachable through a
    # SECOND route (empty username). A narrowing of the class on that route alone
    # would leave the named-username walk above green, so it is walked here too.
    assert redaction.redact_text(f"x://:ab{char}cd@h") == "x://:[redacted: secret value]@h"


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
        # The boundary itself, from both sides.
        (f"https://{_TOKEN_16}:@host/path", "https://[redacted: secret value]:@host/path"),
        (f"https://{_TOKEN_15}:@host/path", f"https://{_TOKEN_15}:@host/path"),
        # A longer opaque token.
        (
            "https://s3cr3tOpaqueToken123456789:@api.example.com/v1",
            "https://[redacted: secret value]:@api.example.com/v1",
        ),
        # Percent-encoding and punctuation inside the admitted class.
        (
            "https://tok%2Fen.with-punct_123~+=:@host",
            "https://[redacted: secret value]:@host",
        ),
        # No scheme at all — the `://` anchor carries the match (#438).
        (f"://{_TOKEN_16}:@host", "://[redacted: secret value]:@host"),
        # A scheme already eaten by the labelled pattern's marker.
        (
            f"key=abcdefghijklmnopx://{_TOKEN_16}:@host",
            "key=[redacted: secret value]://[redacted: secret value]:@host",
        ),
        # Two candidates of this shape: `sub` must reach the second.
        (
            f"https://{_TOKEN_16}:@h1 https://s3cr3tOtherToken99:@h2",
            "https://[redacted: secret value]:@h1 https://[redacted: secret value]:@h2",
        ),
        # Mixed with the existing password shape, in BOTH orders — neither match
        # may swallow the other.
        (
            f"https://{_TOKEN_16}:@h1 postgres://u:s3cr3tPass@h2",
            "https://[redacted: secret value]:@h1 postgres://u:[redacted: secret value]@h2",
        ),
        (
            f"postgres://u:s3cr3tPass@h1 https://{_TOKEN_16}:@h2",
            "postgres://u:[redacted: secret value]@h1 https://[redacted: secret value]:@h2",
        ),
        # All three userinfo shapes on one line.
        (
            f"https://{_TOKEN_16}:@h1 redis://:s3cr3tPw@h2 postgres://u:s3cr3tPass@h3",
            "https://[redacted: secret value]:@h1 "
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


def test_username_token_redaction_is_idempotent():
    # The marker's own `:` truncates the run to `[redacted` (9 chars), under the
    # gate, so a second pass cannot re-match and nest a marker inside a marker.
    once = redaction.redact_text(f"https://{_TOKEN_16}:@h1 redis://:s3cr3tPw@h2")
    assert redaction.redact_text(once) == once
    assert once.count("[redacted: secret value]") == 2


def test_username_token_redacted_in_a_source_diff_and_path_recorded():
    diff = "\n".join(
        [
            "diff --git a/app.py b/app.py",
            "+++ b/app.py",
            f'+CLIENT = Api("https://{_TOKEN_16}:@api.example.com")',
        ]
    )
    out, paths = redaction.redact(diff)
    assert '+CLIENT = Api("https://[redacted: secret value]:@api.example.com")' in out
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
        # and the redaction must land on the real value, not on the identifier before it
        assert "[redacted: secret value]" in out


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
    assert "[redacted: secret value]" in out
    assert paths == ["app.py"]
