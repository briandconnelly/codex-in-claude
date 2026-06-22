"""Behavior contract for scripts/check_commit_message.py.

The script lives under scripts/ (not the package), so coverage doesn't track it;
these tests pin its parse/validate logic and 0/1 exit behavior directly. It is
loaded by path, mirroring tests/test_check_github_actions_pinning.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_commit_message.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("check_commit_message", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load_script()


# --- validate: None means OK, str means violation reason ---------------------


@pytest.mark.parametrize(
    "subject",
    [
        "feat: add async jobs",
        "fix(core): handle empty diff",
        "chore(config): add prek hooks",
        "docs: clarify release steps",
        "refactor(tools): split dispatch",
        "perf(jobs): cache results",
        "ci: pin actions",
        "build: bump hatchling",
        "test(schemas): cover fingerprint",
        "revert: drop async lifecycle",
        "feat(worktree)!: change diff format",
    ],
)
def test_validate_accepts_valid_messages(subject):
    assert check.validate(subject) is None


@pytest.mark.parametrize(
    "subject",
    [
        "Merge branch 'main' into feature",
        'Revert "feat: add async jobs"',
        "fixup! feat: add async jobs",
        "squash! fix(core): handle empty diff",
    ],
)
def test_validate_skips_git_generated_messages(subject):
    # These are auto-generated forms; they bypass validation (return None).
    assert check.validate(subject) is None


def test_validate_rejects_unknown_type():
    assert check.validate("feet: add a thing") is not None


def test_validate_rejects_unknown_scope():
    assert check.validate("feat(unknown): add a thing") is not None


def test_validate_rejects_missing_colon():
    assert check.validate("feat add a thing") is not None


def test_validate_rejects_empty_subject():
    assert check.validate("feat: ") is not None


@pytest.mark.parametrize("subject", ["feat:  ", "feat: \t", "fix(core):   "])
def test_validate_rejects_whitespace_only_subject(subject):
    # A subject that is only whitespace (>=1 char so the regex matches) must be
    # rejected, not silently accepted.
    assert check.validate(subject) is not None


def test_validate_rejects_capitalized_subject():
    assert check.validate("feat: Add a thing") is not None


def test_validate_rejects_trailing_period():
    assert check.validate("feat: add a thing.") is not None


def test_validate_rejects_uppercase_type():
    assert check.validate("Feat: add a thing") is not None


def test_validate_lowercase_revert_is_validated_not_skipped():
    # `revert` is an allowed type; a malformed `revert:` must still be caught.
    assert check.validate("revert: Add a thing") is not None
    assert check.validate("revert: add a thing") is None


# --- first_line: strip comments / blank lines --------------------------------


def test_first_line_skips_comment_and_blank_lines():
    text = "# please enter the commit message\n\nfeat: add a thing\n"
    assert check.first_line(text) == "feat: add a thing"


def test_first_line_empty_when_only_comments():
    assert check.first_line("# a\n# b\n") == ""


# --- main: exit codes via a commit-message file ------------------------------


def _write_msg(tmp_path: Path, body: str) -> str:
    f = tmp_path / "COMMIT_EDITMSG"
    f.write_text(body)
    return str(f)


def test_main_returns_0_for_valid_message(tmp_path):
    assert check.main([_write_msg(tmp_path, "feat: add a thing\n")]) == 0


def test_main_returns_1_for_invalid_message(tmp_path, capsys):
    assert check.main([_write_msg(tmp_path, "nope nope\n")]) == 1
    assert "Conventional Commit" in capsys.readouterr().out


def test_main_returns_1_when_no_argument():
    assert check.main([]) == 1
