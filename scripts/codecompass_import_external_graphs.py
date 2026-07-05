#!/usr/bin/env python3
"""COMBO-004: CLI for build/import/diagnose.

This script is the *diagnostic* surface for the import pipeline. It
runs only read-only by default. Persistent writes go through the Hub
task flow (CCRIG-DD-009).

Examples
--------

Validate a CRG export (dry-run):

    python -m scripts.codecompass_import_external_graphs \\
        validate /tmp/export.json

Import a CRG export into the local graph store:

    python -m scripts.codecompass_import_external_graphs \\
        import-cr /tmp/export.json --workspace-dir /ws

Run a diagnostic (counts only):

    python -m scripts.codecompass_import_external_graphs \\
        diagnose /ws/.code-review-graph
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agent.feature_flags import is_enabled
from agent.services.tools.graph_evidence import validate_graph_evidence
from worker.retrieval.codecompass_crg_adapter import (
    CRG_PROVIDER_ID,
    CrgJsonAdapter,
    CrgSqliteAdapter,
)
from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore
from worker.retrieval.codecompass_rig_importer import import_snapshot_file


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_validate(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.path).read_text())
    ev = payload.get("evidence") or payload
    res = validate_graph_evidence(ev if "trust_level" in ev else {
        "trust_level": "extracted",
        "verification_status": "verified",
        "evidence": {"source_kind": "crg_json_export",
                     "source_record_id": "user-supplied",
                     "reason": "manual_fixture"},
        "provenance": {"source": "code-review-graph",
                       "provider_id": "user",
                       "provider_revision": "manual",
                       "build_system": "unknown"},
    })
    _print({"ok": res.ok, "diagnostics": res.diagnostics,
            "failures": [f.as_dict() for f in res.failures]})
    return 0 if res.ok else 1


def cmd_import_cr(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace_dir)
    if not is_enabled("crg.adapter_enabled"):
        print("crg.adapter_enabled is off; refusing to import",
              file=sys.stderr)
        return 2
    adapter = CrgJsonAdapter(workspace_dir=workspace)
    snap = adapter.import_snapshot()
    if not snap.content_hash:
        _print({"ok": False, "diagnostics": snap.diagnostics})
        return 1
    graph_store = CodeCompassGraphStore(index_path=args.index_path or
                                       str(workspace / ".codecompass" / "graph.json"))
    graph_store.rebuild_from_output_records(
        manifest_hash=snap.content_hash,
        records=[{"_provenance": {"output_kind": "graph_nodes"},
                  "id": r.get("id", f"crg:{i}"),
                  "kind": r.get("kind", "symbol"),
                  "name": (r.get("attrs") or {}).get("name"),
                  "file": (r.get("attrs") or {}).get("file")}
                 for i, r in enumerate(snap.graph_nodes)] +
                [{"_provenance": {"output_kind": "graph_edges"},
                  "source": e.get("from_id"),
                  "target": e.get("to_id"),
                  "type": e.get("kind", "related")}
                 for e in snap.graph_edges],
    )
    _print({"ok": True, "provider_id": CRG_PROVIDER_ID,
            "content_hash": snap.content_hash,
            "node_count": len(snap.graph_nodes),
            "edge_count": len(snap.graph_edges),
            "diagnostics": snap.diagnostics})
    return 0


def cmd_import_rig(args: argparse.Namespace) -> int:
    res = import_snapshot_file(
        Path(args.snapshot),
        workspace_dir=Path(args.workspace_dir) if args.workspace_dir else None,
        write_index=args.write_index,
        index_path=Path(args.index_path) if args.index_path else None,
    )
    _print({"ok": res.ok,
            "snapshot_id": res.snapshot_id,
            "content_hash": res.content_hash,
            "rig_node_count": len(res.rig_nodes),
            "rig_edge_count": len(res.rig_edges),
            "failures": list(res.failures),
            "diagnostics": res.diagnostics})
    return 0 if res.ok else 1


def cmd_diagnose(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace)
    graph_store = CodeCompassGraphStore(
        index_path=str(workspace / ".codecompass" / "graph.json"))
    payload = graph_store.load()
    diag = payload.get("diagnostics") or {}
    counts = {
        "symbol_nodes": len(payload.get("nodes") or []),
        "symbol_edges": len(payload.get("edges") or []),
        "rig_nodes": len(payload.get("rig_nodes") or []),
        "rig_edges": len(payload.get("rig_edges") or []),
        "x86_nodes": len(payload.get("x86_nodes") or []),
        "x86_edges": len(payload.get("x86_edges") or []),
    }
    counts["ambiguous_edges"] = sum(
        1 for e in (payload.get("edges") or [])
        if isinstance(e, dict) and str(e.get("confidence_kind") or "").upper() == "AMBIGUOUS"
    )
    counts["missing_evidence"] = sum(
        1 for e in (payload.get("edges") or [])
        if isinstance(e, dict)
        and not (e.get("evidence") or {}).get("source_record_id")
    )
    _print({"ok": True,
            "diagnostics": diag,
            "counts": counts})
    return 0


def cmd_probe_cr(args: argparse.Namespace) -> int:
    adapter = CrgJsonAdapter(workspace_dir=Path(args.workspace_dir))
    probe = adapter.probe()
    _print({"provider_id": probe.provider_id,
            "available": probe.available,
            "provider_revision": probe.provider_revision,
            "reason_unavailable": probe.reason_unavailable,
            "details": probe.details})
    return 0 if probe.available else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codecompass_import_external_graphs")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="Validate a CRG export (dry-run)")
    p_validate.add_argument("path", type=Path)
    p_validate.set_defaults(func=cmd_validate)

    p_imp_cr = sub.add_parser("import-cr", help="Import a CRG JSON export")
    p_imp_cr.add_argument("path", type=Path)
    p_imp_cr.add_argument("--workspace-dir", type=Path, required=True)
    p_imp_cr.add_argument("--index-path", type=Path, default=None)
    p_imp_cr.set_defaults(func=cmd_import_cr)

    p_imp_rig = sub.add_parser("import-rig", help="Import a RIG snapshot")
    p_imp_rig.add_argument("snapshot", type=Path)
    p_imp_rig.add_argument("--workspace-dir", type=Path, default=None)
    p_imp_rig.add_argument("--write-index", action="store_true")
    p_imp_rig.add_argument("--index-path", type=Path, default=None)
    p_imp_rig.set_defaults(func=cmd_import_rig)

    p_diag = sub.add_parser("diagnose", help="Diagnostic counts")
    p_diag.add_argument("workspace", type=Path)
    p_diag.set_defaults(func=cmd_diagnose)

    p_probe = sub.add_parser("probe-cr", help="Probe CRG adapter")
    p_probe.add_argument("--workspace-dir", type=Path, required=True)
    p_probe.set_defaults(func=cmd_probe_cr)

    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())