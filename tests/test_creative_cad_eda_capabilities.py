from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "data" / "domain_runtime_inventory.json"
CAPABILITY_PACKS = {
    "blender": ROOT / "domains" / "blender" / "capabilities.json",
    "freecad": ROOT / "domains" / "freecad" / "capabilities.json",
    "kicad": ROOT / "domains" / "kicad" / "capabilities.json",
}
DESCRIPTORS = {
    "blender": ROOT / "domains" / "blender" / "domain.json",
    "freecad": ROOT / "domains" / "freecad" / "domain.json",
    "kicad": ROOT / "domains" / "kicad" / "domain.json",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_capability_ids_follow_shared_domain_resource_action_convention() -> None:
    allowed_actions = {"read", "inspect", "plan", "mutate", "execute"}
    for domain_id, path in CAPABILITY_PACKS.items():
        payload = _load_json(path)
        capabilities = list(payload.get("capabilities") or [])
        assert capabilities
        for capability in capabilities:
            capability_id = str(capability.get("capability_id") or "")
            parts = capability_id.split(".")
            assert parts[0] == domain_id
            assert len(parts) == 3
            assert parts[-1] in allowed_actions


def test_mutating_and_execution_capabilities_are_not_silently_allowed() -> None:
    deny_like = {"deny", "default_deny", "approval_required"}
    for path in CAPABILITY_PACKS.values():
        payload = _load_json(path)
        for capability in list(payload.get("capabilities") or []):
            capability_id = str(capability.get("capability_id") or "")
            action = capability_id.split(".")[-1]
            is_sensitive = bool(capability.get("mutating")) or action == "execute"
            if not is_sensitive:
                continue
            assert str(capability.get("default_policy_state") or "") in deny_like
            assert bool(capability.get("approval_required")) is True


def test_descriptor_status_for_blender_freecad_kicad_does_not_imply_runtime_readiness() -> None:
    inventory = _load_json(INVENTORY_PATH)
    inventory_by_domain = {
        str(entry.get("domain_id") or ""): str(entry.get("inventory_status") or "")
        for entry in list(inventory.get("domains") or [])
    }
    runtime_claim_statuses = {"runtime_mvp", "runtime_complete"}
    for domain_id, descriptor_path in DESCRIPTORS.items():
        descriptor = _load_json(descriptor_path)
        if str(descriptor.get("runtime_status") or "") != "descriptor_only":
            continue
        assert inventory_by_domain.get(domain_id) not in runtime_claim_statuses
