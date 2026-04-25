from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_domain_integrations import (
    generate_domain_integration_report,
    validate_domain_runtime_inventory,
)

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "data" / "domain_runtime_inventory.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_validate_domain_runtime_inventory_detects_counter_drift() -> None:
    inventory = {
        "domains": [
            {
                "domain_id": "example",
                "inventory_status": "foundation_only",
                "descriptor_path": "domains/example/domain.json",
                "required_runtime_files": [],
                "smoke_commands": [],
                "runtime_evidence_refs": [],
            }
        ],
        "status_counters": {"planned": 1},
    }
    descriptors = {"example": {"runtime_status": "descriptor_only", "lifecycle_status": "foundation_only"}}

    blockers, _warnings = validate_domain_runtime_inventory(
        root=ROOT,
        inventory_payload=inventory,
        descriptors=descriptors,
    )

    assert any("status_counters mismatch" in blocker for blocker in blockers)


def test_domain_integration_audit_report_passes_for_repository_inventory() -> None:
    report = generate_domain_integration_report(root=ROOT, inventory_path=INVENTORY_PATH)
    assert report["ok"] is True
    assert report["blockers"] == []


def test_domain_integration_audit_blocks_false_runtime_claim(tmp_path: Path) -> None:
    payload = _load_json(INVENTORY_PATH)
    for entry in payload["domains"]:
        if entry["domain_id"] != "example":
            continue
        entry["inventory_status"] = "runtime_mvp"
        entry["required_runtime_files"] = ["client_surfaces/blender/addon/__missing__.py"]
        entry["smoke_commands"] = ["python scripts/run_blender_smoke_checks.py"]
        entry["runtime_evidence_refs"] = ["artifacts/domain/example/missing.log"]
    payload["status_counters"] = {
        "planned": 0,
        "foundation_only": 1,
        "runtime_mvp": 1,
        "runtime_complete": 0,
        "deferred": 0,
        "blocked": 0,
    }
    inventory_path = tmp_path / "domain_runtime_inventory.json"
    inventory_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    report = generate_domain_integration_report(root=ROOT, inventory_path=inventory_path)

    assert report["ok"] is False
    assert any("descriptor runtime_status=descriptor_only" in blocker for blocker in report["blockers"])
    assert any("missing runtime files" in blocker for blocker in report["blockers"])

