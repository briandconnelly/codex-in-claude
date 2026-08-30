"""Tests for the shared parameter-contract registry (issue #333).

The registry is the single source of truth for parameters whose inline
`tools/list` description is a compressed summary and whose full semantics live in
the `codex://params` resource. These tests guard that (a) every registry entry is
well-formed, (b) each tool's inline Field description is drawn from the registry
summary (so the inline and the resource cannot drift), and (c) the compressed
inline summary still carries the selection-critical facts and guarantees a client
needs on a first call.
"""

from __future__ import annotations

import re
from typing import ClassVar

import pytest

from codex_in_claude import param_contracts, server


def test_registry_is_nonempty_and_well_formed():
    contracts = param_contracts.PARAMETER_CONTRACTS
    assert contracts, "PARAMETER_CONTRACTS is empty"
    for name, c in contracts.items():
        assert c.name == name, f"{name}: contract.name mismatch"
        assert c.summary.strip(), f"{name}: empty summary"
        assert c.full.strip(), f"{name}: empty full"
        # The summary is the compressed inline form; the full is authoritative and
        # never shorter than the summary.
        assert len(c.summary) <= len(c.full), f"{name}: summary longer than full"


def test_resource_uri_is_stable():
    assert param_contracts.PARAMS_RESOURCE_URI == "codex://params"


def test_resource_body_serializes_every_contract():
    body = param_contracts.resource_body()
    assert isinstance(body, dict)
    params = body["params"]
    assert set(params) == set(param_contracts.PARAMETER_CONTRACTS)
    for name, entry in params.items():
        assert entry["summary"] == param_contracts.PARAMETER_CONTRACTS[name].summary
        assert entry["full"] == param_contracts.PARAMETER_CONTRACTS[name].full


@pytest.mark.parametrize("name", sorted(param_contracts.PARAMETER_CONTRACTS))
def test_inline_summary_points_at_the_resource(name):
    """A compressed inline summary must reference its findable full home (#333)."""
    summary = param_contracts.PARAMETER_CONTRACTS[name].summary
    assert param_contracts.PARAMS_RESOURCE_URI in summary, (
        f"{name} summary does not reference {param_contracts.PARAMS_RESOURCE_URI}"
    )


# Which server param alias each registry entry feeds. The inline description on
# these aliases must be exactly the registry summary — the anti-drift guarantee.
_ALIAS_FOR = {
    "idempotency_key": "IdempotencyKeyParam",
    "reasoning_effort": "ReasoningEffortParam",
    "extra_context": "ExtraContextParam",
    "developer_instructions": "DeveloperInstructionsParam",
}


def test_registry_covers_exactly_the_wired_aliases():
    assert set(_ALIAS_FOR) == set(param_contracts.PARAMETER_CONTRACTS)


@pytest.mark.parametrize(("name", "alias"), sorted(_ALIAS_FOR.items()))
def test_alias_description_is_the_registry_summary(name, alias):
    """The wire description IS the registry summary — one source, no drift (#333)."""
    desc = getattr(server, alias).__metadata__[0].description
    assert desc == param_contracts.PARAMETER_CONTRACTS[name].summary


def test_idempotency_summary_keeps_selection_critical_facts():
    """Compressing idempotency_key must keep what a first call needs (#333)."""
    s = param_contracts.PARAMETER_CONTRACTS["idempotency_key"].summary.lower()
    assert "workspace" in s, "dropped the tool+workspace scoping"
    assert "conflict" in s, "dropped the different-args conflict behavior"
    assert "async" in s, "dropped the sync/async-are-separate-tools fact"
    assert "bounded" in s or "not indefinite" in s, "dropped bounded-retention warning"


def test_idempotency_full_keeps_moved_lifecycle_detail():
    """The moved detail must survive in its new home, not vanish (#333)."""
    full = param_contracts.PARAMETER_CONTRACTS["idempotency_key"].full.lower()
    assert "idempotency_in_progress" in full
    assert "idempotency_result_unavailable" in full
    assert "ttl" in full
    assert "idempotency_replayed" in full


class TestAuditTwoCompaction:
    """F2: the two parameters whose inline text is long enough to be worth compressing.

    `workspace_root`, `model`, and `isolation` are deliberately NOT registered: their current
    inline descriptions are already shorter than any summary carrying the required
    `codex://params` pointer, so registering them would grow `tools/list` (measured
    2026-07-26: +70, +248, +88 bytes respectively)."""

    # The inline description each summary replaces, measured on the wire at schema-56.
    PREVIOUS_INLINE: ClassVar[dict[str, int]] = {"idempotency_key": 545, "extra_context": 342}

    def test_extra_context_is_registered(self):
        assert "extra_context" in param_contracts.PARAMETER_CONTRACTS

    @pytest.mark.parametrize("name", ["workspace_root", "model", "isolation"])
    def test_terse_parameters_stay_out_of_the_registry(self, name):
        # Guard the measurement that drove this decision: registering these grows the wire.
        assert name not in param_contracts.PARAMETER_CONTRACTS

    @pytest.mark.parametrize("name", ["idempotency_key", "extra_context"])
    def test_summary_is_materially_shorter_than_what_it_replaced(self, name):
        summary = param_contracts.PARAMETER_CONTRACTS[name].summary
        previous = self.PREVIOUS_INLINE[name]
        assert len(summary) <= previous * 0.75, (
            f"{name}: summary is {len(summary)} chars vs {previous} before — "
            "under a 25% reduction this registration costs more than it saves"
        )

    @pytest.mark.parametrize("name", ["idempotency_key", "extra_context"])
    def test_summary_is_shorter_than_its_full_text(self, name):
        c = param_contracts.PARAMETER_CONTRACTS[name]
        assert len(c.summary) < len(c.full)

    def test_extra_context_summary_keeps_the_safety_facts(self):
        # UNTRUSTED framing and the redaction gap are safety-critical (contract-checklist §3)
        # and must survive compression.
        summary = param_contracts.PARAMETER_CONTRACTS["extra_context"].summary
        assert "UNTRUSTED" in summary
        # Whole-word, case-insensitive: a bare substring like "edaction" would also match
        # a mangled/misspelled word that merely happens to end the same way.
        assert re.search(r"\bredaction\b", summary, re.IGNORECASE)

    def test_idempotency_summary_keeps_the_spend_guarantee(self):
        summary = param_contracts.PARAMETER_CONTRACTS["idempotency_key"].summary
        # Whole-word matches only — a bare substring would also match unrelated words
        # like "suspend" or "expend" that contain "spend" but don't carry the guarantee.
        assert re.search(r"\bspend\b", summary, re.IGNORECASE) or re.search(
            r"\bunpaid\b", summary, re.IGNORECASE
        )
