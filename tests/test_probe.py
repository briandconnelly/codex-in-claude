"""The bridge-local sync-probe shim (#541).

`pontonier.core.runtime.run_sync_capture` promises in its own docstring to return a
CommandRun "rather than raising", but catches only (FileNotFoundError,
NotADirectoryError) -- so a spawn failure such as EACCES or ENOEXEC escapes as a raised
exception. Its sibling `run_async` catches broad OSError around Popen, and appserver's
two Popen sites catch OSError too, which is why ONLY the synchronous probe path leaks.

These tests pin the shim's classification DOMAIN, not just the two values its callers
happen to hit today (AGENTS.md -> Testing: a new parameter is new API surface). The
conversion is deliberately conservative: only an OSError carrying spawn evidence becomes
`binary_missing`; anything else propagates, because reporting a pipe-read failure as
"codex not found" would send the agent down the wrong repair path.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

import pytest
from pontonier.core import runtime

from codex_in_claude import probe


def test_passes_a_normal_run_through_unchanged() -> None:
    """The shim is transparent on the happy path: same CommandRun, no reclassification."""
    run = probe.run_probe(["echo", "hello"], timeout_seconds=10)
    assert run.exit_code == 0
    assert run.stdout.strip() == "hello"
    assert not run.binary_missing
    assert not run.timed_out


def test_passes_a_nonzero_exit_through_unchanged() -> None:
    """A command that runs and fails is a RUN failure, never binary_missing."""
    run = probe.run_probe(["sh", "-c", "exit 3"], timeout_seconds=10)
    assert run.exit_code == 3
    assert not run.binary_missing


def test_missing_binary_still_reports_binary_missing() -> None:
    """The case upstream already handles keeps working through the shim."""
    run = probe.run_probe(["definitely-not-a-real-binary-541"], timeout_seconds=10)
    assert run.binary_missing


def test_executable_directory_becomes_binary_missing_not_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #541 shape: an executable DIRECTORY named `codex` as the only PATH entry.

    `shutil.which` excludes directories, so this is reachable only through the
    bare-literal branch, where `subprocess` does its own PATH lookup and raises
    PermissionError. Upstream lets that escape; the shim converts it.
    """
    (tmp_path / "codex").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path))

    # Positive control: the UNWRAPPED upstream call raises, so this test cannot pass
    # vacuously against a shim that does nothing.
    with pytest.raises(PermissionError):
        runtime.run_sync_capture(["codex", "--version"], timeout_seconds=10)

    run = probe.run_probe(["codex", "--version"], timeout_seconds=10)
    assert run.binary_missing
    assert run.exit_code == 127
    assert not run.timed_out


def test_non_executable_file_becomes_binary_missing(tmp_path: Path) -> None:
    """A real file lacking the execute bit: EACCES on an explicit path."""
    target = tmp_path / "codex"
    target.write_text("#!/bin/sh\necho hi\n")
    target.chmod(0o644)
    run = probe.run_probe([str(target)], timeout_seconds=10)
    assert run.binary_missing


def test_non_binary_executable_becomes_binary_missing(tmp_path: Path) -> None:
    """ENOEXEC: the execute bit is set on something the kernel cannot exec."""
    target = tmp_path / "codex"
    target.write_bytes(b"\x00\x01\x02not an executable\n")
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    run = probe.run_probe([str(target)], timeout_seconds=10)
    assert run.binary_missing


def test_timeout_passes_through_as_timed_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """A timeout is upstream's own classification and must survive the shim."""
    run = probe.run_probe(["sleep", "5"], timeout_seconds=1)
    assert run.timed_out
    assert not run.binary_missing


def test_oserror_without_spawn_evidence_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-spawn OSError must NOT be reported as binary_missing.

    Reporting a pipe/communicate failure as "the binary is missing" would hand the agent
    a repair step that cannot work. `run_sync_capture` wraps spawn and communicate in one
    try block, so the shim cannot see the phase directly and instead requires positive
    spawn evidence before converting.
    """

    def _raise(*_a: object, **_k: object) -> None:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(runtime, "run_sync_capture", _raise)
    with pytest.raises(OSError, match="Input/output error"):
        probe.run_probe(["codex", "--version"], timeout_seconds=10)


def test_spawn_errno_naming_a_different_file_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A spawn-shaped errno is not enough on its own: the filename must match argv[0].

    An EACCES raised against some OTHER path is evidence about that path, not about the
    binary we asked to spawn, so it propagates rather than being reported as a missing
    codex.
    """

    def _raise(*_a: object, **_k: object) -> None:
        raise PermissionError(errno.EACCES, "Permission denied", "/some/other/file")

    monkeypatch.setattr(runtime, "run_sync_capture", _raise)
    with pytest.raises(PermissionError):
        probe.run_probe(["codex", "--version"], timeout_seconds=10)


def test_spawn_errno_with_no_filename_is_converted(monkeypatch: pytest.MonkeyPatch) -> None:
    """CPython does not always populate `filename` on the exec-phase error.

    With no filename to contradict argv[0], a spawn-shaped errno is treated as spawn
    evidence -- the safe direction, since the alternative is a raw crash out of a probe
    documented never to raise.
    """

    def _raise(*_a: object, **_k: object) -> None:
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(runtime, "run_sync_capture", _raise)
    run = probe.run_probe(["codex", "--version"], timeout_seconds=10)
    assert run.binary_missing


