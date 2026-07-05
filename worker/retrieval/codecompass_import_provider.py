"""CRG-002 + RIG-002: vendor-neutral graph import provider port.

Two adapters (CRG and RIG) must not derive from each other. They
implement this small capability-based port instead. Providers are *only*
responsible for turning an external source into normalized graph records
(``graph_nodes`` / ``graph_edges``) plus diagnostics. Trust/evidence
policy is enforced by :mod:`agent.services.tools.graph_evidence`.

Lifecycle:

1. ``probe()`` returns a :class:`ProviderProbe` describing whether the
   source is available, what revision, and which feature flags gate it.
2. ``import_snapshot()`` returns :class:`ImportSnapshot` containing
   normalized records and a content_hash.
3. ``diagnostics()`` summarises the last run for the Hub/CLI.

All file reads are bounded by ``workspace_dir`` (DD-013) and never build
shell commands from user/parser input.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# capability flag values
# ---------------------------------------------------------------------------

CAP_PROBE = "probe"
CAP_IMPORT_SNAPSHOT = "import_snapshot"
CAP_DIAGNOSTICS = "diagnostics"


# ---------------------------------------------------------------------------
# value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderProbe:
    provider_id: str
    available: bool
    provider_revision: str | None
    required_flags: tuple[str, ...]
    reason_unavailable: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ImportSnapshot:
    provider_id: str
    provider_revision: str
    content_hash: str
    graph_nodes: tuple[dict[str, Any], ...]
    graph_edges: tuple[dict[str, Any], ...]
    # Provider-specific slots, e.g. ``rig_nodes`` / ``rig_edges``
    extras: dict[str, tuple[dict[str, Any], ...]] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderDiagnostics:
    provider_id: str
    last_run: dict[str, Any]
    warnings: tuple[str, ...] = ()
    degraded: bool = False


# ---------------------------------------------------------------------------
# capability port
# ---------------------------------------------------------------------------

@runtime_checkable
class CodeCompassGraphImportProvider(Protocol):
    """Small capability-based port for graph-import providers."""

    @property
    def provider_id(self) -> str: ...

    def probe(self) -> ProviderProbe: ...

    def import_snapshot(self) -> ImportSnapshot: ...

    def diagnostics(self) -> ProviderDiagnostics: ...


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class WorkspacePathError(ValueError):
    """Raised when a read path escapes the workspace_dir."""


def assert_within_workspace(path: Path, workspace_dir: Path) -> Path:
    """Fail-closed path-bound check (DD-013)."""
    try:
        candidate = Path(path).resolve(strict=False)
        workspace = workspace_dir.resolve(strict=False)
        candidate.relative_to(workspace)
        return candidate
    except (ValueError, RuntimeError) as exc:
        raise WorkspacePathError(
            f"path {path!r} is outside workspace_dir {workspace_dir!r}"
        ) from exc


def compute_content_hash(records: tuple[dict[str, Any], ...]) -> str:
    """Stable content hash over a sequence of normalised records.

    Uses Python's built-in :mod:`hashlib` with sorted JSON encoding so
    the hash is deterministic across platforms and Python versions.
    """
    import hashlib
    import json

    payload = json.dumps(list(records), sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "CAP_PROBE",
    "CAP_IMPORT_SNAPSHOT",
    "CAP_DIAGNOSTICS",
    "ProviderProbe",
    "ImportSnapshot",
    "ProviderDiagnostics",
    "CodeCompassGraphImportProvider",
    "WorkspacePathError",
    "assert_within_workspace",
    "compute_content_hash",
]