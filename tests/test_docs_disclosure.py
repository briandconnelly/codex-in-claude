"""The doc half of cli_contract.py's egress-disclosure RULE is enforced too.

`cli_contract.py`'s "Implicit Codex context" RULE binds every egress-caveat prose site
— code *and* docs — to disclose both skills roots. Its code half already fails the gate
when it regresses (the guarantee matchers in `test_server.py`, plus the manifest
snapshot). Its doc half had no guard at all, so a disclosure change could land enforced
in code and silently incomplete in prose. #358 is the evidence: eight sites had to be
found and corrected by hand after #300 updated only some of them.
"""

from __future__ import annotations

import re
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


# --- The mechanism half of the disclosure (#498) ---------------------------------
#
# #480 established *how* each half of the implicit context arrives, and the two differ:
# `AGENTS.md` content is auto-LOADED — already in context when the turn begins, with no
# read issued for it — while a skill is auto-DISCOVERED by name and description, and its
# BODY arrives only through a read the model itself issues once it selects the skill.
# COMPATIBILITY.md and cli_contract.py were corrected to say so; the other disclosure
# sites still called the whole thing "auto-loading", so the repo contradicted itself
# about a security-relevant mechanism.
#
# Scoped to the markdown SECTION that names a skills root, not the whole file: every one
# of these files uses "select" somewhere unrelated (route selection, workspace selection),
# so a file-wide search would pass vacuously — see
# test_mechanism_matcher_rejects_the_pre_498_wording for the red-green proof.
_SELECTION_RE = re.compile(r"\bselect(s|ed|ion|ing)?\b", re.IGNORECASE)
_METADATA_RE = re.compile(r"\b(descriptions?|metadata)\b", re.IGNORECASE)
# The body is the security-load-bearing half of the mechanism: metadata alone is
# harmless, the body is the egress. Requiring it means a rewrite cannot drop the
# body claim and keep the guard green.
_BODY_RE = re.compile(r"\bbod(y|ies)\b", re.IGNORECASE)


