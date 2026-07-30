from __future__ import annotations

from pathlib import Path

from scripts.source_control_container_smoke import (
    load_definition as load_container_definition,
    plan as container_plan,
)
from scripts.source_control_control_center_harness import (
    load_definition as load_harness_definition,
    plan as harness_plan,
)


ROOT = Path(__file__).resolve().parents[1]


def test_container_definition_has_no_invented_measurements() -> None:
    definition = load_container_definition(
        ROOT
        / "artifacts/test-gates/source-control-container-smoke-definition.json"
    )
    report = container_plan(definition)

    assert report["status"] == "unverified"
    assert all(item["duration_ms"] is None for item in report["results"])


def test_load_definition_has_no_invented_measurements_or_ids() -> None:
    definition = load_harness_definition(
        ROOT
        / "artifacts/test-gates/source-control-load-recovery-definition.json"
    )
    report = harness_plan(definition)

    assert report["status"] == "unverified"
    assert all(value is None for value in report["metrics"].values())
    rendered = str(definition)
    assert "SRC_" not in rendered
    assert "RUN_" not in rendered
