#!/usr/bin/env python3
"""Run the deterministic, network-denied GeoMap release gate."""

from __future__ import annotations

import argparse
import json
import socket
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from agent.services.geomaps.contracts import GeoMapError
from agent.services.geomaps.geometry import sanitize_svg
from agent.services.geomaps.service import GeoMapService

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts" / "test-gates" / "geomap-catalog.json"


@contextmanager
def _deny_network():
    original = socket.create_connection

    def denied(*_args, **_kwargs):
        raise AssertionError("geomap_release_gate_network_access_forbidden")

    socket.create_connection = denied
    try:
        yield
    finally:
        socket.create_connection = original


def run_gate() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    with _deny_network():
        service = GeoMapService()
        catalog = service.catalog()
        map_count = len(catalog["maps"])
        checks.append({"id": "registry", "status": "passed" if map_count == 4 else "failed", "map_count": map_count})
        for definition in catalog["maps"]:
            geometry = service.geometry(definition["id"])
            feature = geometry["features"][0]
            region_id = feature["properties"]["id"]
            projection = service.project(
                map_id=definition["id"],
                rows=[{"region": region_id, "value": 1}],
                region_key="region",
                value_key="value",
                aggregation="sum",
                data_attribution="GeoMap release fixture",
                minimum_match_ratio=1,
            )
            checks.append(
                {
                    "id": f"offline-{definition['id']}",
                    "status": "passed" if projection.report.publication_eligible else "failed",
                    "feature_count": len(geometry["features"]),
                    "publication_eligible": projection.report.publication_eligible,
                }
            )
        sample = service.project(
            map_id="de-states",
            rows=[{"region": "DE-BE", "value": 1}],
            region_key="region",
            value_key="value",
            aggregation="sum",
            data_attribution="GeoMap release fixture",
            minimum_match_ratio=1,
        )
        for output_format in ("svg", "png", "pdf", "html"):
            artifact = service.export(projection=sample, output_format=output_format, title="GeoMap release")
            attribution_bound = bool(artifact.metadata["map_attribution"] and artifact.metadata["data_attribution"])
            checks.append(
                {
                    "id": f"export-{output_format}",
                    "status": "passed" if artifact.content and attribution_bound else "failed",
                    "nonempty": bool(artifact.content),
                    "attribution_bound": attribution_bound,
                }
            )
        malicious = b'<svg xmlns="http://www.w3.org/2000/svg"><script/><path id="x"/></svg>'
        try:
            sanitize_svg(malicious)
        except GeoMapError as exc:
            security_passed = exc.reason_code == "geomap_svg_active_content_forbidden"
        else:
            security_passed = False
        checks.append({"id": "svg-active-content", "status": "passed" if security_passed else "failed"})
    passed = all(check["status"] == "passed" for check in checks)
    return {
        "schema": "ananta.geomap-release-gate.v1",
        "status": "passed" if passed else "failed",
        "network_policy": "denied",
        "human_intervention_required": False,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run_gate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"geomap-release-gate-{report['status']}")
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
