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
# as the label itself, and inside the guard that stops a match from swallowing a later
# label (#436; originally bracket-only, #434). Two literal copies would drift, and a guard
# that silently stopped covering a label the pattern still matches is precisely the failure
# this indirection prevents.
_LABEL_ALT = (
    r"(?:(?:api|access|secret|private)?_?(?:key|token|secret)|passw(?:or)?d|pwd|passphrase)"
)
# The value run, also used twice for the same reason.
_VALUE_CHARS = r"[A-Za-z0-9._~+/=-]"
# The redactable-value threshold, likewise defined once and interpolated everywhere this
# pattern family hard-codes it — the main value run below AND the guard's inner-value tail
# (#436). A repeated literal `16` lets the guard's notion of "redactable" drift from the
# matcher's own, silently, the same drift `_KEY_STEP_ANON` above exists to make
# unrepresentable for the label/separator shape. (Bearer/userinfo/`sk_live_`'s own `{16,}`
# are hand-spelled per their own comments and deliberately untouched — this constant is
# scoped to the labelled-value family alone.)
_MIN_SECRET_VALUE_LEN = 16
# How far the UNCONDITIONAL swallow guard (below) peeks ahead of a candidate's own
# separator hunting a LATER label, in `_VALUE_CHARS`, when `key_bracket` is falsy (the
# coverage #436 ADDS — main had no protection here at any distance, bracketed or not).
# Left unbounded, that peek made the guard quadratic on repeated-anchor input the same way
# #439's JWT segment was (that bound is defined further below, at the JWT entry in
# `SECRET_VALUE_PATTERNS`): matching an open-ended run costs O(remaining) even with no
# backtracking, and the guard is evaluated at every one of a repeated anchor's
# occurrences, so `("key=", N)` summed that cost across ~N positions — O(N^2). Measured on
# the unbounded guard: `"key="*20000` (80k chars) 3.5s, `"key="*40000` (160k) 14.2s, ~4x
# per 2x input.
#
# This bound applies ONLY to the non-bracket branch. The bracketed branch (below, the
# `(?(key_bracket)...)` conditional) stays genuinely UNBOUNDED — restoring #434's shipped
# guarantee exactly, with no cap and no distance limit — and is still flat on the hostile
# seed for a structural reason rather than a numeric one: on `"key="*N`, `key_bracket` is
# false at EVERY anchor (there is no bracket anywhere in that text), so the unbounded
# conditional branch never evaluates at all — only this O(cap) branch runs, giving O(N).
# On a text built from repeated BRACKETED anchors (`'x["key"]= ' * N`, the seed this
# guard's own conditional branch is actually exposed to), the branch is flat for a
# different reason: `key_bracket` requires consuming a quote and a `]`, and both are
# outside `_VALUE_CHARS`, so the unbounded probe run FROM one bracketed anchor is
# terminated (by hitting a quote/bracket char the value class can't cross) before the NEXT
# bracketed anchor can even begin — no probe run can span two bracketed anchors, so the
# per-anchor cost cannot compound across anchors the way an uncapped non-bracket peek did.
# Measured (`'x["key"]= ' * N`): 5000 reps (50k chars) 0.007s, 10000 (100k) 0.014s, 20000
# (200k) 0.029s, 40000 (400k) 0.057s — linear, ~2x per 2x input, both branches together
# proven flat by `test_repeated_anchor_input_is_not_quadratic_for_any_pattern` and a
# second, bracket-anchor-seeded timing test alongside it.
#
# The value-length probe on BOTH branches is an EXACT `_MIN_SECRET_VALUE_LEN` rather than
# an open `{16,}` — the guard only asks a yes/no question ("is the inner value at least
# this long"), which a fixed count answers identically (both accept exactly when 16+
# matching chars exist) at O(16) instead of O(remaining); this is what keeps the
# UNBOUNDED bracketed branch itself flat per-anchor once it reaches a candidate inner
# label, independent of the peek-cap question above, which is about how far it can reach
# in the first place.
#
# 1024 rather than a smaller cap: measured (`"key="*20000`, the mandated anti-quadratic
# seed) at 256 -> 0.034s, 1024 -> 0.103s, 4096 -> 0.366s, confirming the cost scales
# linearly in the cap (not just in input length) — so the cap itself, not only the
# bound's existence, has to be chosen deliberately. 1024 is 2x the JWT first-segment
# bound (512, chosen for real JOSE headers ~20-60 chars, ~120 with `kid`/`jku`) and >12x
# the widest peek distance any fixture in the swallow suites below (:2300-2358,
# `test_labelled_match_does_not_swallow_a_later_sensitive_label`) exercises (under 80
# chars), while still leaving ~20x headroom under the 2.0s anti-quadratic budget on this
# machine — 4096's ~5.5x margin was judged too thin for a slower CI runner, the same
# reasoning `test_repeated_anchor_input_is_not_quadratic_for_any_pattern`'s own module
# comment applies to the JWT rep count.
#
# Because the bracketed branch is unbounded, this cap is NOT a narrowing of anything main
# ever shipped: main's bracket-conditional guard is preserved byte-for-byte in reach (see
# the conditional branch below), so every input it protected is still protected, at any
# distance. What the cap bounds is exclusively the NEW non-bracket coverage #436 adds —
# main had zero protection there at ANY distance, so capping it at 1024 is a partial fix
# of a pre-existing leak, not a regression: a non-bracket swallow chain whose gap exceeds
# 1024 chars remains exactly as unprotected as it always was on every released version.
# Pinned at the exact cliff by `test_non_bracket_swallow_guard_peek_boundary_is_pinned`.
_SWALLOW_GUARD_PEEK = 1024

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
    # TWO guards in sequence, both refusing a candidate whose own value would contain a
    # LATER sensitive label, separator, and a redactable value behind it — a swallow.
    # Found by the #434 review: a bracketed candidate matches EARLIER than the pre-#434
    # pattern did, and `sub` never revisits consumed text, so
    # `cfg["token"] = application_specific_api_key = "<secret>"` matched at `token"]`,
    # swallowed `application_specific_api_key`, and sent the real secret after the second
    # separator out intact — where the old pattern had redacted it. Failing the candidate
    # here makes the engine advance and find the `api_key` match instead.
    #
    # Both guards look INSIDE the value, not past its end, because `_VALUE_CHARS` contains
    # `=`: an unspaced `label=value` chain is absorbed whole, so a trailing `(?!\s*[:=])`
    # inspects the wrong position and misses it entirely. Both also have to allow the
    # value run to start INSIDE a quoted key: the value's own opening quote consumes the
    # swallowed key's opening `"`, so the run begins at the label and ends on its closing
    # quote — which is why each uses `_KEY_STEP_ANON` and not a bare separator (the second
    # #434 review finding; the bare form leaked
    # `cfg["token"] = "aws_secret_access_key": "<secret>"`).
    #
    # GUARD 1 (conditional, UNBOUNDED): `#434`'s original guard, conditioned on
    # `key_bracket` so it fires only for a bracketed outer, at ANY distance — no peek cap.
    # This is deliberately not merged into guard 2: it exists to preserve #434's shipped
    # protection byte-for-byte in REACH (not narrowed by anything #436 or its own
    # follow-up adds), while still picking up the one refinement both guards need (see the
    # length-tail paragraph below). Flat on a repeated-BRACKETED-anchor seed for a
    # structural reason, not a numeric one — see `_SWALLOW_GUARD_PEEK`'s module comment
    # for why an unbounded probe here still cannot compound across anchors.
    rf"(?(key_bracket)(?!{_VALUE_CHARS}*{_LABEL_ALT}{_KEY_STEP_ANON}\s*(?:\\?['\"])?"
    rf"{_VALUE_CHARS}{{{_MIN_SECRET_VALUE_LEN}}}))"
    # GUARD 2 (unconditional, BOUNDED at `_SWALLOW_GUARD_PEEK`): fires for every
    # candidate, bracketed or not — this is the coverage #436 ADDS that #434 never had at
    # any distance, so it is a bounded ADDITION, not a narrowing of guard 1's reach (see
    # `_SWALLOW_GUARD_PEEK`'s module comment for the full non-regression argument and the
    # cap's own flatness proof).
    rf"(?!{_VALUE_CHARS}{{0,{_SWALLOW_GUARD_PEEK}}}{_LABEL_ALT}{_KEY_STEP_ANON}\s*(?:\\?['\"])?"
    rf"{_VALUE_CHARS}{{{_MIN_SECRET_VALUE_LEN}}})"
    # The trailing length check on the swallowed label's OWN value — shared by both
    # guards — is load-bearing, not decorative: without it a guard refuses the outer
    # candidate the moment it merely SEES a later label, whether or not that label's own
    # value would ever have been redactable. `cfg["token"] = aaaaaaaaaaaakey=short` is the
    # counterexample for guard 1 (bracketed) and `token = aaaaaaaaaaaakey=short` for guard
    # 2: the inner `key=short` value is 5 chars, below the redaction threshold, so a naive
    # guard refuses the outer AND the inner never matches on its own — a total miss.
    # Verified on `main`: the pre-#436, tail-less bracketed guard DOES leak the whole
    # bracketed chain this way (`cfg["token"] = aaaaaaaaaaaakey=short` comes out
    # untouched) — so the tail is not only new-coverage hygiene for guard 2, it is a
    # genuine improvement over guard 1's pre-#436 shape too, now redacting that chain
    # whole. Requiring the inner value to itself clear `_MIN_SECRET_VALUE_LEN` keeps the
    # outer eligible whenever the inner alone could never have been redacted. It is an
    # EXACT `{_MIN_SECRET_VALUE_LEN}` on both guards, not an open `{_MIN_SECRET_VALUE_LEN,}`
    # — a yes/no probe, not a real match, so a fixed count answers identically at O(16)
    # instead of O(remaining); see `_SWALLOW_GUARD_PEEK` for why that matters even for
    # guard 1's unbounded peek, not only guard 2's bounded one.
    #
    # Making guard 2 unconditional (rather than absent, as `main` had it) also changes
    # NON-bracket matches that used to keep byte-identical behavior: `key:api_key=<secret>`
    # redacts only the tail (`key:api_key=[redacted…]`) rather than the whole chain.
    # Accepted deliberately (#436) — the narrower span is the trade for closing the
    # swallow on a shape `main` never protected at all, not a leak either way, since the
    # secret is still redacted.
    #
    # On a diff body line (`exempt_code=True`), a guard's refusal can also hand the line
    # to `_is_code_reference` (#421), which may then exempt it entirely — e.g.
    # `password = application_api_key = resolve_credentialx(env)` used to redact the FIRST
    # span; now the whole line reads as a code reference and nothing is masked. Accepted:
    # 0 occurrences in the 713,126-line real third-party corpus swept for this change
    # (`.venv/site-packages`, `exempt_code=True`), and what stays unmasked in that shape
    # is an IDENTIFIER (`application_api_key`), not a literal — arguably a false-positive
    # reduction rather than a loss, since the code-reference exemption exists precisely to
    # stop masking source under review.
    rf"{_VALUE_CHARS}{{{_MIN_SECRET_VALUE_LEN},}}"
)

