"""Adversarial tests for the rule-id checker.

Each case is a violation the checker must catch. They exist because the first
version of the checker passed all of them: it matched only `- ` bullets, took
any bracket body as an id, ignored code fences, skipped whole declaration
lines when scanning citations, and had no way to notice a deleted rule that
nothing cited.

`declared_ids` and `unresolved_citations` read the real checklist path, so the
tests here drive `declared_ids` directly on synthetic text and exercise the
manifest comparison on synthetic id sets.
"""

from __future__ import annotations

import check_rule_ids as c

HEADER = "# Checklist\n\nPreamble prose.\n\n## 1. Server-Level\n\n"

# A floor, not the exact count: the shipped checklist should never collapse to a
# handful of rules without someone noticing, but the exact total moves.
MIN_EXPECTED_RULES = 100


def problems_for(body: str) -> list[str]:
    _, problems = c.declared_ids(HEADER + body)
    return problems


def test_canonical_rule_is_accepted():
    ids, problems = c.declared_ids(HEADER + "- `[1.name]` **Name it.** Body.\n")
    assert ids == {"1.name": 7}
    assert problems == []


def test_star_marker_rule_without_id_is_caught():
    assert any("canonical" in p for p in problems_for("* **Name it.** Body.\n"))


def test_ordered_marker_rule_without_id_is_caught():
    assert any("canonical" in p for p in problems_for("1. **Name it.** Body.\n"))


def test_dash_rule_without_id_is_caught():
    assert any("canonical" in p for p in problems_for("- **Name it.** Body.\n"))


def test_uppercase_or_underscore_slug_is_malformed():
    assert any("malformed" in p for p in problems_for("- `[1.Bad_Slug]` **X.** B.\n"))


def test_extra_dot_in_slug_is_malformed():
    assert any("malformed" in p for p in problems_for("- `[1.bad.slug]` **X.** B.\n"))


def test_duplicate_id_is_caught():
    body = "- `[1.name]` **A.** B.\n- `[1.name]` **C.** D.\n"
    assert any("duplicate" in p for p in problems_for(body))


def test_section_prefix_mismatch_is_caught():
    assert any("declares section" in p for p in problems_for("- `[7.name]` **A.** B.\n"))


def test_declaration_before_any_section_is_caught():
    text = "# Checklist\n\n- `[1.name]` **A.** B.\n"
    _, problems = c.declared_ids(text)
    assert any("before any numbered section" in p for p in problems)


def test_fenced_declaration_does_not_count():
    body = "```\n- `[1.fake]` **Fake.** Body.\n```\n"
    ids, problems = c.declared_ids(HEADER + body)
    assert ids == {}
    assert problems == []


def test_tilde_fenced_declaration_does_not_count():
    body = "~~~\n- `[1.fake]` **Fake.** Body.\n~~~\n"
    ids, _ = c.declared_ids(HEADER + body)
    assert ids == {}


def test_nested_rule_is_accepted():
    body = "- `[1.outer]` **Outer.** Body.\n  - `[1.inner]` **Inner.** Body.\n"
    ids, problems = c.declared_ids(HEADER + body)
    assert set(ids) == {"1.outer", "1.inner"}
    assert problems == []


def test_manifest_reports_deleted_rule(tmp_path, monkeypatch):
    manifest = tmp_path / "rule-ids.txt"
    manifest.write_text("# header\n1.name\n1.gone\n")
    monkeypatch.setattr(c, "MANIFEST", manifest)
    problems = c.manifest_drift({"1.name": 1})
    assert any("1.gone" in p and "no longer declared" in p for p in problems)


def test_manifest_reports_unregistered_rule(tmp_path, monkeypatch):
    manifest = tmp_path / "rule-ids.txt"
    manifest.write_text("1.name\n")
    monkeypatch.setattr(c, "MANIFEST", manifest)
    problems = c.manifest_drift({"1.name": 1, "1.surprise": 2})
    assert any("1.surprise" in p and "not in the manifest" in p for p in problems)


def test_manifest_match_is_silent(tmp_path, monkeypatch):
    manifest = tmp_path / "rule-ids.txt"
    manifest.write_text("# comment\n\n1.name\n")
    monkeypatch.setattr(c, "MANIFEST", manifest)
    assert c.manifest_drift({"1.name": 1}) == []


def test_indented_manifest_comment_is_not_a_rule_id(tmp_path, monkeypatch):
    """An indented comment must not survive stripping and read as an id."""
    manifest = tmp_path / "rule-ids.txt"
    manifest.write_text("  # indented note\n\t# tabbed note\n1.name\n")
    monkeypatch.setattr(c, "MANIFEST", manifest)
    assert c.manifest_drift({"1.name": 1}) == []


def test_citation_regex_ignores_malformed_ids():
    # `[1.Bad_Slug]` is not a citation shape, so it cannot resolve by accident.
    assert c.CITE_RE.findall("see `[1.Bad_Slug]` and `[1.good-slug]`") == ["1.good-slug"]


def test_real_checklist_passes():
    """The shipped checklist satisfies its own convention."""
    ids, problems = c.declared_ids(c.CHECKLIST.read_text(encoding="utf-8"))
    assert problems == []
    assert len(ids) > MIN_EXPECTED_RULES
    assert c.unresolved_citations(ids) == []
    assert c.manifest_drift(ids) == []
