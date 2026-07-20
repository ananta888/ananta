#!/usr/bin/env python3
"""Generate normalized, content-free SBOM and vulnerability inputs.

Syft and Grype stay external tools.  This adapter binds their output to exact
local image IDs and emits only package/version/license/origin plus bounded
finding metadata; scanner timestamps, host paths and layer locations are
deliberately discarded.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.services.semantic_media_program_evidence import (  # noqa: E402
    assert_content_free,
    canonical_sha256,
    source_hash,
)
from scripts.build_semantic_media_containers import (  # noqa: E402
    SFU_REFERENCE,
    TURN_REFERENCE,
    ContainerBuildError,
    validate_build_manifest,
)

COMPONENT_SOURCES = {
    "hub": "ananta-quickstart-no-ollama:local",
    "frontend": "ananta-frontend-tests:local",
    "sfu": SFU_REFERENCE,
    "turn": TURN_REFERENCE,
    "reconciliation": "ananta-speech-reconciliation-worker:local-cpu",
    "training": "ananta-speech-training-worker:local",
}
SOURCE_BINDINGS = (
    "config/semantic-media-vulnerability-exceptions.v1.json",
    "requirements.lock",
    "frontend-angular/package-lock.json",
    "frontend-angular/Dockerfile",
    "models/voice/manifests/voice-models.json",
    "scripts/build_semantic_media_containers.py",
    "scripts/generate_semantic_media_supply_chain_reports.py",
    "docker-compose.semantic-media.yml",
    "docker/compose-next/Dockerfile.quickstart-no-ollama",
    "docker/compose-next/Dockerfile.speech-reconciliation-worker",
    "docker/compose-next/Dockerfile.speech-training-worker",
    "docker/compose-next/compose.speech-reconciliation.yml",
    "docker/compose-next/compose.speech-training.yml",
    "docker/compose-next/requirements.runtime-http.txt",
    "docker/compose-next/requirements.speech-reconciliation.txt",
    "docker/compose-next/requirements.voice-cpu.txt",
    "services/evolver_bridge/package-lock.json",
)
_SAFE = re.compile(r"^[^\x00-\x1f\\]{1,256}$")


class SupplyReportError(RuntimeError):
    pass


def generate(
    sources: Mapping[str, str],
    *,
    build_manifest: Mapping[str, Any],
    syft: str,
    grype: str,
    exceptions: Mapping[str, Sequence[Mapping[str, str]]],
    timeout_seconds: int = 900,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if set(sources) != set(COMPONENT_SOURCES):
        raise SupplyReportError("supply_component_coverage_invalid")
    try:
        built_images = validate_build_manifest(build_manifest)
    except ContainerBuildError as exc:
        raise SupplyReportError(str(exc)) from exc
    if any(sources[component] != built_images[component][0] for component in sources):
        raise SupplyReportError("supply_source_not_bound_to_build_manifest")
    source_digest = str(build_manifest["source_sha256"])
    policy_digest = source_hash(ROOT, SOURCE_BINDINGS)
    build_manifest_digest = canonical_sha256(build_manifest)
    sbom_components: list[dict[str, Any]] = []
    scanner_images: list[dict[str, Any]] = []
    for component in sorted(sources):
        source = _source(sources[component])
        image_digest = _image_digest(source, timeout_seconds)
        if image_digest != built_images[component][1]:
            raise SupplyReportError("supply_image_not_bound_to_build_manifest")
        syft_json = _run_json(
            [syft, "scan", f"docker:{source}", "--output", "syft-json"],
            timeout_seconds,
            "syft_scan_failed",
        )
        grype_json = _run_json(
            [grype, f"docker:{source}", "--output", "json", "--only-fixed=false"],
            timeout_seconds,
            "grype_scan_failed",
        )
        packages = normalize_packages(syft_json)
        findings = normalize_findings(grype_json)
        configured = normalize_exceptions(exceptions.get(component, ()), findings)
        sbom_components.append(
            {"name": component, "image_digest": image_digest, "packages": packages}
        )
        scanner_images.append(
            {
                "component": component,
                "image_digest": image_digest,
                "critical": sum(row["severity"] == "critical" for row in findings),
                "high": sum(row["severity"] == "high" for row in findings),
                "findings": findings,
                "exceptions": configured,
            }
        )
    sbom = {
        "schema": "ananta.semantic-media-sbom.v2",
        "source_sha256": source_digest,
        "policy_sha256": policy_digest,
        "build_manifest_sha256": build_manifest_digest,
        "components": sbom_components,
    }
    scanner = {
        "schema": "ananta.semantic-media-vulnerability-report.v2",
        "source_sha256": source_digest,
        "policy_sha256": policy_digest,
        "build_manifest_sha256": build_manifest_digest,
        "images": scanner_images,
    }
    assert_content_free(sbom)
    assert_content_free(scanner)
    return sbom, scanner


def normalize_packages(document: Mapping[str, Any]) -> list[dict[str, str]]:
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list):
        raise SupplyReportError("syft_artifacts_invalid")
    rows: set[tuple[str, str, str, str]] = set()
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            continue
        name = _text(artifact.get("name"), "unnamed")
        version = _text(artifact.get("version"), "unversioned")
        license_name = _license(artifact.get("licenses"))
        origin = _text(artifact.get("type") or artifact.get("foundBy"), "unclassified")
        rows.add((name, version, license_name, origin))
    if not rows:
        raise SupplyReportError("syft_package_inventory_empty")
    return [
        {"name": name, "version": version, "license": license_name, "origin": origin}
        for name, version, license_name, origin in sorted(rows)
    ]


def normalize_findings(document: Mapping[str, Any]) -> list[dict[str, str]]:
    matches = document.get("matches")
    if not isinstance(matches, list):
        raise SupplyReportError("grype_matches_invalid")
    rows: set[tuple[str, str, str, str, str]] = set()
    for match in matches:
        if not isinstance(match, Mapping):
            continue
        vulnerability = match.get("vulnerability")
        artifact = match.get("artifact")
        if not isinstance(vulnerability, Mapping) or not isinstance(artifact, Mapping):
            continue
        severity = str(vulnerability.get("severity") or "unknown").casefold()
        if severity not in {"critical", "high", "medium", "low", "negligible", "unknown"}:
            severity = "unknown"
        fix = vulnerability.get("fix")
        fix_state = str(fix.get("state") or "unknown").casefold() if isinstance(fix, Mapping) else "unknown"
        rows.add(
            (
                _text(vulnerability.get("id"), "unknown-finding"),
                severity,
                _text(artifact.get("name"), "unnamed"),
                _text(artifact.get("version"), "unversioned"),
                _text(fix_state, "unknown"),
            )
        )
    return [
        {"finding_id": finding, "severity": severity, "package": package, "version": version, "fix_state": fix}
        for finding, severity, package, version, fix in sorted(rows)
    ]


def normalize_exceptions(
    configured: Sequence[Mapping[str, str]],
    findings: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    high_ids = {row["finding_id"] for row in findings if row["severity"] == "high"}
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in configured:
        if not isinstance(item, Mapping) or set(item) != {"finding_id", "owner", "rationale", "expires_on"}:
            raise SupplyReportError("vulnerability_exception_contract_invalid")
        normalized = {key: _text(item[key]) for key in ("finding_id", "owner", "rationale", "expires_on")}
        if normalized["finding_id"] not in high_ids:
            raise SupplyReportError("vulnerability_exception_finding_missing")
        if normalized["finding_id"] in seen:
            raise SupplyReportError("vulnerability_exception_duplicate")
        try:
            dt.date.fromisoformat(normalized["expires_on"])
        except ValueError as exc:
            raise SupplyReportError("vulnerability_exception_expiry_invalid") from exc
        seen.add(normalized["finding_id"])
        result.append(normalized)
    return sorted(result, key=lambda row: row["finding_id"])


def _run_json(command: list[str], timeout_seconds: int, reason: str) -> dict[str, Any]:
    executable = shutil.which(command[0]) if os.sep not in command[0] else command[0]
    if not executable or not Path(executable).is_file():
        raise SupplyReportError(f"{reason}:tool_missing")
    command[0] = executable
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        raise SupplyReportError(f"{reason}:exit_{completed.returncode}")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SupplyReportError(f"{reason}:json_invalid") from exc
    if not isinstance(value, dict):
        raise SupplyReportError(f"{reason}:document_invalid")
    return value


def _image_digest(source: str, timeout_seconds: int) -> str:
    completed = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", source],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    rendered = completed.stdout.strip().removeprefix("sha256:")
    if completed.returncode != 0 or not re.fullmatch(r"[a-f0-9]{64}", rendered):
        raise SupplyReportError("container_image_digest_unavailable")
    return rendered


def _license(value: Any) -> str:
    candidates: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                candidates.append(item)
            elif isinstance(item, Mapping):
                raw = item.get("spdxExpression") or item.get("value")
                if raw:
                    candidates.append(str(raw))
    if not candidates:
        return "NOASSERTION"
    rendered = " AND ".join(sorted(set(_text(item) for item in candidates)))
    if len(rendered) <= 256:
        return rendered
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    return f"COMPOSITE-SPDX-SHA256:{digest}"


def _text(value: Any, fallback: str | None = None) -> str:
    rendered = str(value or fallback or "").strip()
    if not _SAFE.fullmatch(rendered) or rendered.startswith(("/", "file:", "~")):
        if fallback is not None:
            return fallback
        raise SupplyReportError("supply_text_invalid")
    return rendered


def _source(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@:-]{0,255}", value):
        raise SupplyReportError("container_source_invalid")
    return value


def _load_exceptions(path: Path | None) -> dict[str, list[dict[str, str]]]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"schema", "components"}:
        raise SupplyReportError("vulnerability_exceptions_invalid")
    if value["schema"] != "ananta.semantic-media-vulnerability-exceptions.v1":
        raise SupplyReportError("vulnerability_exceptions_invalid")
    components = value["components"]
    if (
        not isinstance(components, dict)
        or set(components) != set(COMPONENT_SOURCES)
        or any(not isinstance(rows, list) for rows in components.values())
    ):
        raise SupplyReportError("vulnerability_exceptions_invalid")
    return {str(name): list(rows) for name, rows in components.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--syft", default=os.environ.get("ANANTA_SYFT_BIN", "syft"))
    parser.add_argument("--grype", default=os.environ.get("ANANTA_GRYPE_BIN", "grype"))
    parser.add_argument("--component", action="append", default=[], metavar="NAME=IMAGE")
    parser.add_argument("--build-manifest", type=Path, required=True)
    parser.add_argument("--exceptions", type=Path)
    parser.add_argument("--sbom-output", type=Path, required=True)
    parser.add_argument("--scanner-output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    try:
        build_manifest = json.loads(args.build_manifest.read_text(encoding="utf-8"))
        if not isinstance(build_manifest, Mapping):
            raise SupplyReportError("container_build_manifest_invalid_or_stale")
        try:
            built_images = validate_build_manifest(build_manifest)
        except ContainerBuildError as exc:
            raise SupplyReportError(str(exc)) from exc
        sources = {component: values[0] for component, values in built_images.items()}
        for raw in args.component:
            name, separator, source = raw.partition("=")
            if not separator or name not in sources:
                raise SupplyReportError("component_override_invalid")
            sources[name] = source
        sbom, scanner = generate(
            sources,
            build_manifest=build_manifest,
            syft=args.syft,
            grype=args.grype,
            exceptions=_load_exceptions(args.exceptions),
            timeout_seconds=args.timeout_seconds,
        )
        args.sbom_output.parent.mkdir(parents=True, exist_ok=True)
        args.scanner_output.parent.mkdir(parents=True, exist_ok=True)
        args.sbom_output.write_text(json.dumps(sbom, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        args.scanner_output.write_text(json.dumps(scanner, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, SupplyReportError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "reason_code": str(exc).split(":", 1)[0]}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, "components": sorted(sources)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