# The connection-string userinfo matchers, named rather than left anonymous in the list
# below. Their contract is one-directional — a change may widen what is recognized, never
# narrow it — and the differential sweep that enforces that has to substitute one of them
# to prove it can still see a loss. Addressing them by list POSITION is what that test did
# first, and appending a matcher pointed it at the wrong one. A name cannot drift that way.
#
# See the comments at each definition below for why each is shaped as it is.
#
# The password run's character class is CONDITIONAL on whether a username was present,
# and the two arms are not interchangeable:
#
#   * With a username (`://user:…@`) the run admits `?` and `#`, byte-for-byte as before.
#     This arm is deliberately untouched. Narrowing it would stop redacting a password
#     containing a raw `?` or `#` (`x://u:ab?cd@h`), a loss the printable-domain walk in
#     the tests already forbids, and the one-directional contract above rules out.
#   * WITHOUT a username (`://:…@`, the branch #440 adds) the run stops at `?` and `#`.
#     It has to: `://:` is also how an empty host with a port serializes, so admitting
#     them made `custom://:8080?email=a@b` — a query string carrying an `@`, no userinfo
#     anywhere — come out as `custom://:[redacted: secret value]@b`. Per RFC 3986 a raw
#     `?`/`#` cannot appear in userinfo (it must be percent-encoded), so an `@` that
#     follows one is in the query or fragment and never delimits a credential.
#
# Constraining only the NEW arm is what keeps this a pure widening: every input the old
# pattern matched still matches, identically. The same false positive DOES exist on the
# username arm (`https://host.example:8443?email=user@example.com` is masked, and was
# before this change), but fixing that is a narrowing of a documented guarantee rather
# than a bug in this one, so it is filed separately as #442 rather than folded in here.
#
# #443 ENLARGED #442's reach without changing this class. Running these matchers first
# removed an accidental brake: a labelled marker landing inside the query used to stop
# the username arm from matching, so `https://host.example:8443?token=<secret>@x.example`
# kept its port and query. It no longer does — and since #445 that no longer depends on
# position either: candidates are collected from the ORIGINAL text, so there is no marker
# in the query to brake on whatever order these matchers sit in. Accepted deliberately —
# the credential is still redacted either way, what grows is how much surrounding text goes
# with it — and pinned by a characterization test so #442's eventual fixer sees the current
# output rather than re-deriving it.
#
# ---------------------------------------------------------------------------
# Connection-string userinfo: redact the password between `://[user]:` and `@host`,
# keeping scheme, user, and host. The `@` lookahead avoids matching `host:port`.
#
# The username is OPTIONAL (`*`, not `+`) — `://:password@host` is password-only
# userinfo, and it is the canonical Redis URL rather than an edge case, since Redis
# had no usernames before ACLs in 6.0, so most `REDIS_URL` values still look like
# that. Requiring a username sent those passwords out verbatim (#440). Do not
# restore the `+`: it is the one userinfo form the rest of this pattern already
# handles correctly.
#
# The PASSWORD side stays `+` deliberately. `://user:@host` has an empty password,
# which holds no secret, and matching it would emit a `[redacted]` marker for a
# blank value — claiming to have hidden something that was never there.
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
CONNECTION_STRING_PASSWORD_PATTERN = re.compile(
    r"(://(?P<cs_user>[^:@\s/]+)?:)(?(cs_user)[^@\s/]+|[^@\s/?#]+)(?=@)"
)
# A token in the USERNAME slot, with a password field present but EMPTY
# (`://<token>:@host`) — the pattern above preserves the username, so a
# credential stored there went out verbatim (#440).
#
# Both restrictions are load-bearing, and each was set by what its absence
# destroys rather than by taste:
#
#   * Only the `:@` shape. A trailing bare colon is a password field that is
#     present and deliberately empty — the token-as-username idiom. The BARE
#     `://token@host` form is left alone at ANY threshold, because length cannot
#     establish credential semantics in that position: a 16+ rule masks
#     `git+ssh://deployment-automation@git.example.com/repo`, `ssh://continuous-
#     integration@build...`, `https://first.last+alerts@example.com`, and
#     `docker://prometheus-operator@sha256:...` (a NAME@DIGEST ref, not userinfo
#     at all) — identities every one. Raising the threshold only changes which
#     identities get destroyed. That position is already covered for every
#     credential shape this module RECOGNIZES, since the vendor patterns match
#     `ghp_`/`AKIA`/`sk-`/`xoxb-` wherever they appear; what remains is a
#     generic opaque string, which is precisely what cannot be told apart from a
#     long username. Leaving it is this module's documented best-effort boundary.
#
#   * The 16-character gate. `:@` alone does not imply a token — RFC 1738 spells
#     `ftp://foo:@host` as username `foo` with an empty password — so an ungated
#     match masks `ftp://anonymous:@host` and `postgres://readonly:@db/app`. 16 is
#     not a new constant; it is already this file's credential threshold, in
#     LABELLED_VALUE_PATTERN's value run.
#
# `?` and `#` are excluded here although the PASSWORD class admits them, because
# the two runs play different roles. This run is the text being REPLACED, so it
# has to stop at the end of the authority or a query carrying an `@` is masked as
# userinfo — `https://example.com?email=a.b+c@example.org` would collapse to
# `https://[redacted: secret value]@example.org`, hiding the host. A password may
# legitimately contain `?` and `#`, so narrowing that class would lose coverage.
CONNECTION_STRING_USERNAME_TOKEN_PATTERN = re.compile(r"(://)[^:@\s/?#]{16,}(?=:@)")

