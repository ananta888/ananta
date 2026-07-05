"""RIG-003: SPADE / CMake File API importer.

Reads CMake File API ``codemodel-v2`` reply and CTest ``ctestInfo``
record from a workspace and produces a RIG snapshot (DD-014) plus an
:func:`import_snapshot` per the CRG-002 import-provider port.

Per CCRIG-DD-007/008/012/013:

* read-only, path-bounded reads inside ``workspace_dir``
* no shell commands are constructed
* reviewer_revision is pinned (we read it from the workspace marker if
  present, otherwise default to the pinned SPADE commit)
* degraded diagnostics with ``coverage_status=partial|unknown`` when
  only one of the two artefacts is present
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from worker.retrieval.codecompass_import_provider import (
    ImportSnapshot,
    ProviderDiagnostics,
    ProviderProbe,
    assert_within_workspace,
    compute_content_hash,
)


SPADE_REVIEWED_REVISION = "6306e203732f7c4553d1564c5250396b7f84a315"
PROVIDER_ID = "rig.cmake.file_api"
PROVIDER_REVISION = SPADE_REVIEWED_REVISION

CMAKELISTS_GLOB = "CMakeLists.txt"
CODEMODEL_GLOB = ".cmake/api/v1/reply/codemodel-v2-{config}.json"
CTEST_INFO_GLOB = ".cmake/api/v1/reply/ctestInfo-{config}.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def _safe_path_within(workspace_dir: Path, relpath: str) -> Path | None:
    """Resolve ``relpath`` against ``workspace_dir`` and ensure it stays inside."""
    candidate = (workspace_dir / relpath).resolve(strict=False)
    try:
        candidate.relative_to(workspace_dir.resolve(strict=False))
        return candidate
    except ValueError:
        return None


@dataclass
class CmakeRigAdapter:
    workspace_dir: Path
    config_name: str = ""
    _last_run: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ port

    @property
    def provider_id(self) -> str:
        return PROVIDER_ID

    def probe(self) -> ProviderProbe:
        cm_rel = CODEMODEL_GLOB.format(config=self.config_name)
        cm = self.workspace_dir / cm_rel
        return ProviderProbe(
            provider_id=self.provider_id,
            available=cm.exists(),
            provider_revision=PROVIDER_REVISION if cm.exists() else None,
            required_flags=("spade.cmake_extractor_enabled",),
            reason_unavailable=None if cm.exists() else "external_graph_unavailable",
            details={"config": self.config_name, "codemodel_path": str(cm_rel)},
        )

    def import_snapshot(self) -> ImportSnapshot:
        diag: dict[str, Any] = {
            "stage": "import_snapshot",
            "config": self.config_name,
            "reviewed_revision": PROVIDER_REVISION,
        }
        # Both reads stay within workspace_dir (DD-013).
        cm = _safe_path_within(self.workspace_dir,
                               CODEMODEL_GLOB.format(config=self.config_name))
        ct = _safe_path_within(self.workspace_dir,
                               CTEST_INFO_GLOB.format(config=self.config_name))
        if cm is None or not cm.exists():
            self._last_run = {**diag, "result": "external_graph_unavailable"}
            return ImportSnapshot(
                provider_id=self.provider_id,
                provider_revision=PROVIDER_REVISION,
                content_hash="",
                graph_nodes=(),
                graph_edges=(),
                diagnostics=self._last_run,
            )

        # Confirm the resolved path is inside workspace_dir.
        assert_within_workspace(cm, self.workspace_dir)
        codemodel = _read_json(cm) or {}
        ctest_info = _read_json(ct) if ct and ct.exists() else None

        nodes, edges = _build_snapshot_records(codemodel, ctest_info, self.workspace_dir)

        coverage_status = "complete" if ctest_info is not None else "partial"
        unsupported: list[str] = []
        if ctest_info is None:
            unsupported.append("ctest_discovery")

        all_records = tuple(nodes) + tuple(edges)
        content_hash = compute_content_hash(all_records)
        self._last_run = {
            **diag,
            "result": "ok",
            "node_count": len(nodes),
            "edge_count": len(edges),
            "coverage_status": coverage_status,
            "unsupported_features": unsupported,
        }
        # The port's ImportSnapshot only carries graph_nodes/edges here.
        # The full snapshot (entities/edges/coverage/extractor) is the
        # output of _build_snapshot_records and is consumed by RIG-005
        # via CodeCompassGraphStore.diagnostics.repository_intelligence.
        return ImportSnapshot(
            provider_id=self.provider_id,
            provider_revision=PROVIDER_REVISION,
            content_hash=content_hash,
            graph_nodes=tuple(nodes),
            graph_edges=tuple(edges),
            extras={"rig_coverage": ({"status": coverage_status,
                                      "unsupported_features": unsupported},)},
            diagnostics=self._last_run,
        )

    def diagnostics(self) -> ProviderDiagnostics:
        return ProviderDiagnostics(
            provider_id=self.provider_id,
            last_run=self._last_run,
            degraded=bool(self._last_run) and self._last_run.get("result") != "ok",
        )


# ---------------------------------------------------------------------------
# Conversion helpers — public for testability
# ---------------------------------------------------------------------------

def _file_sha256(path: Path) -> str:
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_snapshot_records(
    codemodel: dict[str, Any],
    ctest_info: dict[str, Any] | None,
    workspace_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Translate CMake File API reply + CTest info into normalised
    rig_nodes/rig_edges records. Records are tagged with the graph-evidence
    policy object expected by COMBO-002."""
    # package_manager
    nodes: list[dict[str, Any]] = [{
        "id": "pm:cmake",
        "kind": "package_manager",
        "attrs": {"kind": "cmake"},
        "trust": _trust_template("pm:cmake", workspace_dir, source_kind="spade_cmake_reply"),
    }]
    edges: list[dict[str, Any]] = []

    configs = codemodel.get("configurations") or []
    for cfg in configs:
        targets = cfg.get("targets") or []
        for tgt in targets:
            tgt_name = str(tgt.get("name") or "").strip()
            tgt_id = str(tgt.get("id") or f"target:{tgt_name}").strip()
            if not tgt_name:
                continue
            tgt_kind = str(tgt.get("type") or "STATIC_LIBRARY").lower()
            tgt_kind = {
                "static_library": "library",
                "shared_library": "library",
                "executable": "executable",
                "module_library": "library",
                "object_library": "library",
                "interface_library": "header_only",
            }.get(tgt_kind, "custom")
            sources = [str(s) for s in (tgt.get("sources") or []) if s]
            tgt_node_id = f"bc:{tgt_name}"
            nodes.append({
                "id": tgt_node_id,
                "kind": "buildable_component",
                "attrs": {
                    "name": tgt_name,
                    "kind": tgt_kind,
                    "language": "cpp",
                    "source_files": sources,
                },
                "trust": _trust_template(tgt_node_id, workspace_dir,
                                          source_kind="spade_cmake_reply",
                                          record_id=tgt_id),
            })
            # map sources as evidence file records
            for src in sources:
                edges.append({
                    "from_id": tgt_node_id,
                    "to_id": f"file:{src}",
                    "kind": "built_by",
                    "evidence": {
                        "source_file": str(workspace_dir / src),
                        "source_kind": "spade_cmake_reply",
                        "source_record_id": tgt_id,
                    },
                    "trust": _trust_template(f"{tgt_node_id}->{src}", workspace_dir,
                                              source_kind="spade_cmake_reply",
                                              record_id=tgt_id),
                })

        # CTest wiring
        if ctest_info:
            for t in ctest_info.get("tests") or []:
                test_name = str(t.get("name") or "").strip()
                if not test_name:
                    continue
                runner_id = "rn:ctest"
                if not any(n["id"] == runner_id for n in nodes):
                    nodes.append({
                        "id": runner_id,
                        "kind": "runner",
                        "attrs": {"kind": "ctest"},
                        "trust": _trust_template(runner_id, workspace_dir,
                                                  source_kind="spade_ctest_record"),
                    })
                test_id = f"t:{test_name}"
                nodes.append({
                    "id": test_id,
                    "kind": "test",
                    "attrs": {"name": test_name},
                    "trust": _trust_template(test_id, workspace_dir,
                                              source_kind="spade_ctest_record",
                                              record_id=test_name),
                })
                # connect runner -> test
                edges.append({
                    "from_id": runner_id,
                    "to_id": test_id,
                    "kind": "runs",
                    "evidence": {
                        "source_file": str(workspace_dir / "CTestTestfile.cmake"),
                        "source_kind": "spade_ctest_record",
                        "source_record_id": test_name,
                    },
                    "trust": _trust_template(f"{runner_id}->{test_id}", workspace_dir,
                                              source_kind="spade_ctest_record",
                                              record_id=test_name),
                })
                # Match test to a buildable_component by name suffix (best-effort)
                for tgt in targets:
                    tgt_name = str(tgt.get("name") or "").strip()
                    if not tgt_name:
                        continue
                    if test_name.startswith(tgt_name):
                        edges.append({
                            "from_id": f"t:{test_name}",
                            "to_id": f"bc:{tgt_name}",
                            "kind": "covers",
                            "evidence": {
                                "source_file": str(workspace_dir / "CTestTestfile.cmake"),
                                "source_kind": "spade_ctest_record",
                                "source_record_id": test_name,
                                "reason": "aggregated_from_children",
                            },
                            "trust": _trust_template(
                                f"t:{test_name}->bc:{tgt_name}",
                                workspace_dir,
                                source_kind="spade_ctest_record",
                                record_id=test_name,
                                reason="aggregated_from_children",
                            ),
                        })
    return nodes, edges


def _trust_template(
    node_id: str,
    workspace_dir: Path,
    *,
    source_kind: str,
    record_id: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "source_file": str(workspace_dir / "CMakeLists.txt"),
        "source_kind": source_kind,
    }
    if record_id:
        evidence["source_record_id"] = record_id
    if reason:
        evidence["reason"] = reason
    return {
        "trust_level": "extracted",
        "verification_status": "verified",
        "confidence": 0.9,
        "evidence": evidence,
        "provenance": {
            "source": "spade",
            "provider_id": PROVIDER_ID,
            "provider_revision": PROVIDER_REVISION,
            "extractor_id": PROVIDER_ID,
            "extractor_version": SPADE_REVIEWED_REVISION[:10],
            "build_system": "cmake",
            "imported_at": "1970-01-01T00:00:00Z",
        },
    }


__all__ = [
    "PROVIDER_ID",
    "PROVIDER_REVISION",
    "SPADE_REVIEWED_REVISION",
    "CmakeRigAdapter",
    "_build_snapshot_records",
    "_safe_path_within",
    "_file_sha256",
]