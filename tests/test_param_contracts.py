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


class TestDeveloperInstructionsCompliance:
    """#563: the compliance limit must be stated generally, not only for verdicts.

    Before this, the only "instructed, not compelled" statement anywhere on the surface was
    scoped to VERDICTS, so a caller read it as "verdicts are protected, the rest applies".
    A first attempt whose stance the model discarded in full is then indistinguishable, to
    that caller, from a parameter that does nothing.

    The presence assertions alone would pass a confident INVERSION — text that keeps the
    vocabulary while promising compliance — so each one is paired with a guard against the
    opposite claim. (A marker-word guard catching omission but not inversion is a failure
    mode this repo has shipped before.)
    """

    #: Claims the surface must never make: the model is not compelled by this text, so no
    #: wording may promise that it follows, obeys, or is bound by the caller's stance.
    FORBIDDEN = (
        r"\bcompelled to (?:follow|honou?r|obey|comply)\b",
        r"\bguarantees? (?:that )?(?:codex|the model) (?:will )?(?:follow|honou?rs?|obey|comply)",
        r"\b(?:codex|the model) (?:will|must) (?:always )?"
        r"(?:follow|honou?r|obey|comply)",
        r"\balways (?:followed|honou?red|obeyed)\b",
        # Named in this class's docstring, so the blocklist must actually carry them
        # (a Codex review caught the guard promising more than it checked).
        r"\b(?:codex|the model) is bound by\b",
        r"\b(?:required|obliged) to comply\b",
        r"\breliably (?:applied|followed|honou?red|obeyed)\b",
    )

    #: Every shape the blocklist claims to reject, asserted against it directly — a
    #: blocklist nobody probes is a guard that cannot be shown to fire.
    INVERSION_SAMPLES = (
        "compliance is best-effort, but Codex will follow it in practice",
        "best-effort and reliably applied in practice",
        "the model is bound by the caller text",
        "the caller text is best-effort; Codex is required to comply",
        "this server guarantees that Codex will honor the stance",
        "the stance is always followed",
        "Codex is compelled to comply with the text",
    )

    def test_the_blocklist_rejects_every_inversion_it_claims_to(self):
        """A negative guard needs a positive control: each sample must trip some pattern."""
        for sample in self.INVERSION_SAMPLES:
            with pytest.raises(AssertionError):
                self._forbid_inversions(sample, "sample")

    def test_the_blocklist_accepts_the_shipping_text(self):
        """...and must not fire on the wording actually shipped (no false positive)."""
        c = param_contracts.PARAMETER_CONTRACTS["developer_instructions"]
        self._forbid_inversions(c.summary, "inline summary")
        self._forbid_inversions(c.full, "full contract")

    def _forbid_inversions(self, text: str, where: str) -> None:
        for pattern in self.FORBIDDEN:
            assert not re.search(pattern, text, re.IGNORECASE), (
                f"{where} promises compliance ({pattern!r}) — the caller text is "
                "best-effort; only the SEND is guaranteed"
            )

    #: Pinned literally, NOT derived from the source — a guard that reads its expectation
    #: from the text it guards cannot fail when that text is wrong. The blocklist above
    #: catches the inversions it enumerates; this catches the ones it does not (a reword to
    #: "best-effort and reliably applied in practice" keeps every marker word, states the
    #: opposite, and passes every other assertion here). Brittle BY DESIGN, like
    #: EXPECTED_MANIFEST_HASH: any reword fails, and the author re-reads #563 before
    #: repinning.
    CANONICAL_SUMMARY_CLAIM = "compliance with the rest is best-effort and may be silent"
    CANONICAL_FULL_CLAIM = "compliance with the caller-supplied text is BEST-EFFORT"

    def test_canonical_compliance_claims_are_pinned(self):
        """Any reword of the load-bearing claim must be acknowledged, not slipped through."""
        c = param_contracts.PARAMETER_CONTRACTS["developer_instructions"]
        for claim, text, where in (
            (self.CANONICAL_SUMMARY_CLAIM, c.summary, "inline summary"),
            (self.CANONICAL_FULL_CLAIM, c.full, "full contract"),
        ):
            assert claim in text, (
                f"the {where}'s compliance claim was reworded away from {claim!r}. This "
                "pin is deliberate: re-read #563, confirm the new wording still says "
                "compliance is best-effort (and does not promise it), then repin here."
            )

    def test_summary_carries_the_best_effort_limit(self):
        """The compressed inline summary is the primary read path (tools/list).

        A caller who never fetches codex://params sees only this, so the limit cannot
        live exclusively in the full contract.
        """
        summary = param_contracts.PARAMETER_CONTRACTS["developer_instructions"].summary
        assert re.search(r"\bbest[- ]effort\b", summary, re.IGNORECASE), (
            "the inline summary dropped the best-effort compliance limit (#563)"
        )
        self._forbid_inversions(summary, "the inline summary")

    def test_summary_limit_is_not_scoped_to_verdicts_alone(self):
        """The pre-#563 defect exactly: a verdict-only carve-out reads as a promise
        about everything else. The limit must reach past the verdict sentence."""
        summary = param_contracts.PARAMETER_CONTRACTS["developer_instructions"].summary
        after_verdicts = re.split(r"\bverdicts?\b", summary, flags=re.IGNORECASE)[-1]
        assert re.search(r"\bbest[- ]effort\b", after_verdicts, re.IGNORECASE), (
            "the compliance limit appears only before/inside the verdict clause — a "
            "reader takes it as scoped to verdicts, which is the #563 defect"
        )

    def test_full_contract_separates_the_send_from_the_effect(self):
        """meta's fingerprint attests what the server SENT, never what the model did —
        the distinction schemas.py already draws for the meta field (#564)."""
        full = param_contracts.PARAMETER_CONTRACTS["developer_instructions"].full
        assert re.search(r"\bbest[- ]effort\b", full, re.IGNORECASE), (
            "the full contract dropped the best-effort compliance limit (#563)"
        )
        assert re.search(r"\bstaged\b|\baccepted\b", full, re.IGNORECASE) and re.search(
            r"what the model did with it", full, re.IGNORECASE
        ), (
            "the full contract must separate the attested REQUEST (accepted/staged) from "
            "what the model did with it — it cannot claim a proven send, because the "
            "fingerprint is assigned at prepare time and an async handle returns before "
            "the worker reaches codex (server.py `_start_job`)"
        )
        self._forbid_inversions(full, "the full contract")

    def test_full_contract_does_not_claim_silence_is_absolute(self):
        """The server's own framing (prompts.py `_CALLER_FRAMING`) instructs Codex to say
        so when the caller text conflicts with the rules above it, and detail="full"
        returns the raw model text. So non-compliance MAY be silent — it is not
        guaranteed to be. An absolute would contradict the prompt this server sends."""
        full = param_contracts.PARAMETER_CONTRACTS["developer_instructions"].full
        assert not re.search(
            r"non-?compliance is (?:always )?silent|silently and always|never reported",
            full,
            re.IGNORECASE,
        ), (
            "the full contract states silence as an absolute; _CALLER_FRAMING instructs "
            "Codex to disclose a conflict, so silence is possible, not guaranteed"
        )
        assert re.search(r"\bmay\b[^.]{0,80}\bsilent", full, re.IGNORECASE), (
            "the full contract must say non-compliance MAY be silent"
        )
