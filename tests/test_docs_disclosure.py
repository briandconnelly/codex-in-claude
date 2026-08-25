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

    #509 split the single `- **Privacy:**` bullet into atomic ones (`Privacy — ...`), so this
    returns the whole contiguous Privacy GROUP rather than one bullet. The #472 enumeration it
    guards still has to appear somewhere in that group, which is the same guarantee — but
    scoping to the group, rather than searching the file, is what keeps it from being satisfied
    by prose in some other section (the #512 failure mode).

    Returned as ONE contiguous slice of the source. An earlier version accumulated per-bullet
    slices that each stopped before their trailing newline and joined them, which fabricated
    junctions ("...on a call is safe.- **Privacy — do not call:**") appearing nowhere in the
    file. No assertion depended on that text, but a guard whose corpus is partly invented can
    match across a seam that does not exist — the defect this whole change is about. Caught by
    a Copilot review.
    """
    text = (_REPO_ROOT / _SKILL_PATH).read_text(encoding="utf-8")
    start = text.index("- **Privacy")
    end = start
    while True:
        nxt = text.index("\n- **", end + 1)
        if not text[nxt + 1 :].startswith("- **Privacy"):
            end = nxt
            break
        end = nxt
    group = text[start:end]
    assert group.count("- **Privacy") >= 1
    assert "\n- **Privacy" in group or group.count("- **Privacy") == 1, (
        "the group must be contiguous source text, newlines included"
    )
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


def test_privacy_rule_text_is_verbatim_source():
    """The helper must return real source text, not a reassembly of it.

    It previously joined per-bullet slices that each stopped short of their trailing
    newline, producing junctions ("...on a call is safe.- **Privacy — do not call:**") that
    appear nowhere in the file. Nothing asserted on those seams, so every test stayed green —
    but a guard matching a corpus it partly invented can fire, or fail to fire, across a seam
    the source does not contain. This assertion fails against that implementation.
    """
    source = (_REPO_ROOT / _SKILL_PATH).read_text(encoding="utf-8")
    group = _privacy_rule_text()
    assert group in source, "the Privacy group is not a contiguous slice of SKILL.md"
    assert group.count("- **Privacy") > 1, (
        "precondition: more than one Privacy bullet, or this cannot detect the defect"
    )


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
    assert "not a read boundary" in independence, (
        "the Independence rules still treat draft placement as a boundary; under the "
        "corrected model Codex's reads are not bounded by the workspace"
    )
    # The reclassification trigger must be something the agent can actually observe. The
    # result contract carries no read audit (schemas.py's success envelopes expose final
    # text, session, and model — nothing per-file), so a trigger keyed on whether Codex
    # "reached" the draft is unfalsifiable and the independence claim rests on nothing.
    assert "otherwise reached it" not in independence, (
        "reclassification is keyed on an unobservable event; key it on the agent's own "
        "tool calls and the returned output instead"
    )
    for observable in ("supplied to Codex or named to it", "returned output contains"):
        assert observable in independence, (
            f"the Independence rules dropped the observable trigger {observable!r}"
        )


# --- #513 preview-scope prose guards ----------------------------------------------
# The doc half of `cli_contract.py`'s `RULE (preview scope):`. A dry run cannot establish
# what a paid call will send, and every prose site that steers a reader toward a preview
# before spending has to say so.
#
# SECTION-scoped, not file-wide, and for a sharper reason than the skills-root guards
# above: README states the read-scope fact ~50 lines BELOW its dry-run bullets, and
# `active-workflows.md` states it in the Consult section before its dry-run prose. A
# file-wide positive check is therefore green today, on files whose dry-run passages still
# make the uncorrected claim — a guard that cannot fail.
#
# SECURITY.md and COMPATIBILITY.md are deliberately absent, unlike _READ_SCOPE_DOC_SITES:
# neither file contains "dry" or "preview" at all, so neither makes the claim being
# corrected, and a section-scoped guard would have no section to anchor to.
_PREVIEW_SCOPE_DOC_SITES = (
    "README.md",
    "docs/REFERENCE.md",
    "skills/collaborating-with-codex/SKILL.md",
    "skills/collaborating-with-codex/references/active-workflows.md",
    "commands/codex/dry-run.md",
    "commands/codex/review.md",
)

# The anchor is derived from the claim being guarded — a passage that steers the reader to
# run a dry run — rather than from every mention of the tools. `docs/REFERENCE.md` names
# them in four unrelated sections (the free-tool list, `coverage.redaction`, `roots_source`,
# `deadline_advisory`); demanding the disclosure in all four over-demands, and "any section"
# reintroduces the vacuity this scoping exists to avoid.
_DRY_RUN_STEER_RE = re.compile(
    r"\b(?:codex_dry_run|codex_delegate_dry_run|dry[- ]run|dry run)\b", re.IGNORECASE
)
_SPEND_STEER_RE = re.compile(
    r"\b(?:before|first|preview|previews|previewing)\b[^.]{0,80}"
    r"\b(?:spend|spending|paid|cost|free)\b"
    r"|\bpreview\b[^.]{0,40}\b(?:a |the )?(?:review|delegate)\b",
    re.IGNORECASE,
)


def _preview_steer_sections(text: str) -> list[str]:
    """Markdown sections that steer a reader to run a dry run before spending.

    Fence-aware for the same reason as `_sections_disclosing_a_skills_root`: a `#` inside a
    fenced block is not a heading, and splitting there could strand a disclosure from the
    passage it qualifies. Frontmatter counts as the first section, which is what lets the
    slash-command files be guarded on their standalone `description:` line.
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
    return [s for s in sections if _DRY_RUN_STEER_RE.search(s) and _SPEND_STEER_RE.search(s)]


# The prose sites paraphrase rather than carrying the 198-byte constant verbatim — a
# slash-command `description:` line is a one-line label, and demanding the whole constant
# there is wrong-sized. So this is a word-presence check, with the acknowledged limit every
# such matcher in this repo carries: it catches omission, not a confident misstatement. The
# contradiction sweep below is the half that covers the misstatement.
# The negation must PRECEDE the limit verb, and the verb list must not reach "boundary".
# Both constraints were found by a false pass, not written defensively: `bound\w*` matched
# "read boundary" and the loose ordering matched "The sandbox bounds writes, not reads" —
# so `docs/REFERENCE.md` satisfied this guard using the READ-scope sentence, while its
# dry-run passage still made the uncorrected claim. A guard that green-lights a site via a
# different guarantee is the #512 shape at matcher level.
_PREVIEW_LIMIT_RE = re.compile(
    r"\b(?:does not|doesn't|do not|don't|cannot|can ?not|never|neither|not)\b[^.]{0,90}"
    r"\b(?:enumerates?|bounds?|proves?|establishe?s?|guarantees?|inventor(?:y|ies))\b",
    re.IGNORECASE,
)
_MODEL_READS_RE = re.compile(
    r"\b(?:codex|the model)\b[^.]{0,60}\breads?\b|\bfiles\b[^.]{0,40}\breads?\b",
    re.IGNORECASE,
)


def _flat_text(relpath: str) -> str:
    return (_REPO_ROOT / relpath).read_text(encoding="utf-8")


def test_preview_scope_doc_sites_exist():
    """Guard the guard: a renamed or moved file must fail loudly, not silently pass."""
    for relpath in _PREVIEW_SCOPE_DOC_SITES:
        assert (_REPO_ROOT / relpath).is_file(), relpath


def test_preview_site_list_matches_its_own_rule():
    """The tuple above and the `RULE (preview scope):` block must name the same sites.

    Two-way, like its read-scope sibling: a one-way check lets a site drop out of the tuple
    and out of enforcement while staying green. The header is matched exactly because
    `cli_contract.py` now carries THREE RULE blocks and a bare `# RULE:` split reads the
    first one.
    """
    contract = (_REPO_ROOT / "src/codex_in_claude/cli_contract.py").read_text(encoding="utf-8")
    assert contract.count("# RULE (preview scope):") == 1
    rule = contract.split("# RULE (preview scope):", 1)[1].split("\n\n", 1)[0]

    expected = {
        "README.md": "README.md",
        "docs/REFERENCE.md": "docs/REFERENCE.md",
        "collaborating-with-codex": "skills/collaborating-with-codex/SKILL.md",
        "references/active-workflows.md": (
            "skills/collaborating-with-codex/references/active-workflows.md"
        ),
        "commands/codex/dry-run.md": "commands/codex/dry-run.md",
        "commands/codex/review.md": "commands/codex/review.md",
    }
    named_in_rule = {token for token in expected if token in rule}
    assert named_in_rule == set(expected), (
        f"the preview-scope RULE no longer names: {set(expected) - named_in_rule}"
    )
    assert set(_PREVIEW_SCOPE_DOC_SITES) == set(expected.values()), (
        "the preview-scope RULE and _PREVIEW_SCOPE_DOC_SITES have drifted: "
        f"only in tuple={set(_PREVIEW_SCOPE_DOC_SITES) - set(expected.values())}, "
        f"only in RULE={set(expected.values()) - set(_PREVIEW_SCOPE_DOC_SITES)}"
    )


@pytest.mark.parametrize("relpath", _PREVIEW_SCOPE_DOC_SITES)
def test_every_dry_run_steer_states_the_preview_limit(relpath):
    """A site that steers a reader to a dry run must say somewhere what it cannot establish.

    At least one steer section, not every one. `SKILL.md`'s numbered Shared-workflow step is a
    RULE, and a `separating-context-from-constraints` audit of this very change flagged the
    load-bearing fact an every-section version had pushed into it — that skill requires rule
    sections to stay free of facts, so the stricter guard mandated the defect. The gap this
    leaves (one section states it, another still misleads) is covered by the file-wide
    contradiction sweep below and, for the skill, by the binding-rule pin at the end of this
    module.
    """
    sections = _preview_steer_sections(_flat_text(relpath))
    assert sections, (
        f"{relpath} no longer contains a dry-run steer — if the steer moved, move this "
        "site out of _PREVIEW_SCOPE_DOC_SITES and out of the RULE (#513)"
    )
    stating = [
        sec
        for sec in (_flat(section) for section in sections)
        if _PREVIEW_LIMIT_RE.search(sec) and _MODEL_READS_RE.search(sec)
    ]
    assert stating, (
        f"{relpath}: no dry-run steer states the preview-scope limit (#513); steers found "
        f"in {len(sections)} section(s), e.g. {_flat(sections[0])[:160]!r}"
    )


def test_preview_steer_matcher_rejects_the_pre_513_wording():
    """Guard the guard, red-green: the wording this issue corrects must FAIL.

    Without this, the matcher could be satisfied by any nearby hedge and the whole
    parametrized sweep above would be a check that cannot fail.
    """
    pre_513 = _flat(
        "- `codex_dry_run(scope, …)` — preview a review's scope/diff size/redactions "
        "before spending."
    )
    assert _DRY_RUN_STEER_RE.search(pre_513) and _SPEND_STEER_RE.search(pre_513), (
        "precondition: the pre-fix bullet must still register as a steer"
    )
    assert not (_PREVIEW_LIMIT_RE.search(pre_513) and _MODEL_READS_RE.search(pre_513)), (
        "the pre-#513 wording must fail the limit check"
    )
    # A redaction-only qualifier is not the read-side one: this is the exact text
    # active-workflows.md carried, and it must not satisfy the guard on its own.
    redaction_only = _flat(
        "Run codex_dry_run first when scope or redaction is uncertain before spending. "
        "A dry-run previews input; it does not prove that redaction catches every secret."
    )
    assert not (
        _PREVIEW_LIMIT_RE.search(redaction_only) and _MODEL_READS_RE.search(redaction_only)
    ), "a redaction-only caveat must not satisfy the read-side guard"
    # …while a corrected passage passes.
    fixed = _flat(
        "Run codex_dry_run first to preview scope before spending. It does not enumerate "
        "the files Codex itself reads during the paid run."
    )
    assert _PREVIEW_LIMIT_RE.search(fixed) and _MODEL_READS_RE.search(fixed)


@pytest.mark.parametrize("relpath", _PREVIEW_SCOPE_DOC_SITES)
def test_no_doc_site_presents_a_preview_as_an_egress_bound(relpath):
    """File-wide contradiction sweep — the misstatement half the word check cannot see."""
    hit = _preview_egress_bound_claim(_flat(_flat_text(relpath)))
    assert hit is None, (
        f"{relpath} presents a preview as evidence about total egress (#513): {hit!r}"
    )


# Same shape and same reasoning as `_CONTRADICTS_READ_SCOPE_PROSE` above, and kept as an
# independent copy of test_server.py's runtime pattern for the reason stated there: the two
# guard different corpora and should be able to diverge without weakening one another.
_CONTRADICTS_PREVIEW_SCOPE_PROSE = re.compile(
    r"\b(?:everything|all)\b[^.]{0,30}\b(?:paid call|model|codex run)\b"
    r"[^.]{0,40}\b(?:sen[dt]s?|transmit(?:s|ted)?|receiv(?:e|es|ed)|leaves?)\b"
    r"|\b(?:everything|nothing (?:else|more))\b[^.]{0,60}"
    r"\b(?:previewed|shown here|listed here|reported here)\b"
    # "inventory" is NOT in this alternative: CAPABILITY_SUMMARY legitimately says "use
    # codex_capabilities for the full inventory", which the looser form flagged.
    r"|\b(?:complete|full|exhaustive)\s+(?:security\s+)?(?:preflight|audit)\b"
    r"|\b(?:complete|full|exhaustive)\s+inventory\s+of\b[^.]{0,40}"
    r"\b(?:read|reads|files|egress|sent)\b"
    r"|\bsafe to (?:proceed|spend)\b"
    r"|\bno (?:further|additional) disclosure review\b"
    # …or a certification that the preview clears the run. The completeness alternatives
    # above are keyed on a quantifier ("everything…transmits"); a Codex review showed this
    # shape needs none — "A clean preview certifies that no sensitive content will reach
    # OpenAI" passed every guard. It is the operative claim #513 exists to stop.
    r"|\b(?:certif(?:y|ies|ied)|confirms?|confirmed|assures?|proves?|guarantees?|"
    r"establishes?|verifies|verified)\b[^.]{0,60}"
    r"\bno\b[^.]{0,30}\b(?:sensitive|secrets?|confidential|disclosure|egress|"
    r"private data)\b"
    r"|\b(?:nothing sensitive|no sensitive \w+|no secrets?)\b[^.]{0,40}"
    r"\b(?:will be sent|is sent|reaches?|reach|leaves?|will leave)\b",
    re.IGNORECASE,
)


# A denial of the claim is not the claim. "…not evidence that nothing sensitive will be
# sent" contains the poison phrase verbatim, and the corrected carriers are FULL of such
# denials — this repo has now been bitten three times by a matcher that reads a
# strengthening sentence as the weakness it forbids (#502, the read-scope emphasis case,
# and this one). So the regex finds candidate claims and this wrapper drops the ones a
# negation governs, rather than the regex being weakened until it stops catching the
# affirmative form.
_DENIAL_PREFIX = re.compile(
    r"\b(?:not|never|n't|no|rather than|instead of|neither|nor)\b(?:\s+\S+){0,12}\s*$",
    re.IGNORECASE,
)


def _preview_egress_bound_claim(text: str) -> str | None:
    """The first affirmative claim that a preview bounds or clears the paid call's egress."""
    for m in _CONTRADICTS_PREVIEW_SCOPE_PROSE.finditer(text):
        # Sentence-scoped: a negation in a PREVIOUS sentence does not govern this claim,
        # and "Do not present a clean preview as evidence that nothing sensitive will be
        # sent" puts its negation seven words back — a fixed word window is the wrong
        # instrument, the sentence is.
        sentence_prefix = re.split(r"(?<=[.;:])\s+", text[: m.start()])[-1]
        if not _DENIAL_PREFIX.search(sentence_prefix):
            return m.group(0)
    return None


def test_preview_prose_contradiction_pattern_is_not_vacuous():
    """Guard the guard: it must fire on the constructed bypasses and spare the fix."""
    for poison in (
        "After a clean dry run, everything the model receives has been previewed here.",
        "prompt_bytes is the full size of everything the paid call transmits to OpenAI.",
        "The preview is a complete security preflight.",
        "A clean preview means it is safe to proceed.",
        "A clean preview certifies that no sensitive content will reach OpenAI.",
        "The preview confirms no secrets are sent.",
    ):
        assert _preview_egress_bound_claim(_flat(poison)), poison
    for ok in (
        "Codex can read files outside the workspace — up to everything the OS user "
        "running it can read — and send them to OpenAI.",
        "preview a review's scope/diff size/redactions before spending",
        "the full UTF-8 size of the prompt that would be sent",
        "Use codex_capabilities for the full inventory.",
    ):
        assert _preview_egress_bound_claim(_flat(ok)) is None, ok


def test_skill_carries_a_binding_rule_not_only_background():
    """The skill must gate the DECISION on this, not merely describe it (#512, #513).

    A prose site can state the fact in its background section while its binding rules stay
    narrow — the agent then follows every rule it is given and still treats a clean preview as
    clearance. That is the #512 defect, and it is why this pins the rule text itself rather
    than trusting the section sweep above.
    """
    text = _flat_text("skills/collaborating-with-codex/SKILL.md")
    parts = text.split("## Binding rules", 1)
    assert len(parts) == 2, "the Binding rules section is gone or renamed"
    flat_rules = _flat(parts[1])
    assert "dry run is not a disclosure check" in flat_rules, (
        "the preview-scope binding rule is missing from the Binding rules section"
    )
    assert _PREVIEW_LIMIT_RE.search(flat_rules), (
        "the binding rule states no limit — background disclosure is not a decision rule"
    )
    exposure = _flat(text.split("## Data exposure", 1)[1].split("## Binding rules", 1)[0])
    assert _PREVIEW_LIMIT_RE.search(exposure) and _MODEL_READS_RE.search(exposure), (
        "Data exposure lost the preview-scope fact the binding rule cross-references"
    )


# --- #523 write-scope disclosure ----------------------------------------------------
# The propose-tier prose promised "writes only inside a throwaway worktree"; codex's
# workspace-write sandbox grants the OS temp roots (/tmp and $TMPDIR) by default, so
# every prose site describing that write boundary must state the grant and none may
# re-assert the worktree as the bound. Same architecture as the #509 read-scope block
# above: a site tuple two-way-checked against the RULE block in cli_contract.py, legacy
# clauses, affirmative markers required together, a contradiction matcher, and
# guard-the-guard controls.

_WRITE_SCOPE_DOC_SITES = (
    "README.md",
    "SECURITY.md",
    "COMPATIBILITY.md",
    "skills/collaborating-with-codex/references/independent-attempt.md",
)

_LEGACY_EXCLUSIVE_WRITE_PROSE = (
    "but only inside a throwaway git worktree",
    "writes only inside a throwaway worktree",
    "lets codex write, but only inside",
    "which likewise bounds what it may write",
    "writes are isolated.",
)

# Affirmative markers, required TOGETHER (the #509 pattern): a site must name the OS
# temp roots as writable AND deny that the worktree bounds the writes. Requiring both
# stops a site from naming /tmp while still asserting the old model.
_TEMP_ROOT_GRANT_RE = re.compile(r"(?:os\s+temp\s+roots?|/tmp\b[^.]{0,60}\$?tmpdir)", re.IGNORECASE)
_NOT_A_WRITE_BOUNDARY_RE = re.compile(
    r"(?:worktree|workspace)\s+(?:does\s+not|doesn'?t)\s+bound"
    r"|not\s+the\s+(?:sandbox'?s?\s+)?whole\s+write\s+grant"
    r"|also\s+(?:lets\s+commands\s+write|grants?\s+(?:writes?\s+to\s+)?the\s+os\s+temp)"
    r"|default\s+already\s+grants\s+writes\s+to\s+the\s+os\s+temp\s+roots"
    r"|bounds\s+its\s+writes\s+\(the\s+worktree\s+plus",
    re.IGNORECASE,
)

# Independent copy of the runtime matcher in tests/test_server.py, for the same reason
# the read-scope block keeps one: the two guard different corpora. Unlike the runtime
# copy, the "writes stay/are isolated" arm also accepts `workspace` as the named bound:
# COMPATIBILITY.md restated the guarantee as "writes stay inside the workspace" — the
# workspace-worded synonym of the worktree claim — and a worktree-only matcher passed it
# (a Codex review caught the survivor).
_CONTRADICTS_WRITE_SCOPE_PROSE = re.compile(
    r"writes?\s+only\s+inside|only\s+inside\s+a\s+throwaway"
    r"|worktree,?\s+which\s+bounds|worktree\s+bounds\s+what\b[^.]{0,40}\bwrite"
    r"|(?:likewise|worktree)\s+bounds\s+what\s+it\s+may\s+write"
    r"|writes?\s+(?:stay|stays|are\s+isolated)\b[^.]{0,50}\b(?:worktree|workspace)"
    r"|writes?\b[^.]{0,30}\b(?:confined|limited|restricted)\s+to\b[^.]{0,40}\bworktree",
    re.IGNORECASE,
)


def test_write_scope_site_list_matches_its_own_rule():
    """The write-scope tuple and the `# RULE (write scope):` block must name the same
    sites — two-way, so a site dropped from either side fails instead of silently
    falling out of enforcement (the #509 pattern)."""
    contract = (_REPO_ROOT / "src/codex_in_claude/cli_contract.py").read_text(encoding="utf-8")
    assert contract.count("# RULE (write scope):") == 1
    rule = contract.split("# RULE (write scope):", 1)[1].split("\n\n", 1)[0]

    expected = {
        "README.md": "README.md",
        "SECURITY.md": "SECURITY.md",
        "COMPATIBILITY.md": "COMPATIBILITY.md",
        "independent-attempt.md": (
            "skills/collaborating-with-codex/references/independent-attempt.md"
        ),
    }
    named_in_rule = {token for token in expected if token in rule}
    assert named_in_rule == set(expected), (
        f"the write-scope RULE no longer names: {set(expected) - named_in_rule}"
    )
    assert set(_WRITE_SCOPE_DOC_SITES) == set(expected.values()), (
        "the write-scope RULE and _WRITE_SCOPE_DOC_SITES have drifted"
    )


@pytest.mark.parametrize("relpath", _WRITE_SCOPE_DOC_SITES)
def test_doc_site_does_not_scope_codex_writes_to_the_worktree(relpath):
    """No prose site may present the worktree as the sandbox's whole write grant (#523)."""
    text = _flat((_REPO_ROOT / relpath).read_text(encoding="utf-8").lower())
    for clause in _LEGACY_EXCLUSIVE_WRITE_PROSE:
        assert clause not in text, (
            f"{relpath} still scopes the propose tier's writes with {clause!r} (#523)"
        )
    contradiction = _CONTRADICTS_WRITE_SCOPE_PROSE.search(text)
    assert contradiction is None, (
        f"{relpath} asserts a worktree write bound ({contradiction.group(0)!r}) alongside "
        "the corrected disclosure (#523)"
    )


@pytest.mark.parametrize("relpath", _WRITE_SCOPE_DOC_SITES)
def test_doc_site_states_the_temp_root_grant(relpath):
    text = _flat((_REPO_ROOT / relpath).read_text(encoding="utf-8"))
    assert _TEMP_ROOT_GRANT_RE.search(text), f"{relpath} never names the OS temp roots as writable"
    assert _NOT_A_WRITE_BOUNDARY_RE.search(text), (
        f"{relpath} never denies that the worktree bounds the writes"
    )


def test_write_scope_prose_guard_rejects_the_pre_523_wording():
    """Guard the guard: the wording #523 replaced must fail, the correction must pass,
    and the inversion must not satisfy the affirmative matcher."""
    for pre_523 in (
        "`propose` (the `delegate` tools) lets Codex write, but only inside a throwaway "
        "git worktree seeded from `HEAD`.",
        "**Writes are isolated.** `codex_delegate` runs Codex with `workspace-write` but "
        "only inside a throwaway git worktree.",
        "Delegate works from the seeded worktree baseline, which likewise bounds what it "
        "may write rather than what it may read.",
    ):
        low = _flat(pre_523.lower())
        assert _CONTRADICTS_WRITE_SCOPE_PROSE.search(low) or any(
            clause in low for clause in _LEGACY_EXCLUSIVE_WRITE_PROSE
        ), pre_523
        assert _NOT_A_WRITE_BOUNDARY_RE.search(low) is None, pre_523

    corrected = _flat(
        "The worktree does not bound Codex's writes: codex's workspace-write sandbox "
        "also lets commands write the OS temp roots (`/tmp` and `$TMPDIR`) by default."
    )
    assert _TEMP_ROOT_GRANT_RE.search(corrected)
    assert _NOT_A_WRITE_BOUNDARY_RE.search(corrected)
    assert _CONTRADICTS_WRITE_SCOPE_PROSE.search(corrected.lower()) is None

    # Naming /tmp while asserting the old model must still fail the denial half.
    inverted = _flat(
        "Codex writes stay inside the worktree; /tmp and $TMPDIR are excluded, so the "
        "worktree bounds what it may write."
    )
    assert _NOT_A_WRITE_BOUNDARY_RE.search(inverted) is None
    assert _CONTRADICTS_WRITE_SCOPE_PROSE.search(inverted.lower())

    # The workspace-worded synonym must trip the matcher too: COMPATIBILITY.md restated
    # the guarantee as "writes stay inside the workspace", and a worktree-only matcher
    # passed it (a Codex review caught the survivor).
    workspace_worded = _flat("the sentence above — writes stay inside the workspace —")
    assert _CONTRADICTS_WRITE_SCOPE_PROSE.search(workspace_worded.lower())


# --- 2026-08-25 review, finding 2: the spend step must describe the LIVE read only ------
# `rate_limit.live_read()` calls `interpret(captured_at=now)` with no `cache_home`, so on the
# path codex_status actually takes `is_stale` and `home_unverified` are always False and no
# persisted snapshot exists (#321). Teaching an agent to branch on them describes a source
# that cannot occur and lengthens the one step S13 shows agents quote verbatim.
# Bare identifiers, not backticked: the shipped wording was "`home_unverified: true`", which a
# closing-backtick phrase never matches — the first version of this tuple missed it.
_SPEND_STEP_DEAD_WORDING = ("stale snapshot", "is_stale", "as_of", "home_unverified")


def _shared_workflow_section() -> str:
    text = (_REPO_ROOT / _SKILL_PATH).read_text(encoding="utf-8")
    start = text.index("## Shared workflow")
    end = text.index("\n## ", start + 1)
    return text[start:end]


def _spend_step_defects(section: str) -> list[str]:
    """The production check, as a predicate the guard-the-guard case runs too.

    Returns every reason the section fails: the live-read fact missing, or any dead
    persisted-snapshot phrase present. Extracted so the pre-review fixture below goes
    through the SAME logic the real assertion uses — a fixture that only asserts things
    about its own hard-coded string proves nothing about the guard (a Copilot review
    caught exactly that on the first version of this test).
    """
    defects = []
    if "reads it live" not in section:
        defects.append("missing the live-read fact")
    defects.extend(phrase for phrase in _SPEND_STEP_DEAD_WORDING if phrase in section)
    return defects


def test_skill_spend_step_teaches_the_live_read_only():
    assert _spend_step_defects(_shared_workflow_section()) == [], (
        "step 2 still teaches the persisted-snapshot branch; codex_status does a live "
        "app-server read and never serves a stale cache"
    )


def test_spend_step_guard_rejects_the_pre_review_wording():
    """Guard the guard: the wording that shipped before the 2026-08-25 review must fail the
    same predicate the production test uses, and the current text must pass it."""
    pre_review = (
        "## Shared workflow\n\n2. Treat `rate_limit` as advisory. treat `unknown` (the live "
        "read could not complete, or only a stale snapshot was available — `is_stale`/`as_of`), "
        "or `home_unverified: true` as uncertainty. reads it live"
    )
    assert _spend_step_defects(pre_review) == [
        "stale snapshot",
        "is_stale",
        "as_of",
        "home_unverified",
    ]
    # A section that dropped the live-read fact is rejected on that ground alone.
    assert _spend_step_defects("2. Treat `rate_limit` as advisory.") == [
        "missing the live-read fact"
    ]
    # …and the text as it actually stands is accepted by that same predicate.
    assert _spend_step_defects(_shared_workflow_section()) == []


# --- 2026-08-25 review, findings 3-5: rules must live in the rule section -------------
# The separating-context-from-constraints audit (R1) found five rules that directed
# behavior only from the "Shared workflow"/"Route the request" prose, and two compound
# bullets (R4). This pins the promoted/split labels so a later edit cannot quietly fold them
# back into narrative.
_REQUIRED_BINDING_RULE_LABELS = (
    "- **Preflight — readiness:**",
    "- **Preflight — spend control:**",
    "- **Spend — declare the cap:**",
    "- **Routing — sync or async:**",
    "- **Composition — opt-in:**",
)
_RETIRED_COMPOUND_RULE_LABELS = ("- **Delegation:**", "- **Retry:**")
_SPLIT_RULE_LABELS = (
    "- **Delegation — apply:**",
    "- **Delegation — scope:**",
    "- **Retry — no loops:**",
    "- **Retry — replay:**",
)
_PRIVACY_BULLET_LABELS = (
    "- **Privacy — never justify a call by workspace placement:**",
    "- **Privacy — session-identified material:**",
    "- **Privacy — do not call:**",
    "- **Privacy — a dry run is not a disclosure check:**",
    "- **Privacy — untrusted workspaces:**",
)


def _binding_rules_section() -> str:
    text = (_REPO_ROOT / _SKILL_PATH).read_text(encoding="utf-8")
    parts = text.split("## Binding rules", 1)
    assert len(parts) == 2, "the Binding rules section is gone or renamed"
    return parts[1]


def test_binding_rules_carry_the_promoted_preflight_and_routing_rules():
    rules = _binding_rules_section()
    for label in _REQUIRED_BINDING_RULE_LABELS:
        assert label in rules, f"{label} is missing from the Binding rules section"


def test_binding_rules_split_the_compound_delegation_and_retry_rules():
    rules = _binding_rules_section()
    for label in _RETIRED_COMPOUND_RULE_LABELS:
        assert label not in rules, f"{label} is the compound bullet the review split"
    for label in _SPLIT_RULE_LABELS:
        assert label in rules, f"{label} is missing from the Binding rules section"


def test_privacy_group_stays_contiguous_after_the_promotion():
    """The promoted bullets sit ABOVE the first Privacy bullet and the split bullets BELOW
    the last one, so `_privacy_rule_text()`'s contiguous slice is untouched. It also fails
    if any bullet is inserted INSIDE the Privacy group, which would truncate that slice while
    the older guards stayed green. (The first version exempted the split labels by prefix,
    which would have accepted one of them landing inside the group — a Copilot review
    caught it.)"""
    rules = _binding_rules_section()
    first_privacy = rules.index("- **Privacy")
    last_privacy = rules.rindex("- **Privacy")
    for label in _REQUIRED_BINDING_RULE_LABELS:
        assert rules.index(label) < first_privacy, (
            f"{label} was inserted at or after the Privacy group"
        )
    for label in _SPLIT_RULE_LABELS:
        assert rules.index(label) > last_privacy, (
            f"{label} was inserted at or before the last Privacy bullet"
        )

    group = _privacy_rule_text()
    for label in _PRIVACY_BULLET_LABELS:
        assert label in group, (
            f"{label} fell outside the contiguous Privacy slice — a non-Privacy bullet was "
            "inserted inside the group"
        )