# Named — like the two connection-string matchers above, and for the same reason
# `LABELLED_VALUE_PATTERN` is: the #446 trailing-safe-set selection (see
# `_LABELLED_SAFE_TERMINATORS` below) has to compare a candidate's originating pattern by
# IDENTITY, and an anonymous `re.compile(...)` inline in `SECRET_VALUE_PATTERNS` cannot be
# named twice without drifting. Shares `_VALUE_CHARS`' character class, hand-spelled here
# rather than referencing the constant, matching how this pattern has always been written —
# unchanged by this naming.
AUTHORIZATION_BEARER_PATTERN = re.compile(
    r"(?i)(Authorization:\s*Bearer\s+)[A-Za-z0-9._~+/=-]{16,}"
)

SECRET_VALUE_PATTERNS = [
    # ORDER IS NO LONGER SEMANTICALLY LOAD-BEARING (#445), and `_redact_secret_values` is
    # what makes that true: it runs every pattern against the ORIGINAL text, collects the
    # spans they would replace, and merges those before rebuilding the line. The merged span
    # set is a union computed per pattern independently, so it cannot depend on this list's
    # order. What the list still decides is which SHAPES are recognized — not which matcher
    # wins a race. The two connection-string matchers keep their position for readability and
    # because #443's tests address them by name; moving them now changes no output.
    #
    # What follows is the history that shaped this list, then the invariant that replaced it.
    #
    # HISTORY (#443). These patterns used to be applied one `re.sub` pass each, in this order,
    # and `sub` never revisits consumed text. Every matcher below them is substring-oriented:
    # it recognizes a credential's SHAPE wherever it appears, including in the middle of a
    # longer value. When one of them fired inside a connection-string password, its marker —
    # which contains a space and a colon — split the credential in two, and both runs below
    # stop at whitespace, so neither could match what was left. The tail shipped intact,
    # behind a marker claiming the value had been handled:
    #
    #     redis://u:token=s3cr3tvalue0123456789%2Ftailsegment@host
    #     -> redis://u:token=[redacted: secret value]%2Ftailsegment@host
    #
    # Matching the COMPLETE userinfo span before anything could fragment it closed that, and
    # at the time it had to be an ordering: the defect is a property of the pipeline, not of
    # any one matcher, so no per-pattern tweak reached it. But an ordering only chooses which
    # member of the interference class bites. #445 was the mirror image — the vendor matchers
    # ahead of LABELLED_VALUE_PATTERN rather than behind the userinfo runs — with a narrower
    # value class consuming a PREFIX of a value the labelled pattern covers whole:
    #
    #     token=ghp_<20 chars>-tailsegment -> token=[redacted: secret value]-tailsegment
    #
    # WHY RUNNING THE USERINFO MATCHERS FIRST WAS SAFE, kept because the test it names is
    # still live. Nothing was lost by running them early: a later candidate was either inside
    # the span they replace — where the marker already covered it — or entirely outside it,
    # since `sub` rescanned the whole string on each pass. The remaining case was a candidate
    # STRADDLING the span's boundary, and that could not arise: the boundary characters are
    # the `:` opening the password and the `@` closing it, and no other matcher's REPLACED run
    # contains either. Where a `:` does appear in another matcher's match — `Authorization:`
    # and a labelled key — it sits in the PRESERVED group, never the replaced run.
    #
    # That single property was the whole argument, and it is checked by
    # `test_no_other_matcher_can_straddle_a_userinfo_boundary` rather than asserted here. Under
    # the merge that test is a pattern-class TRIPWIRE rather than the safety argument — a
    # straddling candidate is now merged with the span it crosses, not stranded by it — but
    # resist restating it as a claim about the character CLASSES either way: two successive
    # review rounds caught a broader version of this sentence being false — the password run
    # below admits `:` on purpose (so `postgres://user:p1:p2:p3@host` redacts whole), and the
    # PEM matcher's span contains spaces, so neither "these runs exclude `:`" nor "every value
    # class is a subset of `[A-Za-z0-9._~+/=-]`" is true. The narrow claim is.
    #
    # THE INVARIANT THAT REPLACED THE ORDERING (#445). Each candidate contributes the span it
    # would REPLACE — the text after the preserved group 1, or the whole match when there is
    # no group. Spans are sorted and folded on STRICT overlap, and one marker is emitted per
    # merged interval. Three consequences are load-bearing:
    #
    #   * TOUCHING SPANS DO NOT MERGE. Two candidates that abut emit two markers, which is
    #     byte-for-byte what two successive `re.sub` passes did.
    #   * A PRESERVED PREFIX SURVIVES POSITIONALLY, not because a matcher hands it back:
    #     group 1's text is simply outside every replaced span, so it is copied through from
    #     the original — and it disappears when some OTHER candidate's span covers it, which
    #     is exactly how a wider match now absorbs a narrower one's leftovers.
    #   * THE #421 EXEMPTION IS JUDGED ON THE ORIGINAL LINE, because `finditer` hands
    #     `_is_code_reference` a `match.string` no earlier substitution has touched. That
    #     removes exemptions an earlier marker used to manufacture (a marker's `]` stopped
    #     `_LABEL_LEAD_RE` from reading back to the sensitive word) — the fail-closed
    #     direction, pinned by
    #     `test_exemption_is_judged_against_the_original_line_not_the_accumulator`.
    #
    # Two limits survive the merge unchanged. It cannot repair input that arrives ALREADY
    # fragmented — a marker is not recoverable, so a second pass over old output does not
    # help. And it only closes a credential some single pattern can SPAN: a userinfo value
    # carrying a character both connection-string runs exclude (`/`, or `?`/`#` on the arms
    # that stop there) produces no candidate covering it, so an earlier matcher firing on its
    # prefix still leaves the remainder beside a marker. That residue is #446, tracked
    # separately. The intra-pattern swallow (#436) is likewise untouched: it is one match's
    # own span, and merging spans cannot widen a match.
    #
    # Adding a matcher here no longer reopens #443 for whatever it recognizes, but the
    # sentinel sweep in tests/test_redaction.py still checks its payload set against this list
    # and will fail if you add one, so a new matcher has to be given a payload (or classified
    # as unable to reach userinfo).
    CONNECTION_STRING_PASSWORD_PATTERN,
    CONNECTION_STRING_USERNAME_TOKEN_PATTERN,
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    AUTHORIZATION_BEARER_PATTERN,
    LABELLED_VALUE_PATTERN,
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # Unlabeled secrets caught by shape alone (#73), independent of an adjacent label.
    # JWT: three base64url segments after the `eyJ` ("{" base64) header marker.
    #
    # The FIRST segment alone is bounded (#439): it used to be `{8,}`, unbounded like
    # the other two, which made this quadratic on repeated-anchor text. On input made
    # of nothing but `eyJ`, every anchor position scanned the unbounded run to the end
    # of the string hunting a `.` that never comes, then backtracked one character at a
    # time before giving up and trying the next anchor — the same shape #438's scheme
    # run had. Measured: `"eyJ"*26666` (80k chars) 1432ms, `"eyJ"*53332` (160k) 5676ms,
    # ~4x per 2x input — quadratic, and reachable from untrusted model output the same
    # way #438 was (`redact_text` runs on it via `redact_tree`, orchestration.py:127,
    # :234), so a liveness/DoS concern: a sync tool that blows its deadline loses its
    # paid work.
    #
    # Bounding seg1 at 512 caps per-anchor work AND caps how many anchors ever reach
    # seg2, which is what kills the quadratic blowup rather than just capping one
    # match's cost: `"eyJ"*26666` 1432ms -> 18ms, `"eyJ"*53332` 5676ms -> 37ms (~2x per
    # 2x = linear). Real JWTs, a 5000-char-payload JWT, an embedded (`xxx`+JWT) match,
    # and a JWT with a nested `eyJ` inside its payload all still match, and the embedded
    # match's span is unchanged.
    #
    # 512 (post-`eyJ` characters, so 515 total for the first segment) rather than a
    # tighter bound: real JOSE headers are base64url of compact JSON — typically 20-60
    # chars, ~120 with `kid`/`jku` — so 512 is ~8x generous. The `x5c`
    # certificate-chain-in-header outlier exceeds any sane bound and is the accepted,
    # pinned boundary; such a token is still caught when labelled (`token=…`) or on an
    # `Authorization: Bearer` line by those patterns.
    #
    # seg2/seg3 stay unbounded on purpose: payloads are legitimately KBs, seg3 is
    # greedy with no follower so it has no backtrack pressure, and seg2's backtracking
    # is transitively bounded once seg1 is capped. A possessive quantifier (`{8,}+`)
    # and an atomic group (`(?>...)`) are both available on this repo's supported
    # Pythons (3.11+), but neither fixes this: both only suppress backtracking
    # *within one match attempt*, not the fresh scan repeated at every successive
    # anchor position, which is the actual source of the quadratic blowup. A
    # `(?<![A-Za-z0-9_-])` left-context lookbehind was also rejected: it does kill the
    # quadratic blowup, but it is a coverage NARROWING — it stops matching an embedded
    # `xxxeyJ…` token that today redacts — so it became the sweep's sensitivity control
    # instead of the fix.
    re.compile(r"eyJ[A-Za-z0-9_-]{8,512}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    # Vendor key prefixes: OpenAI (sk-, sk-proj-), Stripe (sk_live_/sk_test_),
    # Google (AIza). `{n,}` rather than a fixed length so a longer/variant token
    # can't leave a trailing suffix unredacted.
    re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35,}"),
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


# The PLAIN marker: this module found no AFFIRMATIVE, grammar-level reason to suspect the
# match stopped short. NOT a proof of completeness — it cannot be one, and does not claim to
# be (#446, recalibrated after a Codex confirm-round finding: the earlier prose here read as
# a completeness claim the mechanism can't back up). A free-form labelled secret — a
# passphrase, an internal token — can legitimately contain almost any character, including
# ones this marker's own checks treat as safe (whitespace, a closing quote/bracket, a comma).
# Treating THOSE as suspect too would not sharpen the signal, it would erase it: virtually
# every labelled value in ordinary prose is followed by one of them, so a heuristic that
# flagged all of them would fire on nearly every redaction and stop meaning anything —
# `test_shared_safe_terminators_keep_the_plain_marker_for_labelled_values` and the boundary
# characterizations pin this as a deliberate calibration, not an oversight. Two markers ship
# from this module — see `_PARTIAL_SECRET_VALUE_MARKER` immediately below for the other, and
# `_interval_is_partial` for the authoritative decision between them. A constant rather than
# an inline literal, so the string the idempotency arguments throughout this file depend on
# (it carries a space and a `:`, which every userinfo run stops at) cannot drift between the
# two emission sites it used to have.
_SECRET_VALUE_MARKER = "[redacted: secret value]"

# The PARTIAL marker: this module found AFFIRMATIVE evidence the match may have stopped
# short of the true credential, from either of two checks (#446, full decision in
# `_interval_is_partial`) — not merely "some character this heuristic doesn't universally
# recognize as safe": a TRAILING follower the matched pattern's own alphabet demonstrably
# could NOT have produced (the value may continue past the marker), or a LEADING preceding
# character that COULD continue the same token, when the interval starts at a whole-match
# candidate (the match may have begun mid-token). A userinfo credential carrying a character
# the connection-string runs exclude (`/`, or `?`/`#` on the arms that stop there) can never
# be SPANNED by those matchers, so an earlier matcher firing on its prefix leaves the
# remainder beside a marker; that shape is what the trailing check exists to be honest about,
# and `_interval_is_partial` has the full decision for both checks — including why the
# trailing evidence bar differs by candidate type. Deliberately NOT a substring of
# `_SECRET_VALUE_MARKER` — pinned by `test_partial_marker_does_not_contain_the_plain_marker`
# — so an `in`-style assertion elsewhere cannot mistake one for the other.
_PARTIAL_SECRET_VALUE_MARKER = "[redacted: possibly partial secret value]"

# The trailing-check safe set (#446): a character right after a merged interval that is NOT
# safe for that interval's TRAILING-EDGE candidate means the replaced text may be a truncated
# fragment of a longer secret rather than the whole of it. Two sets, not one — a Codex review
# round found the original single global set unsound: `@`/`&`/`;`/`\` are genuinely terminal
# for a USERINFO/connection-string candidate (its own grammar closes there — `user:pass@host`
# by RFC 3986), but for a LABELLED or Bearer candidate, whose value alphabet is the generic
# `_VALUE_CHARS` catch-all rather than a specific credential's grammar, those same characters
# are exactly as plausible as an INTERIOR character the alphabet simply cannot express —
# treating them as safe there repeated the honesty failure this module exists to close:
# `password=<16+ chars>@tailsegment` must not read as complete merely because `@` happens to
# close userinfo ELSEWHERE in this file. Both sets derive from one shared base so they cannot
# silently diverge; `_interval_is_partial` selects between them per merged interval.

# Members safe for EVERY candidate type: none of them can be a legitimate interior character
# of any secret shape this module recognizes — whitespace, a closing quote (`"` `'`), a closing
# bracket/brace (`)` `]` `}`), a comparison/closing angle bracket (`>`), and a plain list
# separator (`,`).
_SHARED_SAFE_TERMINATORS = frozenset(" \t\n\r\v\f" + "\"'),]}>")

# Members safe ONLY when the trailing edge is a USERINFO/connection-string candidate: `@`
# closes userinfo (`user:pass@host`, `token:@host`) by RFC 3986 — nothing after it can be part
# of the credential a userinfo pattern matched. `&`, `;`, and `\` join it for the same
# reasoning even though — measured — none is reachable as an actual immediate follower of
# either connection-string pattern shipped today: both classes ADMIT all three as ordinary
# password/token characters, so a real occurrence is consumed into the match rather than
# stopping it early. Kept anyway, to record the intended classification ahead of a future
# userinfo-shaped pattern that DOES exclude them, rather than leaving it undocumented until
# one exists.
_USERINFO_ONLY_SAFE_TERMINATORS = frozenset("@&;\\")

# The set used for a userinfo/connection-string trailing edge, and the DEFAULT for every other
# candidate type this fix does not touch — the vendor/JWT/PEM whole-match patterns, whose
# character classes ARE the credential's real grammar (a vendor's own spec, not a generic
# catch-all), so a character their class excludes is a genuine boundary rather than a
# value-alphabet artifact. Settled by three plan-review rounds for its ORIGINAL (still correct
# for this scope) members; do not "simplify" it.
_SAFE_TERMINATORS = _SHARED_SAFE_TERMINATORS | _USERINFO_ONLY_SAFE_TERMINATORS

# The narrower set used when the trailing edge is a LABELLED or Bearer candidate:
# `_SHARED_SAFE_TERMINATORS` alone, dropping `@`/`&`/`;`/`\`. Both patterns share
# `_VALUE_CHARS` (or, for Bearer, its hand-spelled equivalent) as their value alphabet, but the
# two reach the same conclusion for different reasons. For LABELLED, that alphabet really is a
# generic "looks like a secret" catch-all with no defined grammar of its own, so a character it
# excludes is exactly as likely to be a real interior character the class cannot express as it
# is to be an actual boundary. For Bearer, the alphabet is not generic at all — `[A-Za-z0-9._~
# +/=-]` is exactly RFC 6750's `b64token` character set — so the over-caution is narrower: a
# b64token is base64(url)-encoded, meaning it can carry ARBITRARY bytes, so its interior can
# look like anything a decoder downstream chooses to make of it. A character the token alphabet
# excludes is therefore still not provably a boundary rather than more of the encoded payload —
# the conclusion matches LABELLED's even though the class itself is exact, not heuristic.
_LABELLED_SAFE_TERMINATORS = _SHARED_SAFE_TERMINATORS

# The leading-continuation class (#446, a round-2 plan-review finding): `_VALUE_CHARS` MINUS
# `=`. Derived from `_VALUE_CHARS` — not retyped — so the two classes cannot silently diverge
# if `_VALUE_CHARS` ever changes; `_VALUE_CHARS` is `[A-Za-z0-9._~+/=-]`, so stripping its
# brackets and removing `=` leaves exactly this set. `=` is excluded on purpose (a round-3
# finding): it is an assignment delimiter that legitimately abuts a COMPLETE vendor token
# (`token=ghp_...`), so treating it as a leading-continuation character would wrongly mark
# that credential partial. Every other member of `_VALUE_CHARS` can be an interior character
# of a longer secret, so one of THOSE sitting immediately before a whole-match candidate means
# the match may have started mid-token — the #439-bounded JWT's mid-token match
# (`xxxeyJ…`) is the case this exists for.
_LEADING_CONTINUATION_RE = re.compile("[" + _VALUE_CHARS[1:-1].replace("=", "") + "]")


def _replaced_span(match: re.Match) -> tuple[int, int]:
    """The span ``match`` would replace: what follows its preserved group 1, or the whole
    match when the pattern has no group.

    The projection is VALIDATED rather than assumed, and falls back to the full match span on
    violation. It is only sound when group 1 is a LEADING, PARTICIPATING prefix of the match —
    true of all four grouped patterns in ``SECRET_VALUE_PATTERNS``, but a property of those
    patterns rather than of this engine, so a pattern added later (or substituted by a caller)
    can break it. Both violation modes leak, which is why they are checked rather than
    documented (the #445 review found them; tracked as #456):

    * **A group that is not at the match start** (``AKIA[0-9A-Z]{16}(_hint:)…``) puts
      ``end(1)`` in the MIDDLE of the match, so everything before the group — the credential
      itself — is copied through as preserved text while the marker covers only the tail.
    * **A truthy ``lastindex`` with a non-participating group 1**
      (``(?:(pre:)|(alt:))…``, where the second branch matched) gives ``span(1) == (-1, -1)``.
      The projection then carries a negative start, which slices from the wrong end of the
      line: the secret survives whole and a stray marker appears beside it.

    Over-redaction is the safe direction for a secret boundary, so a violating candidate takes
    the WHOLE match rather than being skipped (which would leak) or raising (which would turn
    a redaction pass into an outage on prose it was only meant to mask).
    """
    if not match.lastindex:
        return match.span()
    group_start, group_end = match.span(1)
    if group_start == match.start() and 0 <= group_end < match.end():
        return group_end, match.end()
    return match.span()


def _interval_is_partial(
    line: str, start: int, end: int, leading_whole: bool, trailing_narrow: bool
) -> bool:
    """Whether the merged interval ``line[start:end]`` gets the PARTIAL marker rather than
    the plain one (#446) — the authoritative decision point for both, and for the documented
    semantics each one carries.

    Neither marker is a proof. The PLAIN marker means this function found no AFFIRMATIVE,
    grammar-level reason to suspect the match stopped short — never that it verified
    completeness, which it structurally cannot: a free-form labelled secret can legitimately
    contain almost any character, including ones the checks below treat as safe. The PARTIAL
    marker means it found such a reason — a follower or leader character the matched
    pattern's own alphabet demonstrably could not have produced. Both checks are heuristic
    hedges, consistent with this module's "best-effort... NOT a guarantee" contract (module
    docstring); where the checks' own mechanics are ambiguous (a tie between candidates —
    see ``_redact_secret_values``'s fold), the tie resolves toward the PARTIAL marker —
    over-hedging, not under-hedging, is the safe direction for a secret boundary. What the
    checks do NOT do is treat every character a value's alphabet excludes as suspect:
    whitespace, a closing quote/bracket, and a comma follow essentially every labelled value
    in ordinary prose, so flagging those too would fire on nearly every redaction and erase
    the signal rather than sharpen it — a deliberate calibration, not an oversight, pinned by
    the boundary characterizations near ``test_a_credential_the_userinfo_runs_cannot_span_
    is_only_partly_redacted``. Either check below is independently sufficient.

    **Trailing**: the character right after the interval exists and is not one of the
    safe-terminator characters for the interval's TRAILING-EDGE CANDIDATE TYPE. An absent
    follower (end of string) is unconditionally treated as no evidence of truncation. Which
    SET of safe terminators applies depends on ``trailing_narrow`` (see the fold in
    ``_redact_secret_values`` for how that is derived across a tie): a LABELLED or Bearer
    trailing edge uses the narrower ``_LABELLED_SAFE_TERMINATORS`` — its value alphabet
    (`_VALUE_CHARS`, or Bearer's RFC 6750 `b64token` character set) is not a specific
    credential's own grammar the way a vendor prefix or userinfo delimiter is, so a character
    it excludes is weaker (but not zero) evidence of a real boundary there — while everything
    else — userinfo/connection-string candidates, and the vendor/JWT/PEM whole-match patterns
    whose classes ARE a specific credential's grammar — uses the wider ``_SAFE_TERMINATORS``.

    **Leading**: the interval's earliest-starting covered candidate is a whole-match one
    (``leading_whole`` — derived the same way as ``trailing_narrow``, across a tie) AND the
    character right before the interval exists and is in the leading-continuation class. A
    whole-match candidate's span start is the true start of what the pattern matched, so a
    continuation character sitting right before it is affirmative evidence the match itself
    began mid-token. A candidate whose span was instead pinned at a preserved group's end (a
    label, `://user:`, `Bearer `) does not carry this evidence — that boundary is deliberate,
    not an artifact of the pattern's own reach — which is why a prefix-preserving candidate is
    excluded rather than merely deprioritized.
    """
    safe_terminators = _LABELLED_SAFE_TERMINATORS if trailing_narrow else _SAFE_TERMINATORS
    trailing = end < len(line) and line[end] not in safe_terminators
    leading = (
        leading_whole and start > 0 and _LEADING_CONTINUATION_RE.match(line[start - 1]) is not None
    )
    return trailing or leading


def _redact_secret_values(line: str, *, exempt_code: bool = False) -> tuple[str, bool]:
    """Replace inline secret-looking values. ``exempt_code`` leaves provable code
    references intact — only sound for a line of source (a diff body line), so callers
    handling arbitrary prose must leave it False (#421).

    Every pattern is matched against ``line`` ITSELF rather than against the previous
    pattern's output (#445). A sequential ``re.sub`` pipeline lets an earlier, narrower
    matcher consume a PREFIX of a value a later one covers whole, and ``sub`` never revisits
    consumed text, so the tail shipped beside a complete-looking marker. Candidate spans are
    collected from the original text, merged, and the line rebuilt with one marker per merged
    interval — ``SECRET_VALUE_PATTERNS``' header has the full semantics and the history.

    Neither the merge NOR the marker choice can repair a value no single pattern's candidate
    ever covered — a userinfo credential carrying `/` (or, on the arms that stop there,
    `?`/`#`) still ends up only partly replaced (#446). What ``_interval_is_partial`` adds is
    honesty about that: the emitted marker says so instead of claiming completeness it does
    not have.

    ``SECRET_VALUE_PATTERNS`` is read at CALL time rather than bound at import, because the
    tests substitute it; precompiling the list into one merged automaton would defeat that,
    and would also lose the per-pattern identity the ``exempt_code`` test below turns on.
    """
    # Each candidate carries two marker-choice flags alongside its span, threaded through the
    # merge below (#446):
    #
    #   * ``whole_match`` — whether the candidate is a "whole-match" one: its REPLACED span
    #     starts at the match's own start, so nothing was stripped off the front.
    #     ``span_start == match.start()`` rather than ``not match.lastindex``: the two agree
    #     for every ordinary case, but they diverge in the #456 fallback, where a grouped
    #     pattern violates the leading-participating-prefix invariant and ``_replaced_span``
    #     falls back to the FULL match span. There ``lastindex`` is still truthy (a group DID
    #     match, just not usably), which would wrongly read as prefix-preserving and suppress
    #     the leading check — but the returned span really does start at the match's true
    #     beginning, so it deserves the same leading-check treatment as an ungrouped pattern.
    #     Testing the span directly gets the fallback case right for free.
    #   * ``narrow_trailing`` — whether the candidate is a LABELLED or Bearer one, compared by
    #     identity (the two patterns whose value alphabet is the generic `_VALUE_CHARS`
    #     catch-all rather than a specific credential's grammar — see
    #     `_LABELLED_SAFE_TERMINATORS`'s header). Every other candidate — userinfo/
    #     connection-string, and the vendor/JWT/PEM whole-match patterns — uses the wider set.
    candidates: list[tuple[int, int, bool, bool]] = []
    for pattern in SECRET_VALUE_PATTERNS:
        # The #421 exemption belongs to exactly one pattern, compared by identity as always.
        exempting = exempt_code and pattern is LABELLED_VALUE_PATTERN
        narrow_trailing = pattern is LABELLED_VALUE_PATTERN or (
            pattern is AUTHORIZATION_BEARER_PATTERN
        )
        for match in pattern.finditer(line):
            if exempting and _is_code_reference(match):
                continue  # an exempted candidate contributes no span
            span_start, span_end = _replaced_span(match)
            candidates.append((span_start, span_end, span_start == match.start(), narrow_trailing))
    if not candidates:
        return line, False

    # Fold left over candidates sorted by (start, end). STRICT overlap merges; touching does
    # not, so abutting candidates keep two markers exactly as two `re.sub` passes did. No span
    # can be empty: every pattern in SECRET_VALUE_PATTERNS requires literal characters after
    # its preserved group, so a zero-length candidate cannot occur and nothing here handles
    # one.
    #
    # ``leading_whole`` tracks whether ANY candidate tied for the merged interval's leftmost
    # start is a whole-match one. A tie is not hypothetical — a vendor pattern with no group
    # (`gh[pousr]_...`) and the labelled pattern's group-bounded candidate
    # (`token=gh[pousr]_...`) commonly start at the identical position — and resolving it by
    # OR-ing rather than by picking whichever candidate happened to sort first keeps the
    # marker choice independent of ``SECRET_VALUE_PATTERNS``' list order, matching the
    # order-invariance #445 already established for the span set itself. Only a candidate
    # that starts at that same leftmost position can affect it; one merged in later (because
    # its span overlaps the interval already grown by a wider candidate) says nothing about
    # how the interval's LEFT edge arose.
    #
    # OR, not AND: the fail-closed direction for a secret boundary is to PREFER marking
    # partial, so one tied whole-match candidate is enough to enable the leading check even
    # when another tied candidate is prefix-preserving. On every pattern shipped today this
    # choice is observationally unpinnable end-to-end — AND, OR, and "keep whichever candidate
    # sorts first" all produce the identical marker for every real tie, because a real tie
    # between a whole-match and a prefix-preserving candidate only arises where the
    # prefix-preserving one's own separator (`=` before a labelled value, `://user:` before a
    # connection-string password) is EITHER excluded from the leading-continuation class
    # (`=`) or already decided by the trailing check via that same matcher's `(?=:@)` closer
    # — a structural coupling between this module's matchers, not a property of this engine,
    # so it is recorded here rather than relied on silently. `test_tie_fold_prefers_partial_
    # when_any_tied_candidate_is_whole_match` pins OR directly with a synthetic tie that
    # isolates the tie-break from both of those escapes.
    #
    # ``trailing_narrow`` mirrors ``leading_whole`` for the interval's RIGHT edge: it tracks
    # whether ANY candidate that currently achieves the merged interval's maximum ``end`` is a
    # narrow-trailing one, OR-ed the same way and for the same fail-closed reason (prefer the
    # set that makes MORE characters unsafe). Unlike ``leading_whole`` this has to be
    # re-evaluated on every span, not only at ties: the candidate that ends up owning the
    # interval's end can be discovered anywhere in the fold, not just among those sharing the
    # leftmost start. A strictly larger ``span_end`` replaces the running flag outright (the
    # previous candidates no longer decide the end at all); an EQUAL one OR-s in; a smaller one
    # is irrelevant to the end and changes nothing.
    candidates.sort(key=lambda c: (c[0], c[1]))
    merged: list[tuple[int, int, bool, bool]] = []
    start, end, leading_whole, trailing_narrow = candidates[0]
    for span_start, span_end, whole_match, narrow_trailing in candidates[1:]:
        if span_start < end:
            if span_end > end:
                end = span_end
                trailing_narrow = narrow_trailing
            elif span_end == end:
                trailing_narrow = trailing_narrow or narrow_trailing
            if span_start == start:
                leading_whole = leading_whole or whole_match
        else:
            merged.append((start, end, leading_whole, trailing_narrow))
            start, end = span_start, span_end
            leading_whole, trailing_narrow = whole_match, narrow_trailing
    merged.append((start, end, leading_whole, trailing_narrow))

    # Rebuild from the ORIGINAL text. A preserved prefix survives because it lies outside
    # every merged interval, not because a replacement emitted it — so a wider candidate
    # covering it takes it with the secret, which is the whole point of #445.
    out: list[str] = []
    cursor = 0
    for span_start, span_end, interval_leading_whole, interval_trailing_narrow in merged:
        out.append(line[cursor:span_start])
        marker = (
            _PARTIAL_SECRET_VALUE_MARKER
            if _interval_is_partial(
                line, span_start, span_end, interval_leading_whole, interval_trailing_narrow
            )
            else _SECRET_VALUE_MARKER
        )
        out.append(marker)
        cursor = span_end
    out.append(line[cursor:])
    return "".join(out), True


def redact_text(text: str | None) -> str | None:
    """Best-effort inline secret-value redaction for free-text (no diff/file logic).

    Applies only the inline ``SECRET_VALUE_PATTERNS`` — the same value replacement
    used on diff body lines — to arbitrary prose Codex returns (summaries, answers,
    raw_response text, finding fields). File-hunk dropping does not apply to prose,
    so only inline values are replaced — with ``[redacted: secret value]`` when no
    affirmative, grammar-level sign was found that the replaced span stopped short (NOT
    a claim the value is provably complete — it cannot be one), or
    ``[redacted: possibly partial secret value]`` when such a sign was found (#446): see
    ``_interval_is_partial`` for the authoritative decision between the two and what
    each marker does and does not claim. ``None`` and empty strings pass through
    unchanged. Defense-in-depth, NOT a guarantee (consistent with this module's
    contract)."""
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
