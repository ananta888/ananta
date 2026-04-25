from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.services.capability_registry import CapabilityRegistry  # noqa: E402
from agent.services.domain_policy_loader import DomainPolicyLoader  # noqa: E402
from agent.services.domain_registry import DomainRegistry  # noqa: E402
from agent.services.rag_source_profile_loader import RagSourceProfileLoader  # noqa: E402

RUNTIME_STATUSES = {"runtime_mvp", "runtime_complete"}
INVENTORY_STATUSES = {"planned", "foundation_only", "runtime_mvp", "runtime_complete", "deferred", "blocked"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit generic domain integration contracts and runtime claims.")
    parser.add_argument("--root", default=str(ROOT), help="Repository root path.")
    parser.add_argument(
        "--inventory",
        default="data/domain_runtime_inventory.json",
        help="Domain runtime inventory path.",
    )
    parser.add_argument("--out", help="Write audit report JSON to this path.")
    parser.add_argument("--fail-on-warning", action="store_true", help="Return non-zero when warnings exist.")
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_rel(path: str, *, root: Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def _status_counters(domains: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(entry.get("inventory_status") or "").strip() for entry in domains)
    status_order = ("planned", "foundation_only", "runtime_mvp", "runtime_complete", "deferred", "blocked")
    return {status: counts.get(status, 0) for status in status_order}


def _is_runtime_artifact(path: str) -> bool:
    normalized = str(path).strip().lower()
    if not normalized:
        return False
    if normalized.endswith(".keep"):
        return False
    if normalized.startswith("docs/") or normalized.endswith(".md"):
        return False
    return True


def validate_domain_runtime_inventory(
    *,
    root: Path,
    inventory_payload: dict[str, Any],
    descriptors: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    domains = [entry for entry in list(inventory_payload.get("domains") or []) if isinstance(entry, dict)]
    declared_counters = dict(inventory_payload.get("status_counters") or {})
    computed_counters = _status_counters(domains)
    if declared_counters != computed_counters:
        blockers.append(
            f"inventory status_counters mismatch: declared={declared_counters} computed={computed_counters}"
        )

    descriptor_ids = set(descriptors.keys())
    inventory_ids = {
        str(entry.get("domain_id") or "").strip()
        for entry in domains
        if str(entry.get("domain_id") or "").strip()
    }
    missing_inventory = sorted(descriptor_ids - inventory_ids)
    if missing_inventory:
        blockers.append(f"inventory missing domain entries: {missing_inventory}")

    unknown_inventory = sorted(inventory_ids - descriptor_ids)
    if unknown_inventory:
        blockers.append(f"inventory references unknown domains: {unknown_inventory}")

    for entry in domains:
        domain_id = str(entry.get("domain_id") or "").strip()
        inventory_status = str(entry.get("inventory_status") or "").strip()
        descriptor_path = str(entry.get("descriptor_path") or "").strip()
        runtime_files = [
            str(item).strip() for item in list(entry.get("required_runtime_files") or []) if str(item).strip()
        ]
        smoke_commands = [str(item).strip() for item in list(entry.get("smoke_commands") or []) if str(item).strip()]
        evidence_refs = [
            str(item).strip() for item in list(entry.get("runtime_evidence_refs") or []) if str(item).strip()
        ]

        if inventory_status not in INVENTORY_STATUSES:
            blockers.append(f"{domain_id}: invalid inventory_status={inventory_status!r}")
        if not descriptor_path:
            blockers.append(f"{domain_id}: descriptor_path missing")
        elif not _normalize_rel(descriptor_path, root=root).exists():
            blockers.append(f"{domain_id}: descriptor_path not found: {descriptor_path}")

        descriptor = descriptors.get(domain_id)
        if descriptor is None:
            continue
        runtime_status = str(descriptor.get("runtime_status") or "").strip()
        lifecycle_status = str(descriptor.get("lifecycle_status") or "").strip()
        if runtime_status == "descriptor_only" and inventory_status in RUNTIME_STATUSES:
            blockers.append(
                f"{domain_id}: descriptor runtime_status=descriptor_only cannot claim "
                f"inventory_status={inventory_status}"
            )
        if (
            lifecycle_status in {"planned", "foundation_only", "deferred", "blocked"}
            and inventory_status in RUNTIME_STATUSES
        ):
            blockers.append(
                f"{domain_id}: lifecycle_status={lifecycle_status} cannot claim inventory_status={inventory_status}"
            )

        if inventory_status in RUNTIME_STATUSES:
            if not runtime_files:
                blockers.append(f"{domain_id}: runtime status requires required_runtime_files")
            else:
                missing_runtime_files = [
                    runtime_file
                    for runtime_file in runtime_files
                    if not _normalize_rel(runtime_file, root=root).exists()
                ]
                if missing_runtime_files:
                    blockers.append(f"{domain_id}: missing runtime files: {missing_runtime_files}")
                if not any(_is_runtime_artifact(runtime_file) for runtime_file in runtime_files):
                    blockers.append(f"{domain_id}: runtime claim uses only docs/skeleton files")
            if not smoke_commands:
                blockers.append(f"{domain_id}: runtime status requires smoke_commands")
            if not evidence_refs:
                blockers.append(f"{domain_id}: runtime status requires runtime_evidence_refs")
            else:
                missing_evidence = [
                    evidence_ref
                    for evidence_ref in evidence_refs
                    if not _normalize_rel(evidence_ref, root=root).exists()
                ]
                if missing_evidence:
                    blockers.append(f"{domain_id}: missing runtime evidence refs: {missing_evidence}")
        elif runtime_files or smoke_commands or evidence_refs:
            warnings.append(
                f"{domain_id}: advisory runtime metadata present while inventory_status={inventory_status}"
            )

    return blockers, warnings


def generate_domain_integration_report(*, root: Path, inventory_path: Path) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    descriptors: dict[str, dict[str, Any]] = {}

    domain_registry = DomainRegistry(repository_root=root)
    try:
        descriptors = domain_registry.load()
    except ValueError as exc:
        blockers.append(f"domain descriptor validation failed: {exc}")
        return {
            "schema": "domain_integration_audit_v1",
            "repository_root": str(root),
            "inventory_path": str(inventory_path),
            "descriptor_domains": [],
            "deferred_domains": [],
            "blockers": blockers,
            "warnings": warnings,
            "ok": False,
        }

    known_domains = set(descriptors.keys())

    capability_registry = CapabilityRegistry(repository_root=root)
    try:
        capability_registry.load_from_descriptors(descriptors)
    except ValueError as exc:
        blockers.append(f"capability registry validation failed: {exc}")

    policy_loader = DomainPolicyLoader(capability_registry=capability_registry, repository_root=root)
    for domain_id, descriptor in descriptors.items():
        policy_refs = [str(item).strip() for item in list(descriptor.get("policy_packs") or []) if str(item).strip()]
        policy = policy_loader.load_for_domain(
            domain_id=domain_id,
            policy_refs=policy_refs,
            known_domains=known_domains,
        )
        if str(policy.get("status") or "").strip() != "loaded":
            lifecycle_status = str(descriptor.get("lifecycle_status") or "").strip()
            if lifecycle_status in RUNTIME_STATUSES:
                blockers.append(f"{domain_id}: runtime lifecycle requires loaded policy pack")
            else:
                warnings.append(f"{domain_id}: policy pack degraded ({policy.get('reason')})")

    rag_loader = RagSourceProfileLoader(repository_root=root)
    try:
        rag_profiles_by_domain = rag_loader.load_from_descriptors(descriptors)
    except ValueError as exc:
        blockers.append(f"rag profile validation failed: {exc}")
        rag_profiles_by_domain = {}

    for domain_id, descriptor in descriptors.items():
        if (
            not rag_profiles_by_domain.get(domain_id)
            and str(descriptor.get("lifecycle_status") or "") in RUNTIME_STATUSES
        ):
            blockers.append(f"{domain_id}: runtime lifecycle requires at least one RAG source profile")

    if not inventory_path.exists():
        blockers.append(f"inventory file missing: {inventory_path}")
        inventory_payload: dict[str, Any] = {"domains": [], "status_counters": {}}
    else:
        inventory_payload = _load_json(inventory_path)
        inventory_blockers, inventory_warnings = validate_domain_runtime_inventory(
            root=root,
            inventory_payload=inventory_payload,
            descriptors=descriptors,
        )
        blockers.extend(inventory_blockers)
        warnings.extend(inventory_warnings)

    domains = [entry for entry in list(inventory_payload.get("domains") or []) if isinstance(entry, dict)]
    deferred_domains = sorted(
        str(entry.get("domain_id") or "").strip()
        for entry in domains
        if str(entry.get("inventory_status") or "").strip() == "deferred"
    )

    return {
        "schema": "domain_integration_audit_v1",
        "repository_root": str(root),
        "inventory_path": str(inventory_path),
        "descriptor_domains": sorted(known_domains),
        "deferred_domains": deferred_domains,
        "blockers": blockers,
        "warnings": warnings,
        "ok": not blockers,
    }


def main() -> int:
    args = _parse_args()
    root = Path(args.root).resolve()
    inventory_path = Path(args.inventory)
    if not inventory_path.is_absolute():
        inventory_path = root / inventory_path
    report = generate_domain_integration_report(root=root, inventory_path=inventory_path)

    for blocker in report["blockers"]:
        print(f"[BLOCKER] {blocker}")
    for warning in report["warnings"]:
        print(f"[WARN] {warning}")
    if report["deferred_domains"]:
        print(f"[INFO] deferred domains: {', '.join(report['deferred_domains'])}")

    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = root / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if report["blockers"]:
        return 2
    if args.fail_on_warning and report["warnings"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
