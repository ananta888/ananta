#!/usr/bin/env python3
"""Fail-closed SBOM, vulnerability and container-hardening gate."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.services.semantic_media_program_evidence import (  # noqa: E402
    GateEvidence,
    ProgramEvidenceError,
    canonical_sha256,
    source_hash,
    unavailable_evidence,
    write_report,
)
from scripts.build_semantic_media_containers import (  # noqa: E402
    SFU_DIGEST,
    TURN_REFERENCE,
    ContainerBuildError,
    validate_build_manifest,
)
from scripts.generate_semantic_media_supply_chain_reports import SOURCE_BINDINGS  # noqa: E402

ROOT = _PROJECT_ROOT
COMPONENTS = frozenset({"hub", "frontend", "sfu", "turn", "reconciliation", "training"})
PACKAGE_FIELDS = frozenset({"name", "version", "license", "origin"})
EXCEPTION_FIELDS = frozenset({"finding_id", "owner", "rationale", "expires_on"})
FINDING_FIELDS = frozenset({"finding_id", "severity", "package", "version", "fix_state"})
SEVERITIES = frozenset({"critical", "high", "medium", "low", "negligible", "unknown"})


def evaluate(  # noqa: C901 - one contract evaluation keeps evidence accounting atomic
    sbom: Mapping[str, Any],
    scanner: Mapping[str, Any],
    *,
    build_manifest: Mapping[str, Any],
    as_of: dt.date,
) -> GateEvidence:
    reasons: list[str] = []
    try:
        built_images = validate_build_manifest(build_manifest)
    except ContainerBuildError as exc:
        raise ProgramEvidenceError("semantic_media_build_manifest_invalid_or_stale") from exc
    build_manifest_digest = canonical_sha256(build_manifest)
    policy_digest = source_hash(ROOT, SOURCE_BINDINGS)
    report_fields = {
        "schema",
        "source_sha256",
        "policy_sha256",
        "build_manifest_sha256",
    }
    if (
        set(sbom) != {*report_fields, "components"}
        or sbom.get("schema") != "ananta.semantic-media-sbom.v2"
    ):
        raise ProgramEvidenceError("semantic_media_sbom_contract_invalid")
    _digest(sbom["source_sha256"])
    _digest(sbom["policy_sha256"])
    _digest(sbom["build_manifest_sha256"])
    components = sbom.get("components")
    if not isinstance(components, list):
        raise ProgramEvidenceError("semantic_media_sbom_components_invalid")
    names: set[str] = set()
    package_count = 0
    unknown_license_count = 0
    composite_license_count = 0
    image_digests: dict[str, str] = {}
    for component in components:
        if not isinstance(component, Mapping) or set(component) != {"name", "image_digest", "packages"}:
            raise ProgramEvidenceError("semantic_media_sbom_component_invalid")
        name = str(component["name"])
        names.add(name)
        _digest(component["image_digest"])
        image_digests[name] = str(component["image_digest"])
        packages = component["packages"]
        if not isinstance(packages, list) or not packages:
            reasons.append("semantic_media_sbom_package_inventory_missing")
            continue
        for package in packages:
            if not isinstance(package, Mapping) or set(package) != PACKAGE_FIELDS or not all(package.values()):
                reasons.append("semantic_media_sbom_package_invalid")
                continue
            for value in package.values():
                _safe_text(value)
            if str(package["license"]).casefold() in {"unknown", "noassertion", "none"}:
                unknown_license_count += 1
            if str(package["license"]).startswith("COMPOSITE-SPDX-SHA256:"):
                composite_license_count += 1
            package_count += 1
    if names != COMPONENTS:
        reasons.append("semantic_media_sbom_component_coverage_missing")
    if sbom.get("source_sha256") != build_manifest.get("source_sha256"):
        reasons.append("semantic_media_sbom_source_stale")
    if sbom.get("policy_sha256") != policy_digest:
        reasons.append("semantic_media_sbom_policy_stale")
    if sbom.get("build_manifest_sha256") != build_manifest_digest:
        reasons.append("semantic_media_sbom_build_manifest_mismatch")
    if any(image_digests.get(component) != built_images[component][1] for component in built_images):
        reasons.append("semantic_media_sbom_build_image_mismatch")

    if (
        set(scanner) != {*report_fields, "images"}
        or scanner.get("schema") != "ananta.semantic-media-vulnerability-report.v2"
    ):
        raise ProgramEvidenceError("semantic_media_scanner_contract_invalid")
    if scanner.get("source_sha256") != sbom.get("source_sha256"):
        reasons.append("semantic_media_scanner_source_mismatch")
    if scanner.get("policy_sha256") != sbom.get("policy_sha256"):
        reasons.append("semantic_media_scanner_policy_mismatch")
    if scanner.get("build_manifest_sha256") != sbom.get("build_manifest_sha256"):
        reasons.append("semantic_media_scanner_build_manifest_mismatch")
    images = scanner.get("images")
    scanned: set[str] = set()
    critical_count = 0
    unaccepted_high = 0
    if not isinstance(images, list):
        raise ProgramEvidenceError("semantic_media_scanner_images_invalid")
    for image in images:
        if not isinstance(image, Mapping) or set(image) != {
            "component",
            "image_digest",
            "critical",
            "high",
            "findings",
            "exceptions",
        }:
            raise ProgramEvidenceError("semantic_media_scanner_image_invalid")
        component = str(image["component"])
        scanned.add(component)
        if image_digests.get(component) != image["image_digest"]:
            reasons.append("semantic_media_scanner_image_mismatch")
        critical = _count(image["critical"])
        high = _count(image["high"])
        findings = image["findings"]
        if not isinstance(findings, list):
            raise ProgramEvidenceError("semantic_media_scanner_findings_invalid")
        normalized_findings: list[tuple[str, str]] = []
        for finding in findings:
            if not isinstance(finding, Mapping) or set(finding) != FINDING_FIELDS:
                raise ProgramEvidenceError("semantic_media_scanner_finding_invalid")
            for value in finding.values():
                _safe_text(value)
            severity = str(finding["severity"]).casefold()
            if severity not in SEVERITIES:
                raise ProgramEvidenceError("semantic_media_scanner_finding_invalid")
            normalized_findings.append((str(finding["finding_id"]), severity))
        measured_critical = sum(severity == "critical" for _, severity in normalized_findings)
        measured_high = sum(severity == "high" for _, severity in normalized_findings)
        if critical != measured_critical or high != measured_high:
            reasons.append("semantic_media_scanner_count_mismatch")
        critical_count += measured_critical
        exceptions = image["exceptions"]
        if not isinstance(exceptions, list):
            raise ProgramEvidenceError("semantic_media_scanner_exception_invalid")
        valid_exception_ids: set[str] = set()
        high_finding_ids = {finding_id for finding_id, severity in normalized_findings if severity == "high"}
        for exception in exceptions:
            if not isinstance(exception, Mapping) or set(exception) != EXCEPTION_FIELDS:
                raise ProgramEvidenceError("semantic_media_scanner_exception_invalid")
            for field in ("finding_id", "owner", "rationale"):
                _safe_text(exception[field])
            try:
                expiry = dt.date.fromisoformat(str(exception["expires_on"]))
            except ValueError as exc:
                raise ProgramEvidenceError("semantic_media_scanner_exception_invalid") from exc
            finding_id = str(exception["finding_id"])
            if finding_id not in high_finding_ids:
                reasons.append("semantic_media_scanner_exception_orphaned")
            elif expiry >= as_of:
                valid_exception_ids.add(finding_id)
        unaccepted_high += sum(
            severity == "high" and finding_id not in valid_exception_ids for finding_id, severity in normalized_findings
        )
    if scanned != COMPONENTS:
        reasons.append("semantic_media_scanner_coverage_missing")
    if critical_count:
        reasons.append("semantic_media_critical_vulnerability")
    if unaccepted_high:
        reasons.append("semantic_media_high_vulnerability_unaccepted")
    if unknown_license_count:
        reasons.append("semantic_media_license_unresolved")
    if composite_license_count:
        reasons.append("semantic_media_composite_license_review_required")

    static = static_hardening_checks()
    reasons.extend(reason for reason, passed in static.items() if not passed)
    source_digest = source_hash(
        ROOT,
        (
            "agent/services/semantic_media_program_evidence.py",
            "scripts/run_semantic_media_supply_chain_gate.py",
            "docs/legal/semantic-media-dependency-review.md",
        ),
    )
    config_digest = canonical_sha256(
        {"sbom": sbom, "scanner": scanner, "build_manifest": build_manifest, "as_of": as_of.isoformat()}
    )
    return GateEvidence(
        gate_id="ASMP-QA-010",
        status="passed" if not reasons else "failed",
        reason_codes=tuple(sorted(set(reasons))),
        source_sha256=source_digest,
        config_sha256=config_digest,
        measurements={
            "component_count": len(names),
            "package_count": package_count,
            "unknown_license_count": unknown_license_count,
            "composite_license_count": composite_license_count,
            "critical_count": critical_count,
            "unaccepted_high_count": unaccepted_high,
            "hardening_check_count": len(static),
        },
    )


def static_hardening_checks() -> dict[str, bool]:
    training_compose = (ROOT / "docker/compose-next/compose.speech-training.yml").read_text(encoding="utf-8")
    reconciliation_compose = (ROOT / "docker/compose-next/compose.speech-reconciliation.yml").read_text(
        encoding="utf-8"
    )
    sfu_compose = (ROOT / "docker-compose.semantic-media.yml").read_text(encoding="utf-8")
    training_document = yaml.safe_load(training_compose)
    reconciliation_document = yaml.safe_load(reconciliation_compose)
    semantic_document = yaml.safe_load(sfu_compose)
    training_service = training_document["services"]["speech-training-worker"]
    reconciliation_services = (
        reconciliation_document["services"]["speech-reconciliation-worker-cpu"],
        reconciliation_document["services"]["speech-reconciliation-worker-nvidia"],
    )
    semantic_services = semantic_document["services"]
    sfu_service = semantic_services["semantic-media-sfu"]
    turn_service = semantic_services["semantic-media-turn-gate"]
    edge_services = (sfu_service, turn_service)
    worker_services = (training_service, *reconciliation_services)
    hardened_services = (*worker_services, *edge_services)
    training_worker = (ROOT / "docker/compose-next/Dockerfile.speech-training-worker").read_text(encoding="utf-8")
    reconciliation_worker = (ROOT / "docker/compose-next/Dockerfile.speech-reconciliation-worker").read_text(
        encoding="utf-8"
    )
    hub_worker = (ROOT / "docker/compose-next/Dockerfile.quickstart-no-ollama").read_text(encoding="utf-8")
    frontend_worker = (ROOT / "frontend-angular/Dockerfile").read_text(encoding="utf-8")
    frontend_lock = json.loads((ROOT / "frontend-angular/package-lock.json").read_text(encoding="utf-8"))
    livekit = frontend_lock.get("packages", {}).get("node_modules/livekit-client", {})
    worker_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for base in (
            ROOT / "worker/semantic_media",
            ROOT / "worker/speech_reconciliation",
            ROOT / "worker/speech_training",
            ROOT / "worker/runtime/speech_reconciliation_app.py",
            ROOT / "worker/runtime/speech_training_app.py",
        )
        for path in ((base,) if base.is_file() else sorted(base.rglob("*.py")))
    )
    return {
        "container_non_root_missing": all(_non_root(service) for service in hardened_services)
        and "USER 10007:10007" in training_worker
        and "USER 10008:10008" in reconciliation_worker,
        "container_read_only_missing": all(service.get("read_only") is True for service in hardened_services),
        "container_capabilities_missing": all(service.get("cap_drop") == ["ALL"] for service in hardened_services)
        and all(not service.get("cap_add") for service in (*worker_services, sfu_service))
        and turn_service.get("cap_add") == ["NET_BIND_SERVICE"],
        "container_no_new_privileges_missing": all(
            "no-new-privileges:true" in service.get("security_opt", []) for service in hardened_services
        ),
        "container_resources_missing": all(_bounded_resources(service) for service in hardened_services),
        "container_internal_network_missing": (
            training_document["networks"]["speech-training-control"].get("internal") is True
            and reconciliation_document["networks"]["speech-reconciliation-control"].get("internal") is True
            and _network_names(training_service) == {"speech-training-control"}
            and all(
                _network_names(service) == {"speech-reconciliation-control"}
                for service in reconciliation_services
            )
            and all(_network_names(service) == {"semantic-media-edge"} for service in edge_services)
            and _turn_is_loopback_only(turn_service)
        ),
        "container_secret_boundary_missing": _secret_boundaries_present(
            training_service=training_service,
            reconciliation_services=reconciliation_services,
            reconciliation_document=reconciliation_document,
            sfu_service=sfu_service,
            turn_service=turn_service,
        ),
        "container_external_digest_pin_missing": (
            str(sfu_service.get("image", "")).endswith(f"@sha256:{SFU_DIGEST}")
            and str(sfu_service.get("image", "")).startswith("livekit/livekit-server@")
            and turn_service.get("image") == TURN_REFERENCE
        ),
        "container_base_digest_pin_missing": all(
            _dockerfile_from_lines_digest_pinned(content)
            for content in (training_worker, reconciliation_worker, hub_worker, frontend_worker)
        ),
        "frontend_dependency_pin_missing": bool(re.fullmatch(r"\d+\.\d+\.\d+", str(livekit.get("version", "")))),
        "hub_dependency_pin_missing": (
            (ROOT / "services/evolver_bridge/package-lock.json").is_file()
            and "cd services/evolver_bridge && npm ci --no-audit --no-fund" in hub_worker
        ),
        "worker_task_policy_boundary_missing": not any(
            marker in worker_sources for marker in ("agent.services", "task_queue_service", "semantic_compute_policy")
        )
        and all("COPY agent" not in content for content in (training_worker, reconciliation_worker))
        and all("build" not in service for service in edge_services),
        "model_manifest_binding_missing": _model_manifest_declared(),
        "license_review_missing": all(
            (ROOT / path).is_file() and (ROOT / path).stat().st_size > 500
            for path in (
                "docs/legal/semantic-media-dependency-review.md",
                "docs/legal/speech-model-license-review.md",
            )
        ),
        "vulnerability_exception_registry_missing": (
            ROOT / "config/semantic-media-vulnerability-exceptions.v1.json"
        ).is_file(),
    }


def _non_root(service: Mapping[str, Any]) -> bool:
    return str(service.get("user", "")).split(":", 1)[0] not in {"", "0", "root"}


def _bounded_resources(service: Mapping[str, Any]) -> bool:
    limits = service.get("deploy", {}).get("resources", {}).get("limits", {})
    return (
        bool(service.get("cpus") or limits.get("cpus"))
        and bool(service.get("mem_limit") or limits.get("memory"))
        and bool(service.get("pids_limit") or limits.get("pids"))
    )


def _network_names(service: Mapping[str, Any]) -> set[str]:
    networks = service.get("networks", {})
    if isinstance(networks, Mapping):
        return {str(name) for name in networks}
    if isinstance(networks, list):
        return {str(name) for name in networks}
    return set()


def _turn_is_loopback_only(service: Mapping[str, Any]) -> bool:
    ports = service.get("ports")
    profiles = service.get("profiles")
    return (
        isinstance(ports, list)
        and len(ports) == 1
        and str(ports[0]).startswith("127.0.0.1:")
        and profiles == ["semantic-media-turn-gate"]
    )


def _secret_boundaries_present(
    *,
    training_service: Mapping[str, Any],
    reconciliation_services: tuple[Mapping[str, Any], ...],
    reconciliation_document: Mapping[str, Any],
    sfu_service: Mapping[str, Any],
    turn_service: Mapping[str, Any],
) -> bool:
    training_environment = training_service.get("environment", {})
    reconciliation_environments = tuple(service.get("environment", {}) for service in reconciliation_services)
    sfu_keys = str(sfu_service.get("environment", {}).get("LIVEKIT_KEYS", ""))
    turn_command = " ".join(str(value) for value in turn_service.get("command", ()))
    return (
        "${ANANTA_SPEECH_TRAINING_INTERNAL_TOKEN:?" in str(training_environment.get("ANANTA_SPEECH_TRAINING_TOKEN", ""))
        and "${ANANTA_SPEECH_TRAINING_CALLBACK_TOKEN:?" in str(
            training_environment.get("ANANTA_SPEECH_TRAINING_CALLBACK_TOKEN", "")
        )
        and all(
            "${ANANTA_SPEECH_RECONCILIATION_INTERNAL_TOKEN:?" in str(
                environment.get("ANANTA_SPEECH_RECONCILIATION_TOKEN", "")
            )
            for environment in reconciliation_environments
        )
        and all(service.get("secrets") for service in reconciliation_services)
        and "speech-reconciliation-keyring" in reconciliation_document.get("secrets", {})
        and "${ANANTA_SEMANTIC_MEDIA_SFU_API_KEY" in sfu_keys
        and "${ANANTA_SEMANTIC_MEDIA_SFU_API_SECRET" in sfu_keys
        and "${ANANTA_SEMANTIC_MEDIA_TURN_GATE_USER" in turn_command
        and "${ANANTA_SEMANTIC_MEDIA_TURN_GATE_PASSWORD" in turn_command
        and all(
            "env_file" not in service
            for service in (*reconciliation_services, training_service, sfu_service, turn_service)
        )
    )


def _model_manifest_declared() -> bool:
    manifest = json.loads((ROOT / "models/voice/manifests/voice-models.json").read_text(encoding="utf-8"))
    models = manifest.get("models")
    if manifest.get("schema_version") != "ananta.voice-model-catalog.v1" or not isinstance(models, list) or not models:
        return False
    identifiers: set[str] = set()
    for model in models:
        if not isinstance(model, Mapping):
            return False
        identifier = str(model.get("id", ""))
        revision = str(model.get("revision", ""))
        license_name = str(model.get("license", ""))
        files = model.get("files")
        if (
            not identifier
            or identifier in identifiers
            or not revision
            or revision.casefold() in {"latest", "main", "master"}
            or not license_name
            or license_name.casefold() in {"unknown", "noassertion", "none"}
            or not isinstance(files, list)
            or not files
        ):
            return False
        identifiers.add(identifier)
        for file in files:
            if not isinstance(file, Mapping) or set(file) != {"path", "sha256"}:
                return False
            path = Path(str(file["path"]))
            digest = str(file["sha256"])
            if path.is_absolute() or ".." in path.parts or not re.fullmatch(r"[a-f0-9]{64}", digest):
                return False
    return "whisper-cpp-v1.8.6-ggml-small" in identifiers


def _dockerfile_from_lines_digest_pinned(content: str) -> bool:
    lines = [line.strip() for line in content.splitlines() if line.strip().upper().startswith("FROM ")]
    aliases: set[str] = set()
    for line in lines:
        parts = line.split()
        source = parts[1] if len(parts) >= 2 else ""
        if "@sha256:" not in source and source not in aliases:
            return False
        if len(parts) >= 4 and parts[-2].upper() == "AS":
            aliases.add(parts[-1])
    return bool(lines)


def unavailable() -> GateEvidence:
    source_digest = source_hash(
        ROOT,
        (
            "agent/services/semantic_media_program_evidence.py",
            "scripts/run_semantic_media_supply_chain_gate.py",
            "docs/legal/semantic-media-dependency-review.md",
        ),
    )
    return unavailable_evidence(
        "ASMP-QA-010",
        source_sha256=source_digest,
        config_sha256=canonical_sha256({"required_components": sorted(COMPONENTS)}),
        reason_code="sbom_or_vulnerability_scan_unavailable",
    )


def _digest(value: Any) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ProgramEvidenceError("semantic_media_supply_digest_invalid")


def _safe_text(value: Any) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or value.startswith(("/", "file:", "~"))
        or "\\" in value
    ):
        raise ProgramEvidenceError("semantic_media_supply_value_invalid")


def _count(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProgramEvidenceError("semantic_media_supply_count_invalid")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sbom-report", type=Path)
    parser.add_argument("--scanner-report", type=Path)
    parser.add_argument("--build-manifest", type=Path)
    parser.add_argument("--as-of", type=dt.date.fromisoformat, default=dt.date.today())
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/test-gates/semantic-media-sbom.json")
    args = parser.parse_args()
    try:
        if args.sbom_report is None or args.scanner_report is None or args.build_manifest is None:
            evidence = unavailable()
        else:
            evidence = evaluate(
                json.loads(args.sbom_report.read_text(encoding="utf-8")),
                json.loads(args.scanner_report.read_text(encoding="utf-8")),
                build_manifest=json.loads(args.build_manifest.read_text(encoding="utf-8")),
                as_of=args.as_of,
            )
    except (OSError, json.JSONDecodeError, ProgramEvidenceError) as exc:
        print(
            json.dumps(
                {"status": "failed", "reason_code": getattr(exc, "reason_code", "supply_chain_input_invalid")},
                sort_keys=True,
            )
        )
        return 1
    write_report(args.output, evidence)
    print(json.dumps(evidence.as_document(), sort_keys=True))
    return 0 if evidence.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
