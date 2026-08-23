"""Public `codex_bin()` entry point (#3): explicit-override precedence/validation,
delegation to the candidate resolver, and process-level caching.

Precedence, per the issue spec:
  1. `CODEX_IN_CLAUDE_CODEX_BIN`, if set to a non-empty value, wins outright and is
     taken EXACTLY as given -- no `shutil.which` re-resolution, even for a bare
     name with no path separators. If the given path does not exist on disk, this
     raises loudly (`binpath.BinaryNotFoundError`) rather than silently falling
     through to the candidate resolver.
  2. Otherwise, delegate to `binresolve.resolve_codex_bin()`.
  3. If that returns None, fall back to the bare literal "codex" (matching
     `cli_contract.CODEX_BIN` -- never regress to "no codex invocation possible").

The resolved value is cached for the process lifetime; `reset_cache()` clears it.
Uses the `clean_env` fixture (strips CODEX_IN_CLAUDE_* vars) so every test starts
from "override unset."
"""

from __future__ import annotations

import pytest

from codex_in_claude import binpath, binresolve, cli_contract

ENV_VAR = "CODEX_IN_CLAUDE_CODEX_BIN"


# --- explicit override: wins, taken literally -----------------------------------


def test_explicit_override_wins_and_is_returned_verbatim(clean_env, tmp_path):
    override = tmp_path / "my-custom-codex"
    override.write_text("#!/bin/sh\necho codex\n")
    clean_env.setenv(ENV_VAR, str(override))
    assert binpath.codex_bin() == str(override)


def test_explicit_override_does_not_consult_the_candidate_resolver(
    clean_env, tmp_path, monkeypatch
):
    override = tmp_path / "my-custom-codex"
    override.write_text("#!/bin/sh\necho codex\n")
    clean_env.setenv(ENV_VAR, str(override))

    def _boom():
        raise AssertionError("a valid override must not fall through to the resolver")

    monkeypatch.setattr(binresolve, "resolve_codex_bin", _boom)
    assert binpath.codex_bin() == str(override)


# --- explicit override: must be validated (raises loudly, no silent fallthrough) -


def test_explicit_override_missing_path_raises_loudly(clean_env, tmp_path):
    missing = tmp_path / "does-not-exist"
    clean_env.setenv(ENV_VAR, str(missing))
    with pytest.raises(binpath.BinaryNotFoundError):
        binpath.codex_bin()


def test_explicit_override_missing_path_does_not_fall_through_to_resolver(
    clean_env, tmp_path, monkeypatch
):
    missing = tmp_path / "does-not-exist"
    clean_env.setenv(ENV_VAR, str(missing))

    def _boom():
        raise AssertionError("a bad override must fail loudly, never fall through silently")

    monkeypatch.setattr(binresolve, "resolve_codex_bin", _boom)
    with pytest.raises(binpath.BinaryNotFoundError):
        binpath.codex_bin()


def test_bare_filename_override_is_validated_as_a_literal_path_and_raises(
    clean_env, tmp_path, monkeypatch
):
    """A bare name with no path separators is accepted as a LITERAL path (never
    rejected outright, never re-resolved via PATH) -- so in a cwd with no `codex`
    file it fails existence validation exactly like any other missing override,
    per point 2 of the spec."""
    monkeypatch.chdir(tmp_path)
    clean_env.setenv(ENV_VAR, "codex")
    with pytest.raises(binpath.BinaryNotFoundError):
        binpath.codex_bin()


# --- empty override == unset ------------------------------------------------------


def test_empty_override_is_treated_as_unset(clean_env, monkeypatch):
    clean_env.setenv(ENV_VAR, "")
    monkeypatch.setattr(binresolve, "resolve_codex_bin", lambda: "/resolved/codex")
    assert binpath.codex_bin() == "/resolved/codex"


# --- no override: delegates to the candidate resolver -----------------------------


def test_no_override_delegates_to_candidate_resolver(clean_env, monkeypatch):
    calls: list[None] = []

    def fake_resolve():
        calls.append(None)
        return "/resolved/codex"

    monkeypatch.setattr(binresolve, "resolve_codex_bin", fake_resolve)
    assert binpath.codex_bin() == "/resolved/codex"
    assert len(calls) == 1


def test_resolver_returning_none_falls_back_to_bare_codex_literal(clean_env, monkeypatch):
    monkeypatch.setattr(binresolve, "resolve_codex_bin", lambda: None)
    assert binpath.codex_bin() == cli_contract.CODEX_BIN
    assert binpath.codex_bin() == "codex"


# --- process-level caching ----------------------------------------------------------


def test_result_is_cached_for_process_lifetime(clean_env, monkeypatch):
    calls = {"n": 0}

    def fake_resolve():
        calls["n"] += 1
        return "/resolved/codex"

    monkeypatch.setattr(binresolve, "resolve_codex_bin", fake_resolve)
    first = binpath.codex_bin()
    second = binpath.codex_bin()
    assert first == second == "/resolved/codex"
    assert calls["n"] == 1  # second call served from cache, not re-resolved


def test_reset_cache_forces_a_fresh_resolve(clean_env, monkeypatch):
    responses = iter(["/resolved/codex-v1", "/resolved/codex-v2"])
    monkeypatch.setattr(binresolve, "resolve_codex_bin", lambda: next(responses))
    first = binpath.codex_bin()
    binpath.reset_cache()
    second = binpath.codex_bin()
    assert first == "/resolved/codex-v1"
    assert second == "/resolved/codex-v2"
