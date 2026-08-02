from __future__ import annotations

import json
import os
import platform
import statistics
import time
from pathlib import Path

import pytest

from agent.services.organization_projection_service import OrganizationProjectionService
from tests.organization_support import organization_limits


class SyntheticTopologyReader:
    """One batched read-port call; production query count is verified separately."""

    def __init__(self, *, node_count: int, total_edge_count: int) -> None:
        if node_count < 2 or total_edge_count < node_count - 1:
            raise ValueError("synthetic_topology_shape_invalid")
        self.node_count = node_count
        self.total_edge_count = total_edge_count
        self.calls = 0

    def load_topology_snapshot(self, **_kwargs):
        self.calls += 1
        units = [
            {
                "id": f"team-{index}",
                "unit_key": f"team_{index}",
                "unit_kind": "team",
                "parent_unit_key": None,
                "team_id": f"team-{index}",
            }
            for index in range(self.node_count - 1)
        ]
        relation_count = self.total_edge_count - len(units)
        relations = [
            {
                "id": f"relation-{index}",
                "kind": "declared_dependency",
                "source_unit_key": f"team_{index % len(units)}",
                "target_unit_key": f"team_{(index + 1) % len(units)}",
                "definition_relation_ref": f"relation_{index}",
            }
            for index in range(relation_count)
        ]
        return {
            "organization_id": "organization-performance",
            "name": "Synthetic organization",
            "definition_revision": "d" * 64,
            "snapshot_hash": "e" * 64,
            "units": units,
            "role_slots": [],
            "assignments": [],
            "relations": relations,
            "runtime_edges": [],
            "diagnostics": [],
            "next_cursor": None,
        }


def _sample_projection(*, node_count: int, edge_count: int, samples: int = 30) -> dict:
    reader = SyntheticTopologyReader(
        node_count=node_count,
        total_edge_count=edge_count,
    )
    service = OrganizationProjectionService(topology_reader=reader)

    def project() -> dict:
        return service.project(
            tenant_id="tenant-performance",
            project_id="project-performance",
            organization_id="organization-performance",
            limits=organization_limits(),
            page_size=500,
            max_depth=8,
        )

    for _ in range(5):
        project()
    durations_ms: list[float] = []
    result = {}
    for _ in range(samples):
        started = time.perf_counter()
        result = project()
        durations_ms.append((time.perf_counter() - started) * 1000)
    ordered = sorted(durations_ms)
    p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)]
    return {
        "result": result,
        "p95_ms": p95,
        "samples_ms": durations_ms,
        "reader_calls": reader.calls,
        "warmup_count": 5,
        "sample_count": samples,
    }


@pytest.mark.integration
def test_32_team_hierarchy_projection_p95_budget(tmp_path: Path) -> None:
    measurement = _sample_projection(node_count=33, edge_count=32)

    assert len(measurement["result"]["nodes"]) == 33
    assert measurement["p95_ms"] <= 1500
    _write_report(tmp_path, "hierarchy-32", measurement, budget_ms=1500)


@pytest.mark.integration
def test_500_node_2000_edge_graph_projection_p95_budget(tmp_path: Path) -> None:
    measurement = _sample_projection(node_count=500, edge_count=2000)

    assert len(measurement["result"]["nodes"]) == 500
    assert len(measurement["result"]["edges"]) == 2000
    assert measurement["p95_ms"] <= 2500
    _write_report(tmp_path, "graph-500-2000", measurement, budget_ms=2500)


def test_topology_page_uses_one_batched_read_port_call() -> None:
    reader = SyntheticTopologyReader(node_count=100, total_edge_count=100)
    result = OrganizationProjectionService(topology_reader=reader).project(
        tenant_id="tenant-performance",
        project_id="project-performance",
        organization_id="organization-performance",
        limits=organization_limits(),
        page_size=100,
    )

    assert len(result["nodes"]) == 100
    assert reader.calls == 1
    assert reader.calls <= 12


def _write_report(
    output_root: Path,
    scenario: str,
    measurement: dict,
    *,
    budget_ms: float,
) -> None:
    samples = list(measurement["samples_ms"])
    report = {
        "schema": "ananta.organization.projection-performance.v1",
        "scenario": scenario,
        "status": "passed" if measurement["p95_ms"] <= budget_ms else "failed",
        "hardware": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
            "python": platform.python_version(),
        },
        "data": {
            "nodes": len(measurement["result"]["nodes"]),
            "edges": len(measurement["result"]["edges"]),
        },
        "warmup_count": measurement["warmup_count"],
        "sample_count": measurement["sample_count"],
        "p50_ms": statistics.median(samples),
        "p95_ms": measurement["p95_ms"],
        "budget_ms": budget_ms,
        "bottleneck": "pure_projection_cpu_and_allocation",
    }
    (output_root / f"{scenario}.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
