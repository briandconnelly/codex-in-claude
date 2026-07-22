"""The doc half of cli_contract.py's egress-disclosure RULE is enforced too.

`cli_contract.py`'s "Implicit Codex context" RULE binds every egress-caveat prose site
— code *and* docs — to disclose both skills roots. Its code half already fails the gate
when it regresses (the guarantee matchers in `test_server.py`, plus the manifest
snapshot). Its doc half had no guard at all, so a disclosure change could land enforced
in code and silently incomplete in prose. #358 is the evidence: eight sites had to be
found and corrected by hand after #300 updated only some of them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

# The doc-side sites named in cli_contract.py's RULE. The code-side sites (server
# instructions, tool descriptions/docstrings, capability `returns`, negative_scope) are
# covered by the guarantee matrix in test_server.py.
_DOC_DISCLOSURE_SITES = (
    "README.md",
    "SECURITY.md",
    "COMPATIBILITY.md",
    "skills/collaborating-with-codex/SKILL.md",
    "skills/collaborating-with-codex/references/server-down-fallback.md",
)

# Both roots must be named. `.agents/skills/` alone is exactly the pre-#358 defect.
_PROJECT_SKILLS_ROOT = ".agents/skills"
_GLOBAL_SKILLS_ROOT = "$CODEX_HOME/skills"


@pytest.mark.parametrize("relpath", _DOC_DISCLOSURE_SITES)
def test_doc_site_discloses_both_skills_roots(relpath):
    text = (_REPO_ROOT / relpath).read_text(encoding="utf-8")
    assert _PROJECT_SKILLS_ROOT in text, f"{relpath} dropped the project skills disclosure"
    assert _GLOBAL_SKILLS_ROOT in text, f"{relpath} dropped the user-global skills disclosure"


def test_disclosure_sites_exist():
    """Guard the guard: a renamed or moved file must fail loudly, not silently pass."""
    for relpath in _DOC_DISCLOSURE_SITES:
        assert (_REPO_ROOT / relpath).is_file(), relpath


def test_site_list_matches_the_authoritative_rule():
    """The tuple above and cli_contract.py's RULE must name the same doc sites.

    Checked in BOTH directions on purpose. A one-way check ("every token in the RULE is
    somewhere in the repo") lets a site be quietly deleted from `_DOC_DISCLOSURE_SITES`:
    the parametrized test would simply run one fewer case and stay green while that file
    fell out of enforcement entirely. Equality makes dropping a site a failure, and makes
    adding one to the RULE fail until it is enforced here too.
    """
    contract = (_REPO_ROOT / "src/codex_in_claude/cli_contract.py").read_text(encoding="utf-8")
    rule = contract.split("# RULE:", 1)[1].split("\n\n", 1)[0]

    # How each RULE mention maps to the file that must carry the disclosure.
    expected = {
        "README.md": "README.md",
        "COMPATIBILITY.md": "COMPATIBILITY.md",
        "SECURITY.md": "SECURITY.md",
        "collaborating-with-codex": "skills/collaborating-with-codex/SKILL.md",
    }
    named_in_rule = {token for token in expected if token in rule}
    assert named_in_rule == set(expected), (
        f"cli_contract.py RULE no longer names: {set(expected) - named_in_rule}"
    )
    # Every RULE-named site is enforced, and nothing enforced here is un-named. The
    # fallback reference is enforced as part of the skill the RULE names.
    enforced = set(_DOC_DISCLOSURE_SITES) - {
        "skills/collaborating-with-codex/references/server-down-fallback.md"
    }
    assert enforced == set(expected.values()), (
        "the RULE and _DOC_DISCLOSURE_SITES have drifted: "
        f"only in tuple={enforced - set(expected.values())}, "
        f"only in RULE={set(expected.values()) - enforced}"
    )
