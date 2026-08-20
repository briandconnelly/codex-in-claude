"""Which agent-visible strings may carry a Unicode ``Cc`` control character, and what happens
when one does (#529).

#528 established the remedy for echoed PROSE: delete every ``Cc`` code point, then redact. This
module owns the other half of that split, because for a MACHINE-READABLE field the #528 remedy is
actively wrong. Deleting a byte from an identifier does not clean it — it corrupts it into a
different, still well-formed-looking identifier, and ``repair.arguments`` values are fed straight
back into a follow-up tool call. The observed instance: a ``job_id`` of ``abc<BEL><ESC>[31mdef``
echoed as ``No job 'abc[31mdef'``, naming an id the caller never sent.

So machine fields are never sanitized. They are split by **who can fix the value**:

``REJECT_PARAMS``
    A caller-supplied input the caller can simply correct. Refused at the MCP boundary by an
    advertised JSON-Schema ``pattern``, so the refusal costs nothing, happens before the handler
    runs, and reports only the static parameter NAME — never the value. The precedent is
    ``reasoning_effort`` (``config.REASONING_EFFORT_VALUE_PATTERN``).

``PRESERVE_FIELDS``
    A value that IS, or derives from, a real filesystem or model identity. POSIX permits a control
    character in a filename, so such a value may be entirely correct, and refusing it would refuse
    a legitimate repository. These keep their exact bytes forever; escaping is a *presentation*
    concern, handled where the value is rendered, not where it is carried. The precedent is
    ``Finding.file`` (``orchestration._FINDING_PROSE_KEYS`` leaves it out deliberately).

The split is on fixability, NOT on provenance. One resolved workspace path is treated the same
whether it arrived as ``workspace_root``, as an MCP root, or as the server's own cwd — an earlier
draft varied by provenance and produced three different behaviors for one directory.

**A client root is never dropped.** ``pontonier.core.workspace.resolve`` selects ``norm_roots[0]``,
so discarding a control-bearing root does not merely shrink ``candidate_roots`` — it silently
promotes the next root, or the server cwd, and retargets a PAID call at the wrong repository.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastmcp import FastMCP

from . import config

# One home for the fact "this string carries no Cc code point". `config` owns the literal so the
# reasoning-effort guard and these cannot drift apart.
CONTROL_CHAR_FREE_PATTERN = config.CONTROL_CHAR_FREE_PATTERN

# Caller-supplied inputs refused at the MCP boundary. `reasoning_effort` is deliberately absent:
# it carries the same pattern already, through its own constant and its own pre-spend re-check on
# the resolved value (an env default never crosses the MCP boundary), and folding it in here would
# give that fact a second home.
REJECT_PARAMS: tuple[str, ...] = (
    "job_id",
    "base",
    "commit",
    "model",
    "transcript_path",
)

# Machine identities kept byte-exact. Named for the completeness guard and for the reader deciding
# where a NEW field belongs; there is no code path that mutates them, which is the point.
PRESERVE_FIELDS: tuple[str, ...] = (
    "cwd",
    "candidate_roots",
    "file",  # Finding.file
    "session_id",
    "source_path",
)


def preserve(value: str) -> str:
    """Return ``value`` unchanged.

    A named no-op, not a placeholder. It marks the call sites that have *considered* control
    characters and concluded the bytes must survive, so a later reader does not mistake the
    absence of a sanitizer for an oversight and "fix" it. The tests assert length is preserved,
    so replacing this body with a sanitizer fails loudly.
    """
    return value


async def advertised_patterns(mcp: FastMCP) -> dict[str, str | None]:
    """Map each tool parameter name to the ``pattern`` its inputSchema advertises.

    Read from the live tool listing rather than from a table, so the completeness guard checks
    what clients actually receive — a table would only prove this module agrees with itself. A
    parameter appearing on several tools must advertise the same pattern on each; a disagreement
    resolves to ``None`` so the guard fails rather than passing on whichever tool happened to be
    visited last.
    """
    found: dict[str, set[str | None]] = {}
    for tool in await mcp.list_tools():
        for name, schema in (tool.parameters or {}).get("properties", {}).items():
            branches = schema.get("anyOf", [schema])
            pattern = next(
                (b.get("pattern") for b in branches if b.get("pattern") is not None), None
            )
            found.setdefault(name, set()).add(pattern)
    return {
        name: patterns.pop() if len(patterns) == 1 else None for name, patterns in found.items()
    }
