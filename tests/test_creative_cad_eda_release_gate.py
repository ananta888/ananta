from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from scripts.audit_domain_integrations import generate_domain_integration_report

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "data" / "domain_runtime_inventory.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _counter_payload(domains: list[dict]) -> dict[str, int]:
    counts = Counter(str(item.get("inventory_status") or "") for item in domains)
    return {
        "planned": counts.get("planned", 0),
        "foundation_only": counts.get("foundation_only", 0),
        "runtime_mvp": counts.get("runtime_mvp", 0),
        "runtime_complete": counts.get("runtime_complete", 0),
        "deferred": counts.get("deferred", 0),
        "blocked": counts.get("blocked", 0),
    }


def _blender_runtime_evidence_missing(entry: dict) -> list[str]:
    status = str(entry.get("inventory_status") or "")
    if status not in {"runtime_mvp", "runtime_complete"}:
        return []
    refs = [str(item) for item in list(entry.get("runtime_evidence_refs") or [])]
    required_tokens = [
        "blender-smoke-report",
        "blender-context-smoke",
        "blender-policy-smoke",
    ]
    return [token for token in required_tokens if not any(token in ref for ref in refs)]


def test_creative_cad_eda_release_gate_baseline_report_is_not_blocked() -> None:
    report = generate_domain_integration_report(root=ROOT, inventory_path=INVENTORY_PATH)
    assert report["ok"] is True


def test_release_gate_blocks_freecad_or_kicad_runtime_claim_without_smoke_evidence(tmp_path: Path) -> None:
    payload = _load_json(INVENTORY_PATH)
    for entry in list(payload.get("domains") or []):
        if str(entry.get("domain_id") or "") == "freecad":
            entry["inventory_status"] = "runtime_mvp"
            entry["required_runtime_files"] = []
            entry["smoke_commands"] = []
            entry["runtime_evidence_refs"] = []
            break
    payload["status_counters"] = _counter_payload(list(payload.get("domains") or []))
    inventory_path = tmp_path / "domain_runtime_inventory.json"
    inventory_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    report = generate_domain_integration_report(root=ROOT, inventory_path=inventory_path)

    assert report["ok"] is False
    assert any("freecad" in blocker and "runtime status requires" in blocker for blocker in report["blockers"])


def test_default_deny_capabilities_remain_denied_without_policy_override() -> None:
    for domain_id in ("freecad", "kicad"):
        payload = _load_json(ROOT / "domains" / domain_id / "capabilities.json")
        for capability in list(payload.get("capabilities") or []):
            capability_id = str(capability.get("capability_id") or "")
            action = capability_id.split(".")[-1]
            if action not in {"mutate", "execute"}:
                continue
            assert str(capability.get("default_policy_state") or "") in {"deny", "default_deny", "approval_required"}


def test_blender_runtime_claim_requires_addon_context_and_policy_smoke_evidence() -> None:
    payload = _load_json(INVENTORY_PATH)
    blender_entry = next(
        entry for entry in list(payload.get("domains") or []) if str(entry.get("domain_id") or "") == "blender"
    )
    assert _blender_runtime_evidence_missing(blender_entry) == []

    synthetic_runtime_claim = dict(blender_entry)
    synthetic_runtime_claim["inventory_status"] = "runtime_mvp"
    synthetic_runtime_claim["runtime_evidence_refs"] = [
        "ci-artifacts/domain-runtime/blender-smoke-report.json",
    ]
    missing = _blender_runtime_evidence_missing(synthetic_runtime_claim)
    assert missing == ["blender-context-smoke", "blender-policy-smoke"]
