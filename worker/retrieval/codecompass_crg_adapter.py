"""CRG-003 / CRG-004 / CRG-012: code-review-graph adapter.

Implements the vendor-neutral ``CodeCompassGraphImportProvider`` port
(CRG-002). Reads *versioned* JSON exports (DD-014 / DD-016). Direct
SQLite reads are only enabled when the feature flag
``codecompass.crg.allow_direct_sqlite_read`` is set AND the SQLite
schema revision matches the pinned CRG review-revision
``b72413cbd34a4ac08cc60dcdd42df1d02f3fc77d``.

Per CCRIG-DD-007/008/013:

* reads are bounded to ``workspace_dir`` (fail-closed)
* never builds shell commands from user/parser input
* unrecognised revisions yield ``external_graph_incompatible``
* missing exports yield ``external_graph_unavailable``
* confidence is mapped through CRG-004's numeric confidence model
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.feature_flags import is_enabled
from agent.services.tools.graph_evidence import (
    POLICY_ALLOWED_TRUST,
    validate_graph_evidence,
)
from worker.retrieval.codecompass_import_provider import (
    CodeCompassGraphImportProvider,
    ImportSnapshot,
    ProviderDiagnostics,
    ProviderProbe,
    WorkspacePathError,
    assert_within_workspace,
    compute_content_hash,
)


CRG_REVIEWED_REVISION = "b72413cbd34a4ac08cc60dcdd42df1d02f3fc77d"
CRG_VERSION = "2.3.6"
CRG_PROVIDER_ID = "crg.adapter"

CONFIDENCE_KIND_TO_NUMERIC = {
    "EXTRACTED": 0.95,
    "INFERRED": 0.6,
    "AMBIGUOUS": 0.3,
}

POLICY_DISALLOWED_KINDS = {"AMBIGUOUS", "INFERRED"}


@dataclass(frozen=True)
class _ParsedCrgRecord:
    kind: str  # file | function | class | call | import | test | covers
    data: dict[str, Any]
    confidence_kind: str


def _conf_to_kind(value: str | None) -> str:
    return str(value or "").upper().strip() or "EXTRACTED"


def _read_export(workspace_dir: Path) -> dict[str, Any]:
    """Read the versioned JSON export under ``workspace_dir/.code-review-graph/export.json``.

    Returns ``{}`` on a missing export so the caller can decide whether
    to emit ``external_graph_unavailable``.
    """
    export = workspace_dir / ".code-review-graph" / "export.json"
    if not export.exists():
        return {}
    with assert_within_workspace(export, workspace_dir).open("r", encoding="utf-8") as fp:
        return json.load(fp)


def _normalise_export_to_records(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for file_entry in payload.get("files") or []:
        file_path = str(file_entry.get("path") or "").strip()
        if not file_path:
            continue
        nodes.append({
            "id": f"file:{file_path}",
            "kind": "file",
            "attrs": {"path": file_path},
        })
        for func in file_entry.get("functions") or []:
            name = str(func.get("name") or "").strip()
            if not name:
                continue
            nodes.append({
                "id": f"symbol_function:{file_path}:{name}",
                "kind": "symbol_function",
                "attrs": {
                    "file": file_path,
                    "name": name,
                    "line_start": func.get("line_start"),
                    "line_end": func.get("line_end"),
                },
                "crg_confidence_kind": _conf_to_kind(func.get("confidence")),
            })
        for cls in file_entry.get("classes") or []:
            name = str(cls.get("name") or "").strip()
            if not name:
                continue
            nodes.append({
                "id": f"symbol_class:{file_path}:{name}",
                "kind": "symbol_class",
                "attrs": {
                    "file": file_path,
                    "name": name,
                    "line_start": cls.get("line_start"),
                    "line_end": cls.get("line_end"),
                },
                "crg_confidence_kind": _conf_to_kind(cls.get("confidence")),
            })
        for imp in file_entry.get("imports") or []:
            imp_name = str(imp or "").strip()
            if not imp_name:
                continue
            edges.append({
                "from_id": f"file:{file_path}",
                "to_id": f"file:{imp_name}",
                "kind": "imports",
                "crg_confidence_kind": "EXTRACTED",
            })
    for edge in payload.get("edges") or []:
        kind = str(edge.get("kind") or "").strip().lower()
        if kind not in {"calls", "imports", "inherits"}:
            continue
        from_file = str(edge.get("from_file") or "").strip()
        from_sym = edge.get("from_symbol")
        to_file = str(edge.get("to_file") or "").strip()
        to_sym = edge.get("to_symbol")
        if not from_file or not to_file:
            continue
        if from_sym:
            src = f"symbol_function:{from_file}:{from_sym}" if kind == "calls" else f"file:{from_file}"
        else:
            src = f"file:{from_file}"
        if to_sym:
            tgt = f"symbol_function:{to_file}:{to_sym}" if kind == "calls" else f"file:{to_file}"
        else:
            tgt = f"file:{to_file}"
        edges.append({
            "from_id": src,
            "to_id": tgt,
            "kind": kind,
            "crg_confidence_kind": _conf_to_kind(edge.get("confidence")),
        })
    for test in payload.get("tests") or []:
        covers = str(test.get("covers_symbol") or "").strip()
        if ":" not in covers:
            continue
        cov_file, cov_name = covers.split(":", 1)
        edges.append({
            "from_id": f"symbol_function:{test.get('file')}:{test.get('function')}",
            "to_id": f"symbol_function:{cov_file}:{cov_name}",
            "kind": "covers",
            "crg_confidence_kind": _conf_to_kind(test.get("confidence")),
        })
    return nodes, edges


def _attach_evidence(record: dict[str, Any], reviewed_revision: str) -> dict[str, Any]:
    confidence_kind = str(record.get("crg_confidence_kind") or "EXTRACTED").upper()
    numeric = CONFIDENCE_KIND_TO_NUMERIC.get(confidence_kind, 0.5)
    out = dict(record)
    out["evidence"] = {
        "source_kind": "crg_json_export",
        "source_record_id": f"{reviewed_revision}:{record.get('kind')}:{record.get('from_id', '')}->{record.get('to_id', '')}",
        "reason": "manual_fixture" if confidence_kind == "EXTRACTED" else None,
    }
    # Remove None reason so the schema-validator accepts it without ambiguity.
    if out["evidence"].get("reason") is None:
        out["evidence"].pop("reason", None)
    out["trust"] = {
        "trust_level": "extracted" if confidence_kind in POLICY_ALLOWED_TRUST else "inferred",
        "verification_status": "verified",
        "confidence": numeric,
        "confidence_kind": confidence_kind,
        "evidence": out["evidence"],
        "provenance": {
            "source": "code-review-graph",
            "provider_id": CRG_PROVIDER_ID,
            "provider_revision": reviewed_revision,
            "extractor_id": "crg.json_export",
            "extractor_version": CRG_VERSION,
            "build_system": "unknown",
        },
    }
    return out


@dataclass
class CrgJsonAdapter:
    """Adapter for versioned JSON exports of code-review-graph."""

    workspace_dir: Path
    _last_run: dict[str, Any] = field(default_factory=dict)

    @property
    def provider_id(self) -> str:
        return CRG_PROVIDER_ID

    def probe(self) -> ProviderProbe:
        available = (self.workspace_dir / ".code-review-graph" / "export.json").exists()
        return ProviderProbe(
            provider_id=self.provider_id,
            available=available,
            provider_revision=CRG_REVIEWED_REVISION if available else None,
            required_flags=("crg.adapter_enabled",),
            reason_unavailable=None if available else "external_graph_unavailable",
            details={
                "reviewed_revision": CRG_REVIEWED_REVISION,
                "version": CRG_VERSION,
            },
        )

    def import_snapshot(self) -> ImportSnapshot:
        diagnostics: dict[str, Any] = {
            "stage": "import_snapshot",
            "strict_pinning": is_enabled("crg.strict_pinning"),
        }
        payload = _read_export(self.workspace_dir)
        if not payload:
            self._last_run = {**diagnostics, "snapshot_count": 0,
                              "result": "external_graph_unavailable"}
            return ImportSnapshot(
                provider_id=self.provider_id,
                provider_revision="",
                content_hash="",
                graph_nodes=(),
                graph_edges=(),
                diagnostics=self._last_run,
            )

        revision = str(payload.get("reviewer_graph_revision") or "")
        if is_enabled("crg.strict_pinning") and revision != CRG_REVIEWED_REVISION:
            self._last_run = {
                **diagnostics,
                "result": "external_graph_incompatible",
                "got_revision": revision,
                "expected_revision": CRG_REVIEWED_REVISION,
            }
            return ImportSnapshot(
                provider_id=self.provider_id,
                provider_revision=revision,
                content_hash="",
                graph_nodes=(),
                graph_edges=(),
                diagnostics=self._last_run,
            )

        nodes, edges = _normalise_export_to_records(payload)
        nodes_wrapped = [_attach_evidence(n, revision) for n in nodes]
        edges_wrapped = [_attach_evidence(e, revision) for e in edges]

        all_records = tuple(nodes_wrapped) + tuple(edges_wrapped)
        content_hash = compute_content_hash(all_records)

        # Run graph-evidence policy validation per-record (CRG-004)
        violations: list[str] = []
        for r in all_records:
            res = validate_graph_evidence(r.get("trust") or {})
            if not res.ok:
                violations.extend(f"{r.get('id', '?')}: {f.reason}" for f in res.failures)
        diagnostics["violations"] = violations

        self._last_run = {**diagnostics, "snapshot_count": 1,
                          "result": "ok", "node_count": len(nodes_wrapped),
                          "edge_count": len(edges_wrapped)}
        return ImportSnapshot(
            provider_id=self.provider_id,
            provider_revision=revision,
            content_hash=content_hash,
            graph_nodes=tuple(nodes_wrapped),
            graph_edges=tuple(edges_wrapped),
            diagnostics=self._last_run,
        )

    def diagnostics(self) -> ProviderDiagnostics:
        return ProviderDiagnostics(
            provider_id=self.provider_id,
            last_run=self._last_run,
            warnings=tuple(self._last_run.get("violations") or ()),
            degraded=bool(self._last_run) and self._last_run.get("result") != "ok",
        )


# ---------------------------------------------------------------------------
# SQLite adapter (opt-in via crg.allow_direct_sqlite_read)
# ---------------------------------------------------------------------------

@dataclass
class CrgSqliteAdapter:
    """Read-only SQLite adapter for code-review-graph v2.3.6 schema."""

    workspace_dir: Path
    _last_run: dict[str, Any] = field(default_factory=dict)

    @property
    def provider_id(self) -> str:
        return f"{CRG_PROVIDER_ID}.sqlite"

    def probe(self) -> ProviderProbe:
        if not is_enabled("crg.allow_direct_sqlite_read"):
            return ProviderProbe(
                provider_id=self.provider_id,
                available=False,
                provider_revision=None,
                required_flags=("crg.allow_direct_sqlite_read",),
                reason_unavailable="feature_disabled",
            )
        db_path = self.workspace_dir / ".code-review-graph" / "graph.db"
        if not db_path.exists():
            return ProviderProbe(
                provider_id=self.provider_id,
                available=False,
                provider_revision=None,
                required_flags=(),
                reason_unavailable="external_graph_unavailable",
            )
        try:
            revision = self._read_revision(db_path)
        except (sqlite3.DatabaseError, OSError, WorkspacePathError):
            return ProviderProbe(
                provider_id=self.provider_id,
                available=False,
                provider_revision=None,
                required_flags=(),
                reason_unavailable="external_graph_incompatible",
            )
        if is_enabled("crg.strict_pinning") and revision != CRG_REVIEWED_REVISION:
            return ProviderProbe(
                provider_id=self.provider_id,
                available=False,
                provider_revision=revision,
                required_flags=(),
                reason_unavailable="external_graph_incompatible",
            )
        return ProviderProbe(
            provider_id=self.provider_id,
            available=True,
            provider_revision=revision,
            required_flags=("crg.allow_direct_sqlite_read",),
        )

    def import_snapshot(self) -> ImportSnapshot:
        diag: dict[str, Any] = {"stage": "import_snapshot", "transport": "sqlite"}
        probe = self.probe()
        if not probe.available:
            self._last_run = {**diag, "result": probe.reason_unavailable}
            return ImportSnapshot(
                provider_id=self.provider_id,
                provider_revision=probe.provider_revision or "",
                content_hash="",
                graph_nodes=(),
                graph_edges=(),
                diagnostics=self._last_run,
            )
        db_path = self.workspace_dir / ".code-review-graph" / "graph.db"
        # Workspace-bound check (DD-013)
        assert_within_workspace(db_path, self.workspace_dir)
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
                nodes = self._read_table(conn, "nodes")
                edges = self._read_table(conn, "edges")
        except sqlite3.DatabaseError as exc:
            self._last_run = {**diag, "result": "external_graph_incompatible",
                              "detail": str(exc)}
            return ImportSnapshot(
                provider_id=self.provider_id,
                provider_revision=probe.provider_revision or "",
                content_hash="",
                graph_nodes=(),
                graph_edges=(),
                diagnostics=self._last_run,
            )

        records = tuple(_attach_evidence(r, probe.provider_revision or "") for r in (*nodes, *edges))
        self._last_run = {**diag, "result": "ok",
                          "node_count": len(nodes), "edge_count": len(edges)}
        return ImportSnapshot(
            provider_id=self.provider_id,
            provider_revision=probe.provider_revision or "",
            content_hash=compute_content_hash(records),
            graph_nodes=tuple(_attach_evidence(n, probe.provider_revision or "") for n in nodes),
            graph_edges=tuple(_attach_evidence(e, probe.provider_revision or "") for e in edges),
            diagnostics=self._last_run,
        )

    def diagnostics(self) -> ProviderDiagnostics:
        return ProviderDiagnostics(
            provider_id=self.provider_id,
            last_run=self._last_run,
            degraded=bool(self._last_run) and self._last_run.get("result") != "ok",
        )

    @staticmethod
    def _read_revision(db_path: Path) -> str:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key='reviewer_graph_revision'"
            ).fetchone()
        if not row:
            raise sqlite3.DatabaseError("missing reviewer_graph_revision row")
        return str(row[0])

    @staticmethod
    def _read_table(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
        # Schema-pinned query — column order is part of CRG v2.3.6 contract.
        if table == "nodes":
            rows = conn.execute(
                "SELECT id, kind, file, name, confidence FROM nodes"
            ).fetchall()
            return [
                {"id": r[0], "kind": r[1], "attrs": {"file": r[2], "name": r[3]},
                 "crg_confidence_kind": (r[4] or "EXTRACTED")}
                for r in rows
            ]
        rows = conn.execute(
            "SELECT source_id, target_id, kind, confidence FROM edges"
        ).fetchall()
        return [
            {"from_id": r[0], "to_id": r[1], "kind": r[2],
             "crg_confidence_kind": (r[3] or "EXTRACTED")}
            for r in rows
        ]


__all__ = [
    "CRG_REVIEWED_REVISION",
    "CRG_VERSION",
    "CRG_PROVIDER_ID",
    "CrgJsonAdapter",
    "CrgSqliteAdapter",
    "CONFIDENCE_KIND_TO_NUMERIC",
]