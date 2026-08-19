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


# --- The AGENTS.md scope on the doc sites (#472) ----------------------------------
#
# The code-side constant is guarded in tests/test_cli_contract.py; these files restate
# the fact in their own prose, so they need their own guard or the published docs can
# drift back to the pre-#472 workspace-only claim while the wire stays correct.
# Two sources are checked because each was separately understated:
#   - the ancestor walk inside the repository (a workspace narrowed to a subdirectory
#     still ships every ancestor `AGENTS.md` up to the repository root), and
#   - the user-global `$CODEX_HOME` guidance file, which loads on every call from any
#     workspace and which neither isolation flag nor `project_doc_max_bytes=0` suppresses.
_ANCESTOR_RE = re.compile(r"\bancestor\b", re.IGNORECASE)
_REPO_ROOT_RE = re.compile(r"\brepositor(y|ies)\b", re.IGNORECASE)
_GLOBAL_AGENTS_RE = re.compile(r"\$CODEX_HOME/AGENTS")


def _states_the_agents_md_scope(section: str) -> bool:
    return bool(
        _ANCESTOR_RE.search(section)
        and _REPO_ROOT_RE.search(section)
        and _GLOBAL_AGENTS_RE.search(section)
    )


@pytest.mark.parametrize("relpath", _DOC_DISCLOSURE_SITES)
def test_doc_site_states_every_agents_md_source(relpath):
    text = (_REPO_ROOT / relpath).read_text(encoding="utf-8")
    disclosing = _sections_disclosing_a_skills_root(text)
    assert disclosing, f"{relpath} names no skills root at all"
    assert any(_states_the_agents_md_scope(section) for section in disclosing), (
        f"{relpath} understates which AGENTS.md files reach OpenAI: the load covers the "
        "resolved workspace, every ancestor up to the repository root when the workspace "
        "is in a repository, and a user-global $CODEX_HOME/AGENTS.override.md or "
        "AGENTS.md (see COMPATIBILITY.md § 'Implicit Codex context')."
    )


def test_agents_md_scope_matcher_rejects_the_pre_472_wording():
    """Guard the guard: the wording #472 replaces must FAIL this matcher.

    The same acknowledged limit as every other word-presence guard in this module — it
    catches OMISSION, not a confident misstatement. The exact-constant pins in
    tests/test_cli_contract.py and tests/test_server.py cover the code carriers against a
    false claim; on the prose sites COMPATIBILITY.md's probe table remains the authority.
    """
    pre_472 = (
        "## Data exposure\n"
        "Codex auto-loads the resolved workspace's `AGENTS.md` and discovers skills in\n"
        "`.agents/skills/` and `$CODEX_HOME/skills/`, whose bodies the model reads once\n"
        "it selects one by description."
    )
    assert _states_the_agents_md_scope(pre_472) is False

    # Half-corrections fail too: the ancestor walk without the global guidance file…
    ancestors_only = (
        "## Data exposure\n"
        "Codex auto-loads `AGENTS.md` from the workspace and every ancestor up to the\n"
        "repository root, and discovers skills in `.agents/skills/` and `$CODEX_HOME/skills/`."
    )
    assert _states_the_agents_md_scope(ancestors_only) is False

    # …and the global file without the ancestor walk.
    global_only = (
        "## Data exposure\n"
        "Codex auto-loads the workspace's `AGENTS.md` plus `$CODEX_HOME/AGENTS.md`, and\n"
        "discovers skills in `.agents/skills/` and `$CODEX_HOME/skills/`."
    )
    assert _states_the_agents_md_scope(global_only) is False

    # The corrected shape passes.
    corrected = (
        "## Data exposure\n"
        "Codex auto-loads `AGENTS.md` from the resolved workspace, from every ancestor\n"
        "directory up to the repository root, and from a user-global\n"
        "`$CODEX_HOME/AGENTS.override.md` (else `$CODEX_HOME/AGENTS.md`); it discovers\n"
        "skills in `.agents/skills/` and `$CODEX_HOME/skills/`."
    )
    assert _states_the_agents_md_scope(corrected) is True


# --- The skill's BINDING rule, not just its background section (#472) --------------
#
# `test_doc_site_states_every_agents_md_source` above is satisfied by ANY one qualifying
# section, which is right for a file whose disclosure lives in one place — and wrong for
# the skill, where "Data exposure" is background and "Binding rules" is what an agent
# actually applies before deciding to spend. A Codex review of this change caught exactly
# that: the Data-exposure section had been widened while the Privacy rule still named only
# `$CODEX_HOME/skills/`, so an agent following the binding rules could have approved a call
# with a secret sitting in one of the two newly disclosed sources. A rule that is narrower
# than the disclosure it enforces is the defect; this guard is scoped to the rule text.
_SKILL_PATH = "skills/collaborating-with-codex/SKILL.md"


def _privacy_rule_text() -> str:
    """The Privacy binding rules from the skill's rules list, and only those.

    #509 split the single `- **Privacy:**` bullet into atomic ones (`Privacy — …`), so
    this collects the whole contiguous Privacy GROUP rather than one bullet. The #472
    enumeration it guards still has to appear somewhere in that group, which is the same
    guarantee — but scoping to the group, rather than searching the file, is what keeps it
    from being satisfied by prose in some other section (the #512 failure mode).
    """
    text = (_REPO_ROOT / _SKILL_PATH).read_text(encoding="utf-8")
    bullets = []
    start = text.index("- **Privacy")
    cursor = start
    while True:
        nxt = text.index("\n- **", cursor + 1)
        bullets.append(text[cursor:nxt])
        if not text[nxt + 1 :].startswith("- **Privacy"):
            break
        cursor = nxt + 1
    group = "".join(bullets)
    assert "**Privacy" in group
    return group


# Both filenames, asserted independently. `_GLOBAL_AGENTS_RE` matches either one, so a
# single search would accept a rule naming only `AGENTS.md` — and `AGENTS.override.md`
# is the file that actually wins when both exist, so dropping it drops the one an agent
# most needs to check. A Codex review caught this exact vacuity.
_GLOBAL_GUIDANCE_FILES = ("$CODEX_HOME/AGENTS.override.md", "$CODEX_HOME/AGENTS.md")


def _names_every_guidance_file(rule: str) -> bool:
    """The production check, as a predicate the guard-the-guard cases can run.

    Extracted so the half-correction fixtures below go through the SAME logic the real
    assertion uses. Fixtures that only assert things about their own hard-coded strings
    prove nothing about the guard — weakening `_GLOBAL_GUIDANCE_FILES` would leave them
    green. A Codex review caught exactly that.
    """
    return all(filename in rule for filename in _GLOBAL_GUIDANCE_FILES)


def test_privacy_rule_covers_every_agents_md_source():
    rule = _privacy_rule_text()
    for filename in _GLOBAL_GUIDANCE_FILES:
        assert filename in rule, (
            f"the Privacy rule omits {filename}, a user-global guidance file that reaches "
            "OpenAI on every call from any workspace"
        )
    assert _names_every_guidance_file(rule)
    assert _ANCESTOR_RE.search(rule) and _REPO_ROOT_RE.search(rule), (
        "the Privacy rule omits the ancestor AGENTS.md files loaded up to the repository root"
    )
    # The rule it already carried must not be dropped while widening it.
    assert "$CODEX_HOME/skills/" in rule


def test_privacy_rule_guard_rejects_the_pre_472_rule():
    """Guard the guard: the verbatim pre-#472 Privacy rule must fail every assertion above.

    This is the shape that shipped — correct about skills, silent about both guidance
    sources — so if it passes, the guard is vacuous.
    """
    pre_472 = (
        "- **Privacy:** Do not make an active call when the supplied prompt, the supplied "
        "context, any file Codex may inspect in the resolved workspace, or your user-global "
        "skills under `$CODEX_HOME/skills/` contain something you cannot disclose (see Data "
        "exposure). Changing the workspace does not exclude those skills."
    )
    assert _GLOBAL_AGENTS_RE.search(pre_472) is None
    assert _ANCESTOR_RE.search(pre_472) is None

    # Half-corrections must fail too, or the guard would accept a rule that names one
    # guidance file and silently drops the other.
    # Each half-correction is run through the SAME predicate the production assertion
    # uses, so weakening `_GLOBAL_GUIDANCE_FILES` fails here too.
    plain_only = (
        "- **Privacy:** Do not make an active call when your user-global "
        "`$CODEX_HOME/AGENTS.md` contains something you cannot disclose."
    )
    assert _names_every_guidance_file(plain_only) is False

    override_only = (
        "- **Privacy:** Do not make an active call when your user-global "
        "`$CODEX_HOME/AGENTS.override.md` contains something you cannot disclose."
    )
    assert _names_every_guidance_file(override_only) is False

    # …and the rule as it actually stands is accepted by that same predicate.
    assert _names_every_guidance_file(_privacy_rule_text()) is True


