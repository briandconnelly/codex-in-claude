"""CodexBackend: this bridge's adapter on the pontonier AgentBackend protocol.

A faithful thin layer over the proven functions in `codex.py` — command
construction, artifact staging, extraction, and classification all delegate to
the same code they always ran. Since the freeze-window re-plumb, this adapter
IS the hot path: `codex.run_codex_exec` stages every model-bearing run through
`prepare()`, so the adapter can no longer drift from production behavior — it
is production behavior. Two earlier protocol-fit findings landed upstream on
the way here: `RunOutcome.events` became the opaque raw JSONL this backend's
normalize layer parses tolerantly (0.3.0), and `PreparedRun.dropped_flags`
carries the help-gated drops production surfaces as compat warnings (0.4.0).

Remaining freeze-window note: `classify_failure` reconciles effort-vs-drift
attribution with ambient `config.extra_args()` context rather than anything on
`RunRequest`; acceptable while extra args are operator-owned process state.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from pontonier.backend.protocol import ClassifiedFailure, ExecResult, PreparedRun, Usage

from codex_in_claude import codex, codex_models, config, normalize, preflight
from codex_in_claude.cli_contract import PONTONIER_CONTRACT

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pontonier.backend.protocol import RunOutcome, RunRequest

CONTRACT = PONTONIER_CONTRACT


class CodexBackend:
    """The behavior half of the Codex contract (facts live on PONTONIER_CONTRACT)."""

    def validate_request(self, request: RunRequest) -> ClassifiedFailure | None:
        # Same pre-spend shape guard the server applies to the resolved effort
        # (argv-hostile values must never reach Popen). Value-level validation is
        # deliberately upstream's: codex rejects a bad effort loudly.
        if request.reasoning_effort is not None:
            reason = config.reasoning_effort_shape_error(request.reasoning_effort)
            if reason is not None:
                return ClassifiedFailure(
                    code="invalid_reasoning_effort",
                    detail=f"the requested reasoning_effort {reason}.",
                )
        return None

    @contextlib.asynccontextmanager
    async def prepare(self, request: RunRequest) -> AsyncIterator[PreparedRun]:
        """Stage exactly what `codex.run_codex_exec` stages: a temp dir holding the
        --output-last-message target and the optional --output-schema file, argv
        from the shared builder, prompt over stdin."""
        with tempfile.TemporaryDirectory(prefix="codex-in-claude-") as tmp:
            last_msg_path = str(Path(tmp) / "last-message.txt")
            schema_path: str | None = None
            if request.schema is not None:
                schema_path = str(Path(tmp) / "schema.json")
                Path(schema_path).write_text(json.dumps(request.schema), encoding="utf-8")
            tier = "propose" if request.kind == "delegate" else "consult"
            cmd, dropped = codex.build_exec_command(
                cwd=request.cwd,
                sandbox=request.access or config.sandbox_for_tier(tier),
                isolation=request.isolation or config.defaults().isolation,
                output_last_message_path=last_msg_path,
                model=request.model,
                reasoning_effort=request.reasoning_effort,
                output_schema_path=schema_path,
                # Consult is read-only Q&A: repo membership is irrelevant, so a
                # non-repo workspace must never block the run. Review/delegate are
                # repo-grounded and keep the check. Backend policy derived from
                # `kind` — the protocol deliberately does not carry this flag.
                skip_git_repo_check=request.kind == "consult",
                extra_args=config.extra_args().tokens,
                flag_support=preflight.flag_support(),
            )
            yield PreparedRun(
                argv=tuple(cmd),
                env=self.scrub_env(dict(os.environ), request.config_mode),
                cwd=request.cwd,
                stdin_text=request.prompt,
                artifacts=tuple(p for p in (last_msg_path, schema_path) if p),
                artifact_paths={
                    name: path
                    for name, path in (("last-message", last_msg_path), ("schema", schema_path))
                    if path
                },
                dropped_flags=tuple(dropped),
            )

    def finalize(self, outcome: RunOutcome, request: RunRequest) -> ExecResult:
        answer = outcome.artifact_texts.get("last-message") or ""
        raw_events = outcome.events
        usage, session_id = normalize.parse_event_metadata(raw_events)
        structured = normalize.parse_structured(answer) if request.schema is not None else None
        return ExecResult(
            answer=answer,
            structured=structured,
            usage=Usage(
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
                total_tokens=getattr(usage, "total_tokens", None),
            )
            if usage is not None
            else None,
            session_id=session_id,
        )

    def classify_failure(self, outcome: RunOutcome, request: RunRequest) -> ClassifiedFailure:
        info = codex.classify_failure(
            outcome.run,
            last_message=outcome.artifact_texts.get("last-message"),
            events=outcome.events or None,
            reasoning_effort=request.reasoning_effort,
        )
        return ClassifiedFailure(
            code=info.code,
            detail=info.message,
            retry_after_ms=info.retry_after_ms,
        )

    def list_models(self) -> tuple[str, ...]:
        return tuple(m.slug for m in codex_models.read_model_catalog().models)

    def auth_probe(self) -> bool | None:
        authenticated, _method = codex.login_status()
        return authenticated

    def scrub_env(self, env: dict[str, str], config_mode: str | None) -> dict[str, str]:  # noqa: ARG002 — protocol signature; codex has no config modes
        # Codex inherits the caller's environment unchanged: auth rides
        # $CODEX_HOME, and connector suppression is argv (`--disable
        # remote_plugin`), not env.
        return env


# The adapter is stateless; every production path shares this instance.
BACKEND = CodexBackend()
