"""The agent-visible surface must not contradict `cli_contract.py`.

Adopted from moonbridge via the shared pontifex test kit. Description-only
defects pass every other gate — the code is right and the prose is wrong — so
these tests read the BUILT manifest (what an agent actually receives), never
source text. The phrase bans live in `cli_contract.FORBIDDEN_SURFACE_PHRASES`,
next to the facts that justify them; the pontifex conformance check also holds
the declarative `PONTIFEX_CONTRACT` to its own bans and pins its derivations
so the instance and the legacy constants can never drift apart.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pontifex.testing import conformance, surface_honesty

from codex_in_claude import cli_contract, manifest


@pytest.fixture(scope="module")
def wire_text() -> str:
    """The full agent-visible surface, as `test_manifest.py` builds it."""
    return json.dumps(asyncio.run(manifest.build_manifest()), ensure_ascii=False)


@pytest.mark.parametrize("phrase", cli_contract.FORBIDDEN_SURFACE_PHRASES)
def test_wire_prose_does_not_contradict_the_cli_contract(wire_text: str, phrase: str):
    assert surface_honesty.find_forbidden_phrases(wire_text, (phrase,)) == [], (
        f"{phrase!r} appears in the agent-visible surface. Cross-bridge vocabulary "
        "(kimi/moonbridge) means a wrong-direction backport reached the wire; a "
        "mechanism claim (applying diffs, bypassing the sandbox) contradicts what "
        "this plugin actually does."
    )


def test_contract_passes_pontifex_conformance():
    assert conformance.check_contract(cli_contract.PONTIFEX_CONTRACT) == []


def test_contract_instance_derives_from_legacy_constants():
    """The declarative PONTIFEX_CONTRACT and the constants codex.py still consumes
    are the same facts in two shapes; pin the derivation so they cannot drift."""
    c = cli_contract.PONTIFEX_CONTRACT
    assert c.bin_name == cli_contract.CODEX_BIN
    assert c.exec_argv_prefix == cli_contract.EXEC_SUBCOMMAND
    assert set(c.always_send_flags) == set(cli_contract.ALWAYS_SEND_FLAGS)
    assert set(c.help_gated_flags) == set(cli_contract.HELP_GATED_FLAGS)
    assert c.implicit_context_disclosure == cli_contract.SKILLS_DISCOVERY_FACT_FULL
    assert c.usage_event_markers == cli_contract.USAGE_EVENT_MARKERS
    assert len(c.failure_signatures.auth) == len(cli_contract.AUTH_FAILURE_PATTERNS)
    assert len(c.failure_signatures.contract_drift) == len(
        cli_contract.CONTRACT_DRIFT_STDERR_PATTERNS
    )


def test_signature_regexes_match_what_the_predicates_match():
    """The escaped, case-insensitive regex forms classify the same evidence the
    legacy substring predicates do — the shared classifier must not weaken
    classification when the adapter migration lands."""
    import re

    samples = {
        "auth": "Error: Not logged in — please run `codex login`",
        "contract_drift": "error: unexpected argument '--sandbox' found",
        "rate_limited": "You've hit your usage limit. Rate limit reached.",
    }
    sigs = cli_contract.PONTIFEX_CONTRACT.failure_signatures
    assert cli_contract.is_auth_failure(samples["auth"])
    assert any(re.search(p, samples["auth"]) for p in sigs.auth)
    assert cli_contract.is_contract_drift(samples["contract_drift"])
    assert any(re.search(p, samples["contract_drift"]) for p in sigs.contract_drift)
    assert cli_contract.is_rate_limited(samples["rate_limited"])
    assert any(re.search(p, samples["rate_limited"]) for p in sigs.rate_limited)


def test_forbidden_phrases_are_still_justified():
    """Guard the guard: each ban exists because of a fact that can change. If
    delegate ever applies diffs or a bypass flag becomes part of the contract,
    retire the ban rather than leave a stale prohibition."""
    # Delegate returns a diff; nothing in the always-send set applies it.
    assert "--dangerously-bypass-approvals-and-sandbox" not in cli_contract.ALWAYS_SEND_FLAGS
    # The cross-bridge canaries stay banned as long as this repo is codex-only.
    assert cli_contract.PONTIFEX_CONTRACT.backend_id == "codex"