def test_non_oserror_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shim narrows nothing but OSError; a ValueError (e.g. a NUL in argv) still raises."""

    def _raise(*_a: object, **_k: object) -> None:
        raise ValueError("embedded null byte")

    monkeypatch.setattr(runtime, "run_sync_capture", _raise)
    with pytest.raises(ValueError):
        probe.run_probe(["codex", "--version"], timeout_seconds=10)


def test_elapsed_ms_is_recorded_on_a_converted_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A converted failure keeps the CommandRun shape callers branch on."""
    (tmp_path / "codex").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path))
    run = probe.run_probe(["codex", "--version"], timeout_seconds=10)
    assert isinstance(run, runtime.CommandRun)
    assert run.elapsed_ms >= 0
    assert run.stdout == ""


def test_forwards_optional_arguments(tmp_path: Path) -> None:
    """cwd/env/stdin_text reach upstream unchanged -- the shim adds no policy of its own."""
    run = probe.run_probe(
        ["sh", "-c", 'pwd; printf %s "$MARKER"; cat'],
        timeout_seconds=10,
        cwd=str(tmp_path),
        env={**os.environ, "MARKER": "marker-541"},
        stdin_text="from-stdin",
    )
    assert str(tmp_path) in run.stdout
    assert "marker-541" in run.stdout
    assert "from-stdin" in run.stdout


def test_no_module_calls_run_sync_capture_directly() -> None:
    """Source-level invariant: every sync spawn in this package goes through the shim.

    A future call site that reaches for `runtime.run_sync_capture` would reopen exactly
    the hole #541 closed, and no behavioral test would catch it until that specific probe
    hit a permission error in the field. `binresolve` is exempt: its npm probe already
    wraps in `except Exception` and documents "never raises".
    """
    src = Path(__file__).resolve().parents[1] / "src" / "codex_in_claude"
    offenders = {
        path.name
        for path in src.rglob("*.py")
        if path.name not in {"probe.py", "binresolve.py"} and "run_sync_capture" in path.read_text()
    }
    assert offenders == set(), (
        f"{sorted(offenders)} call runtime.run_sync_capture directly; route them through "
        "probe.run_probe so a spawn-phase OSError stays a CommandRun fact (#541)."
    )


@pytest.mark.parametrize(
    ("code", "label"),
    [
        (errno.ETXTBSY, "executable open for writing"),
        (errno.ELOOP, "symlink loop"),
        (errno.ENAMETOOLONG, "path too long"),
        (errno.EPERM, "exec denied by policy"),
    ],
)
def test_every_exec_failure_naming_the_binary_is_converted(
    code: int, label: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OSError naming argv[0] is a spawn failure whatever its errno.

    The first version of this shim keyed only on an enumerated errno set and omitted
    ETXTBSY, so an exec against an executable open for writing escaped a probe documented
    never to raise (found in review). Keying on the filename first removes the dependence
    on that list being complete; these cases pin the behaviour.
    """

    def _raise(*_a: object, **_k: object) -> None:
        raise OSError(code, label, "codex")

    monkeypatch.setattr(runtime, "run_sync_capture", _raise)
    run = probe.run_probe(["codex", "--version"], timeout_seconds=10)
    assert run.binary_missing


def test_communicate_phase_error_naming_no_file_still_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The filename-first rule must not have widened conversion to every OSError.

    Positive control for the narrowing: EIO with no filename is the shape a pipe read
    failure takes, and it must still reach the caller rather than be reported as a
    missing binary.
    """

    def _raise(*_a: object, **_k: object) -> None:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(runtime, "run_sync_capture", _raise)
    with pytest.raises(OSError, match="Input/output error"):
        probe.run_probe(["codex", "--version"], timeout_seconds=10)


def test_exec_errno_with_no_filename_is_converted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fallback rule: no filename, but an errno only an exec produces."""

    def _raise(*_a: object, **_k: object) -> None:
        raise OSError(errno.ETXTBSY, "Text file busy")

    monkeypatch.setattr(runtime, "run_sync_capture", _raise)
    assert probe.run_probe(["codex", "--version"], timeout_seconds=10).binary_missing


@pytest.mark.parametrize("name", ["codex_version", "login_status"])
def test_probe_callers_keep_their_documented_no_raise_contract(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`codex_version`/`login_status` document a return value on ANY failure, never a raise.

    Asserted against the errno that broke that promise before this shim existed, and
    against ETXTBSY, which broke it again after the first version of the predicate.
    """
    from codex_in_claude import codex

    for code in (errno.EACCES, errno.ETXTBSY, errno.ENOEXEC):

        def _raise(cmd: list[str], *_a: object, __code: int = code, **_k: object) -> None:
            raise OSError(__code, os.strerror(__code), cmd[0])

        monkeypatch.setattr(runtime, "run_sync_capture", _raise)
        assert getattr(codex, name)() in (None, (None, None))


def test_probe_help_keeps_its_documented_no_raise_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`preflight._probe_help` documents "" on any failure, never a raise."""
    from codex_in_claude import preflight

    def _raise(cmd: list[str], *_a: object, **_k: object) -> None:
        raise OSError(errno.ETXTBSY, "Text file busy", cmd[0])

    monkeypatch.setattr(runtime, "run_sync_capture", _raise)
    assert preflight._probe_help() == ""
