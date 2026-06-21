"""Behavior contract for scripts/check_github_actions_pinning.py.

The script lives under scripts/ (not the package), so coverage doesn't track it;
these tests pin its classification logic and 0/1/2 exit behavior directly. It is
loaded by path, mirroring tests/test_check_codex_contract.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_github_actions_pinning.py"
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location("check_github_actions_pinning", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load_script()


# --- iter_uses: extracting uses: values from YAML text -----------------------


def test_iter_uses_extracts_value_and_strips_inline_comment():
    text = "    steps:\n      - uses: actions/checkout@" + "a" * 40 + " # v6.0.3\n"
    found = check.iter_uses(text)
    assert found == [(2, "actions/checkout@" + "a" * 40)]


def test_iter_uses_skips_full_line_comments():
    text = "      # - uses: actions/checkout@v4\n      - run: echo hi\n"
    assert check.iter_uses(text) == []


def test_iter_uses_ignores_uses_substring_in_run_block():
    text = '      - run: echo "this uses: something"\n'
    assert check.iter_uses(text) == []


def test_iter_uses_ignores_uses_inside_multiline_literal_run_block():
    text = (
        "    steps:\n"
        "      - run: |\n"
        "          uses: not/a-real-action\n"
        "          echo done\n"
        "      - uses: actions/checkout@" + "a" * 40 + "\n"
    )
    assert check.iter_uses(text) == [(5, "actions/checkout@" + "a" * 40)]


def test_iter_uses_ignores_uses_inside_folded_run_block_with_chomp():
    text = "    steps:\n      - run: >-\n          uses: nope\n          still text\n"
    assert check.iter_uses(text) == []


def test_iter_uses_ignores_block_with_indent_then_chomp_indicator():
    # YAML allows the indentation indicator before the chomping indicator (|2-).
    text = (
        "    steps:\n"
        "      - run: |2-\n"
        "          uses: nope\n"
        "      - uses: actions/checkout@" + "a" * 40 + "\n"
    )
    assert check.iter_uses(text) == [(4, "actions/checkout@" + "a" * 40)]


def test_iter_uses_resumes_after_block_dedents():
    text = (
        "      - run: |\n          uses: ignored\n      - uses: actions/setup-uv@" + "b" * 40 + "\n"
    )
    assert check.iter_uses(text) == [(3, "actions/setup-uv@" + "b" * 40)]


def test_iter_uses_strips_surrounding_quotes():
    text = '      - uses: "actions/checkout@' + "a" * 40 + '"\n'
    assert check.iter_uses(text) == [(1, "actions/checkout@" + "a" * 40)]


# --- classify: None means OK, str means violation reason ---------------------


def test_classify_full_sha_is_ok():
    assert check.classify("actions/checkout@" + "a" * 40) is None


def test_classify_uppercase_hex_sha_is_ok():
    assert check.classify("actions/checkout@" + "A1B2C3D4" + "e" * 32) is None


def test_classify_local_action_is_ok():
    assert check.classify("./.github/actions/setup") is None


def test_classify_reusable_workflow_full_sha_is_ok():
    assert check.classify("owner/repo/.github/workflows/ci.yml@" + "b" * 40) is None


def test_classify_docker_digest_is_ok():
    assert check.classify("docker://alpine@sha256:" + "c" * 64) is None


def test_classify_tag_is_violation():
    assert check.classify("actions/checkout@v4") is not None


def test_classify_branch_is_violation():
    assert check.classify("actions/checkout@main") is not None


def test_classify_short_sha_is_violation():
    assert check.classify("actions/checkout@abc1234") is not None


def test_classify_missing_ref_is_violation():
    assert check.classify("actions/checkout") is not None


def test_classify_docker_tag_is_violation():
    assert check.classify("docker://alpine:3.18") is not None


def test_classify_docker_no_tag_is_violation():
    assert check.classify("docker://alpine") is not None


# --- main: exit codes --------------------------------------------------------


def _write_workflow(root: Path, body: str) -> None:
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "ci.yml").write_text(body)


def test_main_returns_0_when_all_pinned(tmp_path, capsys):
    _write_workflow(
        tmp_path,
        "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@" + "a" * 40 + " # v6\n",
    )
    assert check.main([str(tmp_path)]) == 0


def test_main_returns_1_on_violation(tmp_path, capsys):
    _write_workflow(tmp_path, "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n")
    assert check.main([str(tmp_path)]) == 1
    assert "actions/checkout@v4" in capsys.readouterr().out


def test_main_returns_2_when_no_workflows(tmp_path, capsys):
    assert check.main([str(tmp_path)]) == 2


def test_main_scans_yaml_extension_too(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yaml").write_text("jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n")
    assert check.main([str(tmp_path)]) == 1


# --- the real repository must stay fully pinned ------------------------------


def test_this_repository_is_fully_pinned():
    """Enforcement that rides the already-required pytest gate, not just a CI step."""
    assert check.main([str(_REPO_ROOT)]) == 0
