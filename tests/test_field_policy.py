"""The #529 machine-field control-character policy.

#528 deletes every Unicode ``Cc`` code point from echoed PROSE. That remedy is wrong for a
machine-readable field: deleting a byte from an identifier silently corrupts it, and a
``repair.arguments`` value is fed straight back into a follow-up tool call, so a corrupted id
that still looks well-formed is worse than a rejected one.

This module pins the split the codebase settled on. It is deliberately NOT one assertion over
"every string in the envelope" — the two families have opposite correct behavior, so a shared
assertion would be wrong for half of them.

REJECT: a caller-supplied input the caller can simply correct is refused at the MCP boundary,
value never echoed. Precedent: ``reasoning_effort``.

PRESERVE: a value that IS, or derives from, a real filesystem or model identity keeps its exact
bytes forever. Precedent: ``Finding.file`` (``orchestration._FINDING_PROSE_KEYS``).
"""

from __future__ import annotations

import json

import pytest

from codex_in_claude import field_policy, server

# One control character from each Cc sub-range the policy names: C0, DEL, and C1.
CC_SAMPLES = ("\x00", "\x07", "\x1b", "\x1f", "\x7f", "\x80", "\x9f")
# BEL then a CSI colour sequence: the shape that made #528's delete corrupt an id, because
# deleting ESC leaves the literal text "[31m" behind and the value still looks well-formed.
CC_PAYLOAD = "\x07\x1b[31m"

WORKSPACE = "."
# A sentinel with no English substring, so "the value did not survive" cannot be faked by a
# collision with the envelope's own boilerplate. An earlier draft used "value" and matched the
# repair prose "accepted values, then retry", reporting a leak that was not there.
SENTINEL_HEAD = "zq7kx"
SENTINEL_TAIL = "wm4vj"


def has_control_char(text: str) -> bool:
    return any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in text)


def walk_strings(value, path=""):
    """Yield (path, string) for every string in a JSON-shaped tree — values AND dict KEYS.

    Walking keys is not incidental: ``Repair.arguments`` is ``dict[str, Any]``, so a
    caller-controlled argument NAME lands in a key position that a typed leaf-walk never
    visits.
    """
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, sub in value.items():
            if isinstance(key, str):
                yield f"{path}<key>", key
            yield from walk_strings(sub, f"{path}.{key}")
    elif isinstance(value, list):
        for index, sub in enumerate(value):
            yield from walk_strings(sub, f"{path}[{index}]")


# --------------------------------------------------------------------------- #
# The registry itself
# --------------------------------------------------------------------------- #


async def test_every_reject_param_is_advertised_with_the_control_free_pattern():
    """Each REJECT parameter advertises the pattern in its own inputSchema, so a client can
    validate before spending and the refusal is not a server-side surprise."""
    schemas = await field_policy.advertised_patterns(server.mcp)
    missing = {
        name: schemas.get(name)
        for name in field_policy.REJECT_PARAMS
        if schemas.get(name) != field_policy.CONTROL_CHAR_FREE_PATTERN
    }
    assert not missing, f"REJECT params not advertising the pattern: {missing}"


def test_reject_and_preserve_families_are_disjoint():
    """A field with two dispositions has none. This is the guard that keeps a later edit from
    quietly moving a value into both lists."""
    assert not (set(field_policy.REJECT_PARAMS) & set(field_policy.PRESERVE_FIELDS))


async def test_registry_classifies_every_reject_param_that_exists():
    """Every name in REJECT_PARAMS is a real parameter on at least one tool — a typo would
    otherwise make its guard vacuous while the test above still passed."""
    known = set(await field_policy.advertised_patterns(server.mcp))
    unknown = set(field_policy.REJECT_PARAMS) - known
    assert not unknown, f"REJECT_PARAMS names no such parameter: {unknown}"


# --------------------------------------------------------------------------- #
# REJECT half — refused at the boundary, value never echoed
# --------------------------------------------------------------------------- #