def _sections_disclosing_a_skills_root(text: str) -> list[str]:
    """The markdown sections that name either skills root.

    Fence-aware: these files embed probe scripts whose `# comment` lines would
    otherwise read as headings and split a section in the middle, which could
    strand a disclosure from the sentence that explains it.
    """
    sections, current, in_fence = [], [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif line.startswith("#") and not in_fence:
            sections.append("\n".join(current))
            current = []
        current.append(line)
    sections.append("\n".join(current))
    return [
        section
        for section in sections
        if _PROJECT_SKILLS_ROOT in section or _GLOBAL_SKILLS_ROOT in section
    ]


def _states_the_mechanism(section: str) -> bool:
    """Does this section say metadata arrives up front and the body on selection?

    Word-presence, deliberately, and its limits are worth stating.

    It CATCHES omission — the pre-#498 wording, and any rewrite that drops the
    body claim — plus prose that denies the discovery outright.

    It does NOT catch a confident misstatement. A section claiming "descriptions
    and bodies both auto-load; the model then selects one", or one asserting the
    body is never read, satisfies every marker. Detecting that needs a reader:
    an attempt to reject negation lexically fired instead on the *strengthening*
    phrases these very docs use ("egress the caller never asked for", "a prompt
    that never referred to it"), so it was removed rather than tuned.
    `COMPATIBILITY.md` remains the authority on the mechanism itself.
    """
    return bool(
        _SELECTION_RE.search(section) and _METADATA_RE.search(section) and _BODY_RE.search(section)
    )


@pytest.mark.parametrize("relpath", _DOC_DISCLOSURE_SITES)
def test_doc_site_states_how_a_skill_body_arrives(relpath):
    text = (_REPO_ROOT / relpath).read_text(encoding="utf-8")
    disclosing = _sections_disclosing_a_skills_root(text)
    assert disclosing, f"{relpath} names no skills root at all"
    assert any(_states_the_mechanism(section) for section in disclosing), (
        f"{relpath} discloses a skills root without saying how a skill's body arrives: "
        "name and description are auto-discovered, the body follows a model-issued read "
        "once the skill is selected (see COMPATIBILITY.md). Calling it 'auto-loading' "
        "contradicts that."
    )


def test_mechanism_matcher_rejects_the_pre_498_wording():
    """Guard the guard: the matcher must fail on the wording #498 replaced.

    Without this, a matcher that accepted everything would keep the test above green
    forever. The first string is a verbatim pre-#498 disclosure; the second is the
    corrected shape.
    """
    pre_498 = (
        "## Data exposure\n"
        "- Codex auto-loads the workspace's `AGENTS.md` and `.agents/skills/` skills, and\n"
        "  discovers your user-global skills under `$CODEX_HOME/skills/` from outside the\n"
        "  workspace, even if the prompt never mentions them."
    )
    assert _states_the_mechanism(pre_498) is False

    corrected = (
        "## Data exposure\n"
        "- Codex auto-loads the workspace's `AGENTS.md` and auto-discovers skills in\n"
        "  `.agents/skills/` and `$CODEX_HOME/skills/`: a skill's name and description\n"
        "  reach the model up front, and its body follows once the model selects it."
    )
    assert _states_the_mechanism(corrected) is True

    # A correct sentence still contains both "auto-loads" and "skills" — the mechanism
    # it auto-loads is `AGENTS.md`. A co-occurrence blacklist would reject this, which
    # is why the predicate asks what the prose STATES rather than which words it uses.
    mixed = (
        "## Safety\n"
        "Codex auto-loads the resolved workspace's `AGENTS.md` and discovers skills in\n"
        "`.agents/skills/` and `$CODEX_HOME/skills/` by name and description; a selected\n"
        "skill's body is then read by the model itself."
    )
    assert _states_the_mechanism(mixed) is True


def test_mechanism_matcher_ignores_unrelated_skill_discovery_prose():
    """Guard the guard: passing must come from the disclosure, not from nearby text.

    `README.md` and `SKILL.md` both use "select" for route and workspace selection, and
    `README.md` separately describes Claude Code auto-discovering this plugin's own
    skill. None of that says anything about Codex egress, so none of it may satisfy the
    predicate — a section-scoped check is what keeps it from doing so.
    """
    unrelated = (
        "## Skills\n"
        "The plugin ships one Claude Code skill, auto-discovered from `skills/`.\n"
        "It selects ordinary consult, review, delegate, transfer, and async tools\n"
        "directly, and loads a reference only when one is needed."
    )
    assert _states_the_mechanism(unrelated) is False

    # A section naming a skills root but flatly denying the egress must not pass either.
    negated = (
        "## Data exposure\n"
        "Codex never discovers skills in `.agents/skills/` or `$CODEX_HOME/skills/`,\n"
        "so no skill name or description is exposed."
    )
    assert _states_the_mechanism(negated) is False


def test_section_split_ignores_headings_inside_code_fences():
    """A `#` comment in an embedded probe script must not split a section.

    `COMPATIBILITY.md` carries shell probes whose comment lines begin with `#`.
    Splitting on them would separate a disclosure from the sentence explaining
    the mechanism, failing a site that is in fact correct.
    """
    text = (
        "## Data exposure\n"
        "Codex auto-discovers skills in `.agents/skills/`.\n"
        "\n"
        "```sh\n"
        "# generate a marker skill\n"
        'mkdir -p "$CODEX_HOME/skills/marker"\n'
        "```\n"
        "\n"
        "Its name and description arrive up front; the body follows once the\n"
        "model selects it.\n"
    )
    sections = _sections_disclosing_a_skills_root(text)
    assert len(sections) == 1, "the fenced comment split the section"
    assert _states_the_mechanism(sections[0]) is True


def test_mechanism_matcher_requires_the_body_claim():
    """Guard the guard: the body is the egress, so dropping it must fail.

    Metadata reaching the model is comparatively harmless; the body is what
    carries private content to OpenAI. A rewrite that keeps the discovery
    sentence but drops the body must not stay green.
    """
    metadata_only = (
        "## Data exposure\n"
        "Codex auto-discovers skills in `.agents/skills/` and `$CODEX_HOME/skills/`.\n"
        "Their names and descriptions reach the model, which may then select one."
    )
    assert _states_the_mechanism(metadata_only) is False

    with_body = metadata_only + "\nSelecting one makes the model read its body."
    assert _states_the_mechanism(with_body) is True
