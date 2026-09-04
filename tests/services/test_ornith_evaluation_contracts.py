from __future__ import annotations

import json
from pathlib import Path

from scripts.benchmark.ornith_benchmark_support import evidence_projection

ROOT = Path(__file__).resolve().parents[2]


def test_catalog_has_tri_state_capabilities_and_unavailable_397b() -> None:
    catalog = json.loads((ROOT / "config/models/ornith-1.5.v1.json").read_text())
    assert catalog["production_default_allowed"] is False
    assert {item["variant_id"] for item in catalog["variants"]} == {
        "ornith-1.5-9b", "ornith-1.5-35b-a3b", "ornith-1.5-397b"
    }
    allowed = {"supported", "unsupported", "unknown"}
    assert all(claim["value"] in allowed for item in catalog["variants"] for claim in item["capabilities"].values())
    assert catalog["variants"][-1]["local_state"] == "known_but_unavailable"


def test_benchmark_contract_requires_repeats_and_closed_bindings() -> None:
    matrix = json.loads((ROOT / "benchmarks/models/ornith-1.5-codecompass-matrix.v1.json").read_text())
    assert matrix["repeats"] >= 5
    assert matrix["tool_execution"] is False
    assert "hub_run_id" in matrix["required_bindings"]
    assert evidence_projection(None) == {
        "state": "unverified", "reason_code": "hub_evidence_assignment_missing"
    }


def test_vision_fixtures_are_small_deterministic_specs() -> None:
    benchmark = json.loads((ROOT / "benchmarks/models/ornith-1.5-vision.v1.json").read_text())
    fixture_root = ROOT / benchmark["fixture_directory"]
    for case in benchmark["cases"]:
        payload = json.loads((fixture_root / case["fixture"]).read_text())
        assert payload["schema"] == "ananta.synthetic-image-fixture.v1"
        assert 1 <= payload["width"] <= 4096
        assert 1 <= payload["height"] <= 4096
