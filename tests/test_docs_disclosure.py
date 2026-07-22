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
    """Guard the guard: a renamed or moved file must fail loudly, not silently pass.

    Without this, a `read_text` on a missing path would error — but a typo'd path that
    happened to exist elsewhere, or a site quietly dropped from the tuple, would not.
    """
    for relpath in _DOC_DISCLOSURE_SITES:
        assert (_REPO_ROOT / relpath).is_file(), relpath


def test_rule_names_the_doc_sites():
    """cli_contract.py's RULE stays the authoritative list this test mirrors.

    If the RULE stops naming a site this test checks, the two have drifted and one of
    them is wrong — fail rather than let the mirror rot (the #227 re-listing failure).
    """
    contract = (_REPO_ROOT / "src/codex_in_claude/cli_contract.py").read_text(encoding="utf-8")
    rule = contract.split("# RULE:", 1)[1].split("\n\n", 1)[0]
    for token in ("README.md", "COMPATIBILITY.md", "SECURITY.md", "collaborating-with-codex"):
        assert token in rule, f"cli_contract.py RULE no longer names {token}"
