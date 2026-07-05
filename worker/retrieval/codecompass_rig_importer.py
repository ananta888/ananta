"""RIG-012: versioned RIG-Schema und manueller Import-Contract.

This module is the entry point for ``scripts/import_repository_intelligence_graph.py``.
It validates a snapshot file against the versioned schema (DD-014) and
graph-evidence policy (DD-016) and, only when ``--write-index`` is set,
loads the normalized nodes/edges into the CodeCompass graph store.

The CLI is *validate/dry-run by default*. Persistent writes go through
the Hub task flow (CCRIG-DD-009).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.services.tools.graph_evidence import (
    enforce_import_invariants,
    validate_repository_intelligence_snapshot,
)


SCHEMA_VERSION = "codecompass.repository-intelligence.v1"
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})


@dataclass(frozen=True)
class ImportResult:
    ok: bool
    snapshot_id: str | None
    content_hash: str | None
    rig_nodes: tuple[dict[str, Any], ...]
    rig_edges: tuple[dict[str, Any], ...]
    failures: tuple[dict[str, Any], ...]
    diagnostics: dict[str, Any]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="import_repository_intelligence_graph",
        description=(
            "Validate (and optionally persist) a RIG snapshot against the "
            "versioned schema. Default is validate/dry-run; --write-index "
            "must be explicit."
        ),
    )
    parser.add_argument("snapshot", type=Path, help="Path to RIG snapshot JSON")
    parser.add_argument(
        "--workspace-dir",
        type=Path,
        default=None,
        help="Workspace root for path-bound checks (DD-013).",
    )
    parser.add_argument(
        "--write-index",
        action="store_true",
        help="Persist normalized rig_nodes/rig_edges to the graph store.",
    )
    parser.add_argument(
        "--index-path",
        type=Path,
        default=None,
        help="Destination index path. Required iff --write-index.",
    )
    return parser.parse_args(argv)


def _content_hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _normalize_to_rig_nodes_edges(snapshot: dict[str, Any]) -> tuple[tuple[dict[str, Any], ...],
                                                                    tuple[dict[str, Any], ...]]:
    """Flatten snapshot entities+edges into rig_nodes/rig_edges arrays.

    IDs are kept verbatim from the snapshot. No synthetic IDs are
    introduced (AGENTS.md: source-grounded answers).
    """
    entities = snapshot.get("entities") or {}
    nodes: list[dict[str, Any]] = []
    kind_map = {
        "package_managers": "package_manager",
        "external_packages": "external_package",
        "buildable_components": "buildable_component",
        "aggregators": "aggregator",
        "runners": "runner",
        "tests": "test",
    }
    for entities_key, kind in kind_map.items():
        for ent in entities.get(entities_key) or []:
            if not isinstance(ent, dict):
                continue
            nodes.append({
                "id": ent.get("id"),
                "kind": kind,
                "attrs": {k: v for k, v in ent.items() if k != "id"},
                "provenance": snapshot.get("extractor"),
            })

    edges_out: list[dict[str, Any]] = []
    for edge in snapshot.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        edges_out.append({
            "from_id": edge.get("from_id"),
            "to_id": edge.get("to_id"),
            "kind": edge.get("kind"),
            "evidence": edge.get("evidence"),
            "trust": edge.get("trust"),
        })

    return tuple(nodes), tuple(edges_out)


def import_snapshot_file(
    snapshot_path: Path,
    *,
    workspace_dir: Path | None,
    write_index: bool,
    index_path: Path | None,
) -> ImportResult:
    """Validate (and optionally persist) one snapshot file."""
    diagnostics: dict[str, Any] = {"stage": "import", "write_index": write_index}
    raw_bytes = snapshot_path.read_bytes()

    try:
        snapshot = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        return ImportResult(
            ok=False,
            snapshot_id=None,
            content_hash=None,
            rig_nodes=(),
            rig_edges=(),
            failures=({"reason": "invalid_json", "detail": str(exc)},),
            diagnostics=diagnostics,
        )

    schema_version = snapshot.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        return ImportResult(
            ok=False,
            snapshot_id=snapshot.get("snapshot_id"),
            content_hash=None,
            rig_nodes=(),
            rig_edges=(),
            failures=({
                "reason": "schema_version_unsupported",
                "detail": f"got {schema_version!r}, expected one of {sorted(SUPPORTED_SCHEMA_VERSIONS)}",
            },),
            diagnostics=diagnostics,
        )

    ws = workspace_dir or snapshot_path.parent.resolve()
    inv = enforce_import_invariants(
        snapshot=snapshot,
        workspace_dir=ws,
        raw_bytes=raw_bytes,
    )

    if not inv.ok:
        return ImportResult(
            ok=False,
            snapshot_id=snapshot.get("snapshot_id"),
            content_hash=None,
            rig_nodes=(),
            rig_edges=(),
            failures=tuple(f.as_dict() for f in inv.failures),
            diagnostics={**diagnostics, **inv.diagnostics},
        )

    rig_nodes, rig_edges = _normalize_to_rig_nodes_edges(snapshot)
    content_hash = _content_hash({"nodes": list(rig_nodes), "edges": list(rig_edges)})

    if write_index:
        if index_path is None:
            return ImportResult(
                ok=False,
                snapshot_id=snapshot.get("snapshot_id"),
                content_hash=content_hash,
                rig_nodes=rig_nodes,
                rig_edges=rig_edges,
                failures=({"reason": "missing_index_path",
                           "detail": "--write-index requires --index-path"},),
                diagnostics=diagnostics,
            )
        payload = {
            "rig_nodes": list(rig_nodes),
            "rig_edges": list(rig_edges),
            "diagnostics": {
                "repository_intelligence": {
                    "snapshot_id": snapshot.get("snapshot_id"),
                    "coverage_status": (snapshot.get("coverage") or {}).get("status"),
                    "extractor": snapshot.get("extractor"),
                    "schema_version": schema_version,
                    "content_hash": content_hash,
                },
            },
        }
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    return ImportResult(
        ok=True,
        snapshot_id=snapshot.get("snapshot_id"),
        content_hash=content_hash,
        rig_nodes=rig_nodes,
        rig_edges=rig_edges,
        failures=(),
        diagnostics={**diagnostics, "schema_version": schema_version},
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.snapshot.exists():
        print(f"snapshot file not found: {args.snapshot}", file=sys.stderr)
        return 2
    result = import_snapshot_file(
        args.snapshot,
        workspace_dir=args.workspace_dir,
        write_index=args.write_index,
        index_path=args.index_path,
    )
    print(json.dumps({
        "ok": result.ok,
        "snapshot_id": result.snapshot_id,
        "content_hash": result.content_hash,
        "rig_node_count": len(result.rig_nodes),
        "rig_edge_count": len(result.rig_edges),
        "failures": list(result.failures),
        "diagnostics": result.diagnostics,
    }, ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())