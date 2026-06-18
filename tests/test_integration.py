"""Live tests that call the real `codex` CLI. Opt in with:

    uv run pytest -m integration --no-cov

They require codex to be installed and authenticated (`codex login`). They spend
tokens, so they are excluded from the default run.
"""

from __future__ import annotations

import pytest

from codex_in_claude import codex, server

pytestmark = pytest.mark.integration


def test_status_live():
    res = server.codex_status()
    assert res["codex_found"] is True
    assert res["ready"] is True, res["readiness_detail"]


async def test_consult_live(tmp_path):
    res = await server.codex_consult(
        "Reply concisely in one sentence: what does the DRY principle mean?",
        workspace_root=str(tmp_path),
        timeout_seconds=150,
    )
    assert res["ok"] is True, res.get("error")
    assert res["summary"]
    assert res["meta"]["sandbox"] == "read-only"
    assert res["meta"]["session_id"]


def test_login_status_live():
    logged_in, _ = codex.login_status()
    assert logged_in is True