# --- #509: the read-scope correction on the prose sites ----------------------------
# The prose sites restate the fact in their own words, so exact containment (what
# tests/test_server.py pins on the code carriers) is the wrong instrument here. What
# actually catches a regression on this side is the LEGACY-CLAUSE rejection: an inversion
# would be phrased as the clause that was removed.
_LEGACY_SCOPED_READ_PROSE = (
    "may read other files in the resolved workspace.",
    "may read files in the workspace itself",
    "read tracked files in the throwaway worktree",
    "can inspect files anywhere in that resolved workspace,",
    "any file codex may inspect in the resolved workspace",
    "a directory you approve for disclosure",
)

# Affirmative markers, required TOGETHER: a site must say reads reach outside the
# workspace AND that the sandbox does not bound them. Requiring both is what stops a
# site from naming the concept while asserting the old model.
_OUTSIDE_READ_RE = re.compile(r"read[^.]{0,80}\boutside\b|\boutside\b[^.]{0,80}read", re.IGNORECASE)
_NOT_A_READ_BOUNDARY_RE = re.compile(
    r"bounds? writes,? not reads|not a read boundary|not confined to it"
    r"|not what it can read|not bounded by the workspace",
    re.IGNORECASE,
)


def _flat(text: str) -> str:
    """Collapse whitespace before matching.

    These are wrapped markdown sources, so a phrase the guard looks for is routinely split
    across a line break ("not a read\n  boundary") or interrupted by emphasis ("**not** a
    read boundary"). Without this the matchers below pass or fail on where the author
    happened to wrap or bold, which is not a property worth gating on — both variants were
    caught mid-review passing a site that plainly stated the fact.
    """
    return re.sub(r"\s+", " ", text.replace("*", "").replace("`", ""))


# The read-scope RULE names a DIFFERENT set of prose sites than the skills-roots RULE
# above: `docs/REFERENCE.md` carries a workspace-selection section and so must state that
# the workspace is not a read boundary, but it carries no skills-root disclosure. Reusing
# `_DOC_DISCLOSURE_SITES` would either under-enforce this rule or wrongly demand skills
# disclosures of REFERENCE.md, so the rule gets its own tuple and its own two-way
# consistency check below.
_READ_SCOPE_DOC_SITES = (*_DOC_DISCLOSURE_SITES, "docs/REFERENCE.md")


def test_read_scope_site_list_matches_its_own_rule():
    """The read-scope tuple and the `RULE (read scope):` block must name the same sites.

    Two-way, for the reason the skills-roots equivalent is: a one-way check lets a site be
    dropped from the tuple and fall out of enforcement while staying green. The header is
    matched exactly so this parses ITS rule — `cli_contract.py` now carries two RULE
    blocks, and splitting on the bare `# RULE:` prefix silently reads the other one.
    """
    contract = (_REPO_ROOT / "src/codex_in_claude/cli_contract.py").read_text(encoding="utf-8")
    assert contract.count("# RULE (read scope):") == 1
    rule = contract.split("# RULE (read scope):", 1)[1].split("\n\n", 1)[0]

    expected = {
        "README.md": "README.md",
        "SECURITY.md": "SECURITY.md",
        "COMPATIBILITY.md": "COMPATIBILITY.md",
        "docs/REFERENCE.md": "docs/REFERENCE.md",
        "collaborating-with-codex": "skills/collaborating-with-codex/SKILL.md",
    }
    named_in_rule = {token for token in expected if token in rule}
    assert named_in_rule == set(expected), (
        f"the read-scope RULE no longer names: {set(expected) - named_in_rule}"
    )
    enforced = set(_READ_SCOPE_DOC_SITES) - {
        "skills/collaborating-with-codex/references/server-down-fallback.md"
    }
    assert enforced == set(expected.values()), (
        "the read-scope RULE and _READ_SCOPE_DOC_SITES have drifted: "
        f"only in tuple={enforced - set(expected.values())}, "
        f"only in RULE={set(expected.values()) - enforced}"
    )


# Same shape as tests/test_server.py's runtime contradiction guard, and same reasoning: a
# prose site can state the fact correctly in one paragraph and re-assert the bound in
# another, which neither the legacy list nor the affirmative matchers can see. Kept as an
# independent copy rather than imported because the two guard different corpora (runtime
# strings vs markdown sources) and should be able to diverge without silently weakening
# one another.
_CONTRADICTS_READ_SCOPE_PROSE = re.compile(
    r"(?:confined|limited|restricted) to\b[^.]{0,40}"
    r"\b(?:workspace|worktree|repo|repos|repository|working dir)"
    r"|(?:cannot|can ?not|can't|never|does not|doesn't) read\b[^.]{0,40}\boutside"
    r"|reads? only\b[^.]{0,40}\b(?:workspace|worktree|repo|repository)",
    re.IGNORECASE,
)


