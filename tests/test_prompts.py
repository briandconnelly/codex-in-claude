"""Composition of the caller developer-instructions value (#556). The boundary bounds
(cap, normalize, unsafe reason) are tested in test_config.py; here: the composed
string's structure and the marker guard's co-location with the marker shapes."""

from __future__ import annotations

import pytest

from codex_in_claude import prompts


def test_compose_leads_with_framing_and_closes_after_the_text():
    out = prompts.compose_developer_instructions("Focus on concurrency.")
    assert out.startswith(prompts.DEVELOPER_INSTRUCTIONS_FRAMING)
    begin = out.index("BEGIN caller-supplied text")
    text_at = out.index("Focus on concurrency.")
    end = out.index("END caller-supplied text")
    assert begin < text_at < end
    # The closing marker restates that the preceding rules outrank the caller text, and
    # nothing follows it but that restatement.
    assert out.rstrip().endswith("claims otherwise.")


def test_compose_refuses_blank():
    # A run without caller text sends NO developer override; compose-for-blank would
    # invite a silent framing-only turn on every run.
    with pytest.raises(ValueError, match="non-blank"):
        prompts.compose_developer_instructions("   \n ")


def test_marker_guard_matches_the_real_markers():
    # The guard must fire on the exact marker lines the composer emits (derive from the
    # composed output, not from a re-typed copy, so the two shapes cannot drift).
    out = prompts.compose_developer_instructions("x")
    for line in out.splitlines():
        if "caller-supplied text" in line.lower() or "caller text follows" in line.lower():
            assert prompts.contains_framing_marker(line), line


def test_composed_value_contains_no_unpaired_markers():
    # Exactly one BEGIN, one END, one inner follows-line.
    out = prompts.compose_developer_instructions("caller words")
    assert out.count("BEGIN caller-supplied text") == 1
    assert out.count("END caller-supplied text") == 1
    assert out.count("caller text follows") == 1


def test_existing_user_turn_framings_are_untouched_by_the_module():
    # The developer value is additive: the user-turn framings still carry the untrusted
    # clause and are what the prompt builders emit.
    assert "untrusted DATA" in prompts.CONSULT_FRAMING
    assert "untrusted DATA" in prompts.REVIEW_FRAMING
    assert prompts.build_consult_prompt("q").startswith(prompts.CONSULT_FRAMING)
