#!/usr/bin/env python3
"""Fail-closed child-delta supply-chain gate for SFU broadcast."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sfu_broadcast_release_common import (  # noqa: E402
    atomic_write_report, build_report, canonical_sha256, content_reasons,
    is_sha256, parse_utc, read_bounded_json, unavailable_report,
    validate_bindings, validate_exceptions,
)

DEFAULT_POLICY = ROOT / "config/security/sfu_broadcast_dependency_policy.json"
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/sfu-broadcast-supply-chain.json"
GATE_ID = "SFB-GATE-012"
REPORT_SCHEMA = "ananta.sfu-broadcast-supply-chain-gate.v1"


_SCAN_ZERO_COUNTERS = ("critical_open", "high_open", "malware_detected", "secrets_detected")
_SANDBOX_PROFILES = ("seccomp", "apparmor")


def _binding_reasons(
    policy: Mapping[str, Any],
    documents: tuple[tuple[str, Mapping[str, Any]], ...],
    required_images: Any,
    reasons: set[str],
) -> dict[str, Any]:
    """Validate the digest bindings of every evidence document; return the SBOM bindings."""

    expected_config = canonical_sha256(policy)
    normalized_bindings: list[dict[str, Any]] = []
    for name, document in documents:
        binding_reasons, bindings = validate_bindings(
            document,
            expected_config_sha256=expected_config,
            required_image_ids=required_images if isinstance(required_images, list) else (),
            lockfile_required=True,
        )
        reasons.update(f"{name}_{reason}" for reason in binding_reasons)
        normalized_bindings.append(bindings)
    bindings = normalized_bindings[0] if normalized_bindings else {}
    if any(item != bindings for item in normalized_bindings[1:]):
        reasons.add("supply_chain_digest_binding_mismatch")
    return bindings


def _sbom_component_reasons(component: Any, reasons: set[str]) -> str | None:
    if not isinstance(component, Mapping):
        reasons.add("supply_chain_component_contract_invalid")
        return None
    count = component.get("package_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        reasons.add("supply_chain_component_package_inventory_empty")
    if component.get("unknown_license_count") != 0:
        reasons.add("supply_chain_unknown_license")
    if component.get("floating_reference") is not False:
        reasons.add("supply_chain_floating_reference")
    if not is_sha256(component.get("deployed_digest")):
        reasons.add("supply_chain_component_digest_invalid")
    return str(component.get("component_id") or "")


def _sbom_reasons(policy: Mapping[str, Any], sbom: Mapping[str, Any], reasons: set[str]) -> set[str]:
    """Validate the child SBOM inventory; return the component ids it covers."""

    if sbom.get("schema") != "ananta.sfu-broadcast-child-sbom.v1":
        reasons.add("supply_chain_sbom_schema_invalid")
    if sbom.get("format") not in set(policy.get("allowed_sbom_formats", [])):
        reasons.add("supply_chain_sbom_format_invalid")
    components = sbom.get("components")
    if not isinstance(components, list):
        reasons.add("supply_chain_component_inventory_invalid")
        components = []
    component_ids: set[str] = set()
    for component in components:
        component_id = _sbom_component_reasons(component, reasons)
        if component_id is not None:
            component_ids.add(component_id)
    if component_ids != set(policy.get("required_components", [])):
        reasons.add("supply_chain_component_scope_mismatch")
    return component_ids


def _parent_sbom_age_reasons(
    policy: Mapping[str, Any],
    parent_sbom: Mapping[str, Any],
    as_of: date,
    reasons: set[str],
) -> None:
    parent_produced = parse_utc(parent_sbom.get("produced_at"))
    as_of_dt = datetime.combine(as_of, datetime.min.time(), tzinfo=UTC)
    if parent_produced is None:
        reasons.add("supply_chain_parent_timestamp_invalid")
    elif as_of_dt - parent_produced > timedelta(days=int(policy.get("maximum_parent_age_days", 0))):
        reasons.add("supply_chain_parent_sbom_stale")


def _child_delta_reasons(sbom: Mapping[str, Any], parent_sbom: Mapping[str, Any], reasons: set[str]) -> Any:
    """Validate the child delta against the parent SBOM; return its changed component ids."""

    delta = sbom.get("child_delta")
    if not isinstance(delta, Mapping):
        reasons.add("supply_chain_child_delta_missing")
        delta = {}
    if delta.get("parent_sbom_sha256") != canonical_sha256(parent_sbom):
        reasons.add("supply_chain_parent_sbom_digest_mismatch")
    changed = delta.get("component_ids")
    if not isinstance(changed, list) or not changed:
        reasons.add("supply_chain_child_delta_empty")
    if not is_sha256(delta.get("delta_sha256")):
        reasons.add("supply_chain_child_delta_digest_invalid")
    return changed


def _provenance_verified(row: Any) -> bool:
    return (
        isinstance(row, Mapping)
        and row.get("signature_verified") is True
        and row.get("builder_verified") is True
        and is_sha256(row.get("subject_sha256"))
    )


def _scan_reasons(scans: Mapping[str, Any], component_ids: set[str], as_of: date, reasons: set[str]) -> None:
    if scans.get("schema") != "ananta.sfu-broadcast-child-security-scan.v1":
        reasons.add("supply_chain_scan_schema_invalid")
    for key in _SCAN_ZERO_COUNTERS:
        if scans.get(key) != 0:
            reasons.add(f"supply_chain_{key}")
    provenance = scans.get("provenance")
    if not isinstance(provenance, list) or {
        row.get("component_id") for row in provenance if isinstance(row, Mapping)
    } != component_ids:
        reasons.add("supply_chain_provenance_scope_mismatch")
        provenance = []
    if not all(_provenance_verified(row) for row in provenance):
        reasons.add("supply_chain_provenance_unverified")
    reasons.update(validate_exceptions(
        scans.get("exceptions"), as_of=as_of, allowed_scopes=component_ids,
    ))


def _container_row_reasons(row: Any, required_controls: set[Any], reasons: set[str]) -> None:
    if not isinstance(row, Mapping):
        reasons.add("supply_chain_container_contract_invalid")
        return
    if any(row.get(control) is not True for control in required_controls):
        reasons.add("supply_chain_container_control_failed")
    if row.get("capabilities_added") not in ([], None):
        reasons.add("supply_chain_container_capability_added")
    for sandbox in _SANDBOX_PROFILES:
        value = row.get(sandbox)
        if not isinstance(value, Mapping) or (
            value.get("available") is True and value.get("enforced") is not True
        ):
            reasons.add(f"supply_chain_container_{sandbox}_invalid")
    if row.get("component_id") in {"sfu", "turn"} and row.get(
        "forbidden_responsibilities"
    ) not in ([], None):
        reasons.add("supply_chain_control_plane_responsibility_leak")


def _container_reasons(
    policy: Mapping[str, Any],
    containers: Mapping[str, Any],
    required_images: Any,
    reasons: set[str],
) -> list[Any]:
    """Validate container hardening controls; return the control rows."""

    if containers.get("schema") != "ananta.sfu-broadcast-container-controls.v1":
        reasons.add("supply_chain_container_schema_invalid")
    controls = containers.get("components")
    if not isinstance(controls, list):
        reasons.add("supply_chain_container_inventory_invalid")
        controls = []
    control_ids = {row.get("component_id") for row in controls if isinstance(row, Mapping)}
    if control_ids != set(required_images):
        reasons.add("supply_chain_container_scope_mismatch")
    required_controls = set(policy.get("required_container_controls", []))
    for row in controls:
        _container_row_reasons(row, required_controls, reasons)
    return controls


def evaluate(
    policy: Mapping[str, Any],
    *,
    sbom: Mapping[str, Any],
    scans: Mapping[str, Any],
    containers: Mapping[str, Any],
    parent_sbom: Mapping[str, Any],
    as_of: date,
    parent_readiness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    reasons: set[str] = set(content_reasons(sbom, scans, containers, parent_sbom))
    required_images = policy.get("required_image_ids", [])
    bindings = _binding_reasons(
        policy,
        (("sbom", sbom), ("scan", scans), ("container", containers)),
        required_images,
        reasons,
    )
    component_ids = _sbom_reasons(policy, sbom, reasons)
    _parent_sbom_age_reasons(policy, parent_sbom, as_of, reasons)
    changed = _child_delta_reasons(sbom, parent_sbom, reasons)
    _scan_reasons(scans, component_ids, as_of, reasons)
    controls = _container_reasons(policy, containers, required_images, reasons)
    return build_report(
        schema=REPORT_SCHEMA,
        gate_id=GATE_ID,
        reasons=reasons,
        bindings=bindings,
        summary={
            "sbom_format": sbom.get("format"),
            "component_count": len(component_ids),
            "child_delta_component_count": len(changed) if isinstance(changed, list) else 0,
            "critical_open": scans.get("critical_open"),
            "high_open": scans.get("high_open"),
            "exception_count": len(scans.get("exceptions", [])) if isinstance(scans.get("exceptions"), list) else 0,
            "container_control_count": len(controls),
        },
        parent=parent_readiness,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--sbom", type=Path)
    parser.add_argument("--scans", type=Path)
    parser.add_argument("--container-evidence", type=Path)
    parser.add_argument("--parent-sbom", type=Path)
    parser.add_argument("--parent-evidence", type=Path)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    policy = read_bounded_json(args.policy)
    parent = read_bounded_json(args.parent_evidence) if args.parent_evidence else None
    paths = (args.sbom, args.scans, args.container_evidence, args.parent_sbom)
    if any(path is None for path in paths):
        report = unavailable_report(
            schema=REPORT_SCHEMA, gate_id=GATE_ID,
            reason="supply_chain_external_evidence_missing", config=policy, parent=parent,
        )
    else:
        report = evaluate(
            policy,
            sbom=read_bounded_json(args.sbom),
            scans=read_bounded_json(args.scans),
            containers=read_bounded_json(args.container_evidence),
            parent_sbom=read_bounded_json(args.parent_sbom),
            as_of=args.as_of,
            parent_readiness=parent,
        )
    atomic_write_report(args.output, report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
