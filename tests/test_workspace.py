"""Workspace resolution precedence and validation."""

from __future__ import annotations

from codex_in_claude._core import workspace


def test_resolve_explicit_param(tmp_path):
    res = workspace.resolve_workspace(str(tmp_path), [], "/server/cwd")
    assert res.source == "param"
    assert res.path == str(tmp_path.resolve())
    assert res.error_code is None


def test_resolve_explicit_must_be_absolute(tmp_path):
    res = workspace.resolve_workspace("relative/path", [], "/server/cwd")
    assert res.error_code == "invalid_workspace_root"


def test_resolve_explicit_not_a_dir(tmp_path):
    missing = tmp_path / "nope"
    res = workspace.resolve_workspace(str(missing), [], "/server/cwd")
    assert res.error_code == "invalid_workspace_root"


def test_resolve_explicit_outside_roots(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    res = workspace.resolve_workspace(str(other), [str(root)], "/server/cwd")
    assert res.error_code == "workspace_outside_roots"


def test_resolve_explicit_inside_roots(tmp_path):
    root = tmp_path / "root"
    sub = root / "sub"
    sub.mkdir(parents=True)
    res = workspace.resolve_workspace(str(sub), [str(root)], "/server/cwd")
    assert res.source == "param"
    assert res.error_code is None


def test_resolve_from_roots(tmp_path):
    res = workspace.resolve_workspace(None, [str(tmp_path)], "/server/cwd")
    assert res.source == "roots"
    assert res.path == str(tmp_path.resolve())


def test_resolve_from_cwd_fallback(tmp_path):
    res = workspace.resolve_workspace(None, [], str(tmp_path))
    assert res.source == "cwd"
    assert res.path == str(tmp_path.resolve())