# (tool, base arguments) pairs that reach validation without spending.
REJECT_CASES = [
    ("codex_job_status", {}, "job_id"),
    ("codex_job_result", {}, "job_id"),
    ("codex_job_cancel", {}, "job_id"),
    ("codex_dry_run", {"scope": "branch"}, "base"),
    ("codex_dry_run", {"scope": "commit"}, "commit"),
    ("codex_dry_run", {"scope": "working_tree"}, "model"),
    ("codex_delegate_dry_run", {"task": "t"}, "model"),
    ("codex_transfer", {}, "transcript_path"),
]


@pytest.mark.parametrize(("tool", "base_args", "param"), REJECT_CASES)
@pytest.mark.parametrize("control", CC_SAMPLES)
async def test_reject_param_refuses_control_char_without_echoing_it(
    tool, base_args, param, control
):
    """The call is refused, and the offending VALUE appears nowhere in the envelope.

    Both halves matter. Refusing while echoing the value would just relocate the leak, and
    echoing a *sanitized* value would hand back an identifier the caller never sent.
    """
    value = f"{SENTINEL_HEAD}{control}{SENTINEL_TAIL}"
    res = await server.mcp.call_tool(tool, {**base_args, param: value, "workspace_root": WORKSPACE})
    payload = res.structured_content
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_arguments"
    # The field NAME is reported (it is a static parameter name); the value is not.
    assert payload["error"]["details"]["field"] == param
    serialized = json.dumps(payload)
    assert value not in serialized
    # Nor a stripped rendering of it, which is the corruption this issue exists to prevent.
    assert value.replace(control, "") not in serialized


@pytest.mark.parametrize(("tool", "base_args", "param"), REJECT_CASES)
async def test_reject_param_envelope_is_free_of_control_characters(tool, base_args, param):
    res = await server.mcp.call_tool(
        tool,
        {
            **base_args,
            param: f"{SENTINEL_HEAD}{CC_PAYLOAD}{SENTINEL_TAIL}",
            "workspace_root": WORKSPACE,
        },
    )
    hits = [
        (path, text)
        for path, text in walk_strings(res.structured_content)
        if has_control_char(text)
    ]
    assert not hits, hits


async def test_reject_pattern_rejects_a_trailing_newline():
    """The advertised pattern ends in ``$``. Under Python's ``re`` that admits a trailing
    newline, so this guarantee rests on pydantic validating with the Rust regex engine, where
    ``$`` is end-of-text. Pin it: a change of validation engine must fail here, loudly, rather
    than silently opening a one-character hole.
    """
    res = await server.mcp.call_tool(
        "codex_job_status", {"job_id": "abc\n", "workspace_root": WORKSPACE}
    )
    assert res.structured_content["error"]["code"] == "invalid_arguments"


# --------------------------------------------------------------------------- #
# PRESERVE half — byte-exact forever
# --------------------------------------------------------------------------- #


def test_control_bearing_finding_file_is_preserved_byte_exact():
    """``Finding.file`` locates code for a reader. Deleting a byte points them at a different
    path, so the model's bytes survive untouched while the finding's prose is cleaned."""
    dirty = f"a{CC_PAYLOAD}.py"
    assert field_policy.preserve(dirty) == dirty


def test_preserve_never_shortens_a_value():
    """The failure mode this half guards is silent shortening. Any Cc-bearing input must come
    back the same length, for every Cc sub-range."""
    for control in CC_SAMPLES:
        value = f"pre{control}post"
        assert field_policy.preserve(value) == value
        assert len(field_policy.preserve(value)) == len(value)


# --------------------------------------------------------------------------- #
# Unknown argument NAMES — the one site no schema can cover
# --------------------------------------------------------------------------- #
#
# An unknown key is rejected *because* it is unknown, so there is no parameter to hang a
# pattern on. The name cannot be echoed (it is caller-controlled) and must not be stripped
# (that is the corruption), so it is withheld behind a marker plus a machine discriminator.


