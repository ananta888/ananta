from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from agent.services.domain_registry import DomainRegistry

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "data" / "domain_runtime_inventory.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_domain_runtime_inventory_status_counters_are_consistent() -> None:
    payload = _load_json(INVENTORY_PATH)
    domains = list(payload.get("domains") or [])
    declared = dict(payload.get("status_counters") or {})
    computed_counts = Counter(str(entry.get("inventory_status") or "") for entry in domains)
    computed = {
        "planned": computed_counts.get("planned", 0),
        "foundation_only": computed_counts.get("foundation_only", 0),
        "runtime_mvp": computed_counts.get("runtime_mvp", 0),
        "runtime_complete": computed_counts.get("runtime_complete", 0),
        "deferred": computed_counts.get("deferred", 0),
        "blocked": computed_counts.get("blocked", 0),
    }
    assert declared == computed


def test_domain_runtime_inventory_domain_ids_and_paths_match_descriptors() -> None:
    payload = _load_json(INVENTORY_PATH)
    inventory_domains = list(payload.get("domains") or [])
    inventory_ids = {str(entry.get("domain_id") or "") for entry in inventory_domains}
    descriptors = DomainRegistry(repository_root=ROOT).load()
    descriptor_ids = set(descriptors.keys())
    assert inventory_ids == descriptor_ids
    for entry in inventory_domains:
        descriptor_path = ROOT / str(entry.get("descriptor_path") or "")
        assert descriptor_path.exists()


def test_descriptor_only_domains_are_not_claimed_as_runtime_complete() -> None:
    payload = _load_json(INVENTORY_PATH)
    descriptors = DomainRegistry(repository_root=ROOT).load()
    runtime_claim_statuses = {"runtime_mvp", "runtime_complete"}
    for entry in list(payload.get("domains") or []):
        domain_id = str(entry.get("domain_id") or "")
        inventory_status = str(entry.get("inventory_status") or "")
        descriptor = descriptors[domain_id]
        if str(descriptor.get("runtime_status") or "") == "descriptor_only":
            assert inventory_status not in runtime_claim_statuses

