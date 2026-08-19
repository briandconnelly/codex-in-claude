"""Adversarial tests for check_fact_sync.py, written in the same commit as the instrument."""

import re
from pathlib import Path

import check_fact_sync as cfs
import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent


def test_real_files_pass():
    assert cfs.run_probes(SKILL_ROOT) == []


@pytest.mark.parametrize("probe", cfs.PROBES, ids=lambda p: p.name)
def test_each_bad_sample_fails_its_probe(probe):
    # The recorded pre-fix text (or a plausible reworded regression) must trip the probe.
    assert not cfs.line_ok(probe, probe.bad_sample)


@pytest.mark.parametrize("probe", cfs.PROBES, ids=lambda p: p.name)
def test_anchor_disappearance_fails(probe, tmp_path):
    # Deleting every anchor-matching line is a failure, not a silent pass.
    src = (SKILL_ROOT / probe.path).read_text(encoding="utf-8").splitlines()
    stripped = [ln for ln in src if not probe.anchor.search(ln)]
    root = tmp_path / "skillcopy"
    (root / Path(probe.path)).parent.mkdir(parents=True, exist_ok=True)
    (root / probe.path).write_text("\n".join(stripped) + "\n", encoding="utf-8")
    for other in {p.path for p in cfs.PROBES} - {probe.path}:
        (root / Path(other)).parent.mkdir(parents=True, exist_ok=True)
        (root / other).write_text(
            (SKILL_ROOT / other).read_text(encoding="utf-8"), encoding="utf-8"
        )
    violations = cfs.run_probes(root)
    assert any(probe.name in v for v in violations)


def test_mutated_line_fails(tmp_path):
    # Reword SKILL.md's cache-hint sentence back to the pre-fix shape; the probe must catch it.
    probe = next(p for p in cfs.PROBES if p.name == "spec-baseline-cache-hint-carriers")
    text = (SKILL_ROOT / probe.path).read_text(encoding="utf-8")
    mutated = "\n".join(
        probe.bad_sample if probe.anchor.search(ln) else ln for ln in text.splitlines()
    )
    root = tmp_path / "skillcopy"
    for other in {p.path for p in cfs.PROBES}:
        (root / Path(other)).parent.mkdir(parents=True, exist_ok=True)
        (root / other).write_text(
            (SKILL_ROOT / other).read_text(encoding="utf-8"), encoding="utf-8"
        )
    (root / probe.path).write_text(mutated + "\n", encoding="utf-8")
    violations = cfs.run_probes(root)
    assert any(probe.name in v for v in violations)


def test_selftest_catches_sabotaged_probe():
    # A probe whose bad_sample would PASS it is a broken instrument; self_test must reject it.
    broken = cfs.Probe(
        name="broken",
        path="SKILL.md",
        section=None,
        anchor=re.compile(r"ttlMs"),
        requires=re.compile(r"ttlMs"),
        bad_sample="carries ttlMs",
    )
    assert not cfs.self_test([broken])
    assert cfs.self_test(cfs.PROBES)


def test_scoped_lines_ignores_headings_inside_fences():
    text = "\n".join(
        [
            "## Spec Baseline",
            "real line",
            "```",
            "## Not A Heading",
            "fenced line",
            "```",
            "after fence",
            "## Next Section",
            "outside",
        ]
    )
    scoped = cfs._scoped_lines(text, "Spec Baseline")
    assert "real line" in scoped
    assert "## Not A Heading" in scoped
    assert "fenced line" in scoped
    assert "after fence" in scoped
    assert "outside" not in scoped