async def test_control_bearing_unknown_argument_name_is_withheld():
    res = await server.mcp.call_tool(
        "codex_status", {f"{SENTINEL_HEAD}{CC_PAYLOAD}{SENTINEL_TAIL}": 1}
    )
    error = res.structured_content["error"]
    assert error["code"] == "invalid_arguments"
    assert error["details"]["field"] == server._WITHHELD_FIELD
    assert error["details"]["field_withheld"] is True
    assert error["invalid_arguments"][0]["field"] == server._WITHHELD_FIELD
    assert error["invalid_arguments"][0]["field_withheld"] is True
    serialized = json.dumps(res.structured_content)
    assert SENTINEL_HEAD not in serialized
    assert SENTINEL_TAIL not in serialized


async def test_a_clean_unknown_argument_name_is_still_reported_verbatim():
    """The withholding must be narrow. A clean name stays useful, and `field_withheld` stays
    false so a client can tell "this is the name" from "the name was withheld"."""
    res = await server.mcp.call_tool("codex_status", {"definitely_not_a_param": 1})
    error = res.structured_content["error"]
    assert error["details"]["field"] == "definitely_not_a_param"
    assert error["details"]["field_withheld"] is False


def test_control_char_is_detected_before_the_length_bound():
    """`_format_loc` truncates to a byte bound. Checking after truncation would miss a control
    character sitting past the bound, so the check runs on the raw location components."""
    buried = ("x" * (server._MAX_ARG_FIELD_LEN + 50)) + "\x07"
    assert server._format_loc((buried,)) == server._WITHHELD_FIELD


def test_a_literal_withheld_marker_from_the_caller_is_itself_withheld():
    """The marker is not a reserved word, so a caller may send it as a real argument name.
    Reporting it verbatim with `field_withheld: false` would make a genuine name
    indistinguishable from a withheld one, so the marker is only ever emitted alongside the
    flag."""
    assert server._format_loc((server._WITHHELD_FIELD,)) == server._WITHHELD_FIELD
    assert server._loc_is_withheld((server._WITHHELD_FIELD,)) is True


# --------------------------------------------------------------------------- #
# Stored job replay
# --------------------------------------------------------------------------- #
#
# Boundary validation only covers a call made AFTER the fix ships. A record written before it
# stays replayable for its full TTL (24h by default), so `codex_job_result` is a second, later
# path the same value can reach. Under this policy the replay answer is not "sanitize it" — a
# stored machine identifier must come back exactly as it was written, or the replay reports a
# finding at a file that does not exist.


def test_stored_replay_preserves_machine_identifiers_byte_exact():
    """The replay pass sanitizes stored PROSE (#528) and must not touch the machine members
    alongside it. `_STORED_PRESENTATION_KEYS` handles `findings` through
    `orchestration._sanitize_finding` precisely so `file` and `severity` survive."""
    dirty_file = f"src/a{CC_PAYLOAD}.py"
    payload = {
        "ok": True,
        "summary": f"prose{CC_PAYLOAD}text",
        "findings": [
            {
                "title": f"t{CC_PAYLOAD}x",
                "severity": "low",
                "file": dirty_file,
                "evidence": "e",
                "risk": "r",
                "recommendation": "rec",
            }
        ],
    }
    out = server._sanitize_stored_presentation(dict(payload))
    finding = out["findings"][0]
    assert finding["file"] == dirty_file, "a stored identifier must replay byte-exact"
    assert len(finding["file"]) == len(dirty_file)
    # ...while the prose beside it is cleaned, which is what makes this a split and not a
    # blanket exemption.
    assert not has_control_char(out["summary"])
    assert not has_control_char(finding["title"])


def test_stored_replay_does_not_mutate_machine_members_of_meta():
    """`meta` is excluded from the replay sanitizer by design. A pre-fix record can hold a
    control character in `meta.model` or `meta.base` (the boundary now refuses new ones), and
    replaying it byte-exact reports what was actually sent rather than inventing a value."""
    meta = {"cwd": f"/repo{CC_PAYLOAD}", "model": f"gpt{CC_PAYLOAD}", "base": f"main{CC_PAYLOAD}"}
    payload = {"ok": True, "summary": "clean", "meta": dict(meta)}
    out = server._sanitize_stored_presentation(dict(payload))
    assert out["meta"] == meta
