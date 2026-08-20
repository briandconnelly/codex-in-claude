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

``PRESERVE_CARRIERS``
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

# Machine identities kept byte-exact, named by CARRIER rather than by bare leaf name.
#
# The qualification is load-bearing, not cosmetic. `model` and `base` appear on both sides of
# this module: as an INPUT they are rejected (`REJECT_PARAMS`), while the same names as stored
# CARRIERS — `meta.model`, `meta.base` on a record written before that rejection existed — are
# replayed byte-exact, because rewriting a stored value would report something the run never
# sent. A registry keyed on the bare leaf could not express that at all, and would have had to
# call one of the two behaviors a contradiction.
#
# There is deliberately no `preserve()` helper. Preservation is the ABSENCE of a sanitizer, so a
# helper would have no production caller, and its tests would prove only that the helper returns
# its argument. The tests instead drive the real functions — `orchestration._sanitize_finding`
# and `server._sanitize_stored_presentation` — which is where a regression would appear.
PRESERVE_CARRIERS: tuple[str, ...] = (
    # Resolved workspace identity, whatever route it arrived by (param, MCP root, server cwd).
    "meta.cwd",
    "workspace.cwd",
    "error.repair.arguments.workspace_root",
    "error.candidate_roots",
    # Model-produced identifiers.
    "findings[].file",
    "meta.session_id",
    "raw_response.session_id",
    # Resolved filesystem identity echoed back on a transfer.
    "source_path",
    # Stored carriers on a pre-#529 record: rejected as new INPUTS, replayed as written.
    "meta.model",
    "meta.base",
    "meta.commit",
    "meta.paths[]",
)

# Derived path LABELS, not callable filesystem paths: git is forced to C-quote control
# characters (`pontonier.core.gitdiff`), so these already arrive escaped and are neither
# preserved-byte-exact nor rejected. Listed so a reader does not mistake their absence above
# for an oversight.
DERIVED_PATH_LABELS: tuple[str, ...] = (
    "meta.redacted_paths",
    "coverage.redaction.withheld_paths",
    "coverage.redaction.masked_paths",
)


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
