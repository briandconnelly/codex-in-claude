"""Resolve the working directory a run targets, with a clear precedence.

Precedence: explicit caller param -> first MCP root -> server cwd (with a warning).
The MCP server launches from its install dir, so a cwd fallback can silently target
the wrong repo; we surface that rather than fail. CLI-agnostic."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class WorkspaceResolution:
    path: str | None
    source: str | None  # "param" | "roots" | "cwd"
    error_code: str | None = None  # invalid_workspace_root | workspace_outside_roots
    error_detail: str | None = None


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def resolve_workspace(
    explicit: str | None,
    roots: list[str],
    server_cwd: str,
) -> WorkspaceResolution:
    """Resolve the workspace path. `roots` are absolute filesystem paths already
    extracted from the client's MCP roots (file:// URIs decoded by the caller)."""
    norm_roots = [str(Path(r).resolve()) for r in roots]
    if explicit is not None:
        candidate = Path(explicit)
        if not candidate.is_absolute():
            return WorkspaceResolution(
                None, None, "invalid_workspace_root", "workspace_root must be an absolute path"
            )
        resolved = candidate.resolve()
        if not resolved.is_dir():
            return WorkspaceResolution(
                None, None, "invalid_workspace_root", f"not a directory: {resolved}"
            )
        if norm_roots and not any(_is_within(resolved, Path(r)) for r in norm_roots):
            return WorkspaceResolution(
                None,
                None,
                "workspace_outside_roots",
                f"{resolved} is outside the client's MCP roots",
            )
        return WorkspaceResolution(str(resolved), "param")
    if norm_roots:
        return WorkspaceResolution(norm_roots[0], "roots")
    return WorkspaceResolution(str(Path(server_cwd).resolve()), "cwd")


def server_cwd() -> str:
    return str(Path.cwd())