@pytest.mark.parametrize("relpath", _READ_SCOPE_DOC_SITES)
def test_doc_site_does_not_scope_codex_reads_to_the_workspace(relpath):
    """No prose site may reuse a clause that presents the workspace as a read bound (#509)."""
    text = _flat((_REPO_ROOT / relpath).read_text(encoding="utf-8").lower())
    for clause in _LEGACY_SCOPED_READ_PROSE:
        assert clause not in text, (
            f"{relpath} still scopes Codex's reads with {clause!r} — read-only bounds "
            "writes, not reads (#509; COMPATIBILITY.md owns the probe)"
        )
    contradiction = _CONTRADICTS_READ_SCOPE_PROSE.search(text)
    assert contradiction is None, (
        f"{relpath} asserts a read bound ({contradiction.group(0)!r} ) alongside the "
        "corrected disclosure (#509)"
    )


def test_prose_contradiction_pattern_is_not_vacuous():
    """Guard the guard: it must fire on an inverted claim and spare the corrected wording."""
    assert _CONTRADICTS_READ_SCOPE_PROSE.search(
        "Nevertheless, Codex is confined to files under the workspace."
    )
    assert _CONTRADICTS_READ_SCOPE_PROSE.search("Codex reads only files in the repo.")
    assert (
        _CONTRADICTS_READ_SCOPE_PROSE.search(
            "Neither read-only nor workspace-write confines what Codex may read."
        )
        is None
    )


@pytest.mark.parametrize("relpath", _READ_SCOPE_DOC_SITES)
def test_doc_site_states_reads_are_not_bounded_by_the_workspace(relpath):
    text = _flat((_REPO_ROOT / relpath).read_text(encoding="utf-8"))
    assert _OUTSIDE_READ_RE.search(text), f"{relpath} never says Codex reads outside the workspace"
    assert _NOT_A_READ_BOUNDARY_RE.search(text), (
        f"{relpath} never says the workspace is not a read boundary"
    )


def test_read_scope_prose_guard_rejects_the_pre_509_wording():
    """Guard the guard: the wording #509 replaced must fail, and the correction must pass.

    Without this a matcher that accepted everything would hold the two tests above green
    forever — the vacuity this file's other guard-the-guard cases exist to prevent.
    """
    pre_509 = (
        "During every active call — including consult — Codex may read other files in the "
        "resolved workspace."
    )
    assert _NOT_A_READ_BOUNDARY_RE.search(pre_509) is None
    corrected = (
        "Codex may read files outside the resolved workspace, up to everything the OS user "
        "running codex can read: the sandbox bounds writes, not reads."
    )
    assert _OUTSIDE_READ_RE.search(corrected) and _NOT_A_READ_BOUNDARY_RE.search(corrected)
    # The INVERSION must not satisfy the affirmative matcher.
    inverted = "The sandbox bounds reads, not writes, so the workspace is a read boundary."
    assert _NOT_A_READ_BOUNDARY_RE.search(inverted) is None


def test_skill_privacy_and_independence_rules_carry_the_correction():
    """The BINDING rules, not just the background section (#512's failure mode, again).

    #509's premise is that readers treat the workspace enumeration as the decision
    boundary. The Privacy rules are where that decision is actually made, and the
    Independence rules keyed their whole argument on the same geometry — so widening the
    Data-exposure bullet while leaving either narrow would reproduce the exact defect.
    """
    rules = _flat(_privacy_rule_text())
    assert _NOT_A_READ_BOUNDARY_RE.search(rules) or "not the boundary" in rules.lower(), (
        "the Privacy binding rules still present the workspace as the decision boundary"
    )
    assert "necessary, not sufficient" in rules, (
        "the Privacy enumeration must say it is necessary but NOT sufficient — it lists "
        "what an ordinary call reaches, not the limit of what can be reached"
    )

    text = (_REPO_ROOT / _SKILL_PATH).read_text(encoding="utf-8")
    start = text.index("- **Independence — draft placement:**")
    independence = _flat(text[start : text.index("\n- **Git state", start)])
    assert "not a boundary" in independence, (
        "the Independence rules still treat draft placement as a boundary; under the "
        "corrected model Codex's reads are not bounded by the workspace"
    )
