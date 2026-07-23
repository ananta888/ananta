#!/usr/bin/env python3
"""Produce fail-closed raw inputs for the SFU broadcast supply-chain gate.

The producer records only facts observed from repository files, Docker image or
container inspection, and scanner output. Missing scanners or attestations are
represented as unknown/false so the downstream gate cannot pass accidentally.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sfu_broadcast_release_common import (  # noqa: E402
    canonical_sha256,
    parse_utc,
    read_bounded_json,
)

DEFAULT_POLICY = ROOT / "config/security/sfu_broadcast_dependency_policy.json"
DEFAULT_MANIFEST = ROOT / "config/release/sfu_broadcast_gate_manifest.json"
DEFAULT_PARENT_SBOM = ROOT / "artifacts/test-gates/semantic-media-sbom.json"
DEFAULT_SBOM_OUTPUT = ROOT / "artifacts/raw/sfu-broadcast-child-sbom.json"
DEFAULT_SCAN_OUTPUT = ROOT / "artifacts/raw/sfu-broadcast-child-scans.json"
DEFAULT_CONTAINER_OUTPUT = ROOT / "artifacts/raw/sfu-broadcast-container-controls.json"
DEFAULT_LOCKFILES = (
    ROOT / "requirements.lock",
    ROOT / "frontend-angular/package-lock.json",
)
DEFAULT_INFRASTRUCTURE_FILES = (
    ROOT / "docker-compose.semantic-media.yml",
    ROOT / "docker-compose.sfu-broadcast.yml",
    ROOT / "docker-compose.sfu-broadcast-turn.yml",
)
COMPONENT_IMAGE_IDS = {
    "hub": "hub",
    "frontend": "frontend",
    "sfu": "sfu",
    "turn": "turn",
    "browser-adapter": "browser",
    "livekit-sdk": "frontend",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PINNED_IMAGE_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
_SENSITIVE_ENV_RE = re.compile(
    r"(?:secret|password|passwd|token|private.?key|credential|api.?key)",
    re.IGNORECASE,
)
APPROVED_SYFT_IMAGES = frozenset({
    "anchore/syft@sha256:13b53ebabe3d215268c90cf8fb9b875f0183908245f376fd4b3a2cb69d21d484",
})
APPROVED_GRYPE_IMAGES = frozenset({
    "anchore/grype@sha256:fd4ab4d1042b522c896e73bdf09ab8bf384fa417df99d6dd0d6e1008c7e7c821",
})
PARENT_SBOM_SCHEMA = "ananta.semantic-media-gate-evidence.v1"
_BUNDLE_LINK_NAME = ".sfu-broadcast-supply-chain-current"
_BUNDLE_DIRECTORY_PREFIX = ".sfu-broadcast-supply-chain-generation-"


class CollectionError(RuntimeError):
    """Raised when an external observation cannot be collected or normalized."""


@dataclass(frozen=True)
class PackageInventory:
    package_count: int
    unknown_license_count: int


@dataclass(frozen=True)
class VulnerabilityInventory:
    critical_open: int
    high_open: int


@dataclass(frozen=True)
class ImageIdentity:
    local_image_sha256: str
    manifest_sha256: str | None


class ImagePort(Protocol):
    def identity(self, image_ref: str) -> ImageIdentity: ...


class SbomPort(Protocol):
    def collect(self, image_ref: str) -> PackageInventory: ...


class VulnerabilityPort(Protocol):
    def collect(self, image_ref: str) -> VulnerabilityInventory: ...


class ContainerPort(Protocol):
    def controls(
        self,
        container_ref: str,
        required_controls: Sequence[str],
    ) -> Mapping[str, Any]: ...


class CommandRunner:
    """Bounded subprocess adapter used by Docker and scanner ports."""

    def __init__(self, *, timeout_seconds: int) -> None:
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds_must_be_positive")
        self._timeout_seconds = timeout_seconds

    def run(self, command: Sequence[str]) -> str:
        try:
            completed = subprocess.run(
                list(command),
                check=True,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise CollectionError("collector_command_unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise CollectionError("collector_command_timeout") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip().splitlines()
            suffix = detail[-1][:240] if detail else "no_detail"
            raise CollectionError(f"collector_command_failed:{suffix}") from exc
        return completed.stdout


def _normalize_sha256(value: object, *, reason: str) -> str:
    normalized = str(value or "").removeprefix("sha256:").lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise CollectionError(reason)
    return normalized


def _load_object(raw: str, *, reason: str) -> Mapping[str, Any]:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CollectionError(reason) from exc
    if isinstance(document, list) and len(document) == 1:
        document = document[0]
    if not isinstance(document, Mapping):
        raise CollectionError(reason)
    return document


def digest_named_files(paths: Sequence[Path], *, root: Path = ROOT) -> str:
    """Hash file names and bytes so concatenation and ordering are unambiguous."""

    digest = hashlib.sha256()
    normalized: list[tuple[str, Path]] = []
    root_resolved = root.resolve()
    for path in paths:
        resolved = path.resolve()
        try:
            name = resolved.relative_to(root_resolved).as_posix()
        except ValueError as exc:
            raise CollectionError("binding_file_outside_repository") from exc
        if not resolved.is_file():
            raise CollectionError(f"binding_file_missing:{name}")
        normalized.append((name, resolved))
    if not normalized:
        raise CollectionError("binding_file_set_empty")
    for name, path in sorted(normalized):
        payload = path.read_bytes()
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


def summarize_spdx(document: Mapping[str, Any]) -> PackageInventory:
    version = str(document.get("spdxVersion") or "")
    packages = document.get("packages")
    if not version.startswith("SPDX-2.") or not isinstance(packages, list):
        raise CollectionError("sbom_spdx_contract_invalid")
    if not packages:
        raise CollectionError("sbom_package_inventory_empty")
    unknown = 0
    for package in packages:
        if not isinstance(package, Mapping):
            raise CollectionError("sbom_package_contract_invalid")
        licenses = {
            str(package.get("licenseDeclared") or "").strip().upper(),
            str(package.get("licenseConcluded") or "").strip().upper(),
        }
        known = any(value not in {"", "NONE", "NOASSERTION"} for value in licenses)
        if not known:
            unknown += 1
    return PackageInventory(package_count=len(packages), unknown_license_count=unknown)


def summarize_grype(document: Mapping[str, Any]) -> VulnerabilityInventory:
    matches = document.get("matches")
    if not isinstance(matches, list):
        raise CollectionError("grype_matches_invalid")
    critical: set[tuple[str, str, str]] = set()
    high: set[tuple[str, str, str]] = set()
    for match in matches:
        if not isinstance(match, Mapping):
            raise CollectionError("grype_match_contract_invalid")
        vulnerability = match.get("vulnerability")
        artifact = match.get("artifact")
        if not isinstance(vulnerability, Mapping) or not isinstance(artifact, Mapping):
            raise CollectionError("grype_match_contract_invalid")
        key = (
            str(vulnerability.get("id") or ""),
            str(artifact.get("name") or ""),
            str(artifact.get("version") or ""),
        )
        severity = str(vulnerability.get("severity") or "").lower()
        if severity == "critical":
            critical.add(key)
        elif severity == "high":
            high.add(key)
    return VulnerabilityInventory(
        critical_open=len(critical),
        high_open=len(high),
    )


def normalize_container_inspect(
    document: Mapping[str, Any],
    required_controls: Sequence[str],
) -> dict[str, Any]:
    config = document.get("Config")
    host = document.get("HostConfig")
    if not isinstance(config, Mapping) or not isinstance(host, Mapping):
        raise CollectionError("container_inspect_contract_invalid")
    user = str(config.get("User") or "").split(":", 1)[0].strip().lower()
    cap_drop = {str(value).upper() for value in host.get("CapDrop") or []}
    cap_add = sorted({str(value).upper() for value in host.get("CapAdd") or []})
    security_options = [str(value).lower() for value in host.get("SecurityOpt") or []]
    healthcheck = config.get("Healthcheck")
    health_test = healthcheck.get("Test") if isinstance(healthcheck, Mapping) else None
    memory = host.get("Memory")
    pids = host.get("PidsLimit")
    cpu = host.get("NanoCpus") or host.get("CpuQuota")
    environment = [str(value) for value in config.get("Env") or []]
    command = " ".join(str(value) for value in (config.get("Cmd") or []))
    secret_env_values = []
    for item in environment:
        name, separator, value = item.partition("=")
        if (
            separator
            and _SENSITIVE_ENV_RE.search(name)
            and not name.upper().endswith("_FILE")
            and value
        ):
            secret_env_values.append(name)
    if _SENSITIVE_ENV_RE.search(command):
        secret_env_values.append("container_command")
    apparmor_profile = str(document.get("AppArmorProfile") or "")
    seccomp_value = next(
        (value.split("=", 1)[1] for value in security_options if value.startswith("seccomp=")),
        None,
    )
    controls: dict[str, Any] = {
        # Config.User is an intended user, not an observation of the running PID.
        "non_root": False,
        "read_only_rootfs": host.get("ReadonlyRootfs") is True,
        "capabilities_drop_all": "ALL" in cap_drop,
        # A Docker network attachment alone does not prove an egress policy.
        "network_policy": False,
        # Absence of a suspicious environment name does not prove secret origin.
        "secret_refs_only": False,
        "healthcheck": (
            isinstance(health_test, list)
            and bool(health_test)
            and str(health_test[0]).upper() != "NONE"
        ),
        "resource_limits": (
            isinstance(memory, int)
            and memory > 0
            and isinstance(pids, int)
            and pids > 0
            and isinstance(cpu, int)
            and cpu > 0
        ),
        # Runtime inspection cannot prove absence of control-plane logic.
        "forbidden_control_logic_absent": False,
        "capabilities_added": cap_add,
        "seccomp": {
            "available": seccomp_value is not None,
            "enforced": seccomp_value is not None and seccomp_value != "unconfined",
        },
        "apparmor": {
            "available": bool(apparmor_profile),
            "enforced": bool(apparmor_profile and apparmor_profile != "unconfined"),
        },
        "forbidden_responsibilities": None,
        "observation_errors": sorted(
            {
                "non_root_requires_runtime_process_observation",
                "network_policy_requires_external_probe",
                "responsibility_boundary_requires_external_attestation",
                "secret_refs_require_external_mount_attestation",
                *(
                    {"secret_value_present_in_runtime_configuration"}
                    if secret_env_values
                    else set()
                ),
            }
        ),
    }
    for control in required_controls:
        controls.setdefault(str(control), False)
    return controls


class DockerImageAdapter:
    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    def identity(self, image_ref: str) -> ImageIdentity:
        document = _load_object(
            self._runner.run(["docker", "image", "inspect", image_ref]),
            reason="docker_image_inspect_invalid",
        )
        local_image_sha256 = _normalize_sha256(
            document.get("Id"),
            reason="docker_image_digest_invalid",
        )
        repo_digests = document.get("RepoDigests")
        manifest_digests: set[str] = set()
        if isinstance(repo_digests, list):
            expected_repository = _image_repository(image_ref)
            for value in repo_digests:
                repository, separator, digest = str(value).rpartition("@sha256:")
                if not separator:
                    continue
                if expected_repository and repository != expected_repository:
                    continue
                try:
                    manifest_digests.add(_normalize_sha256(
                        digest,
                        reason="docker_manifest_digest_invalid",
                    ))
                except CollectionError:
                    continue
        manifest_sha256 = (
            next(iter(manifest_digests)) if len(manifest_digests) == 1 else None
        )
        return ImageIdentity(
            local_image_sha256=local_image_sha256,
            manifest_sha256=manifest_sha256,
        )


class DockerSpdxAdapter:
    def __init__(
        self,
        runner: CommandRunner,
        *,
        syft_image: str | None,
    ) -> None:
        self._runner = runner
        self._syft_image = syft_image
        if syft_image is not None and syft_image not in APPROVED_SYFT_IMAGES:
            raise CollectionError("syft_image_not_approved")

    def collect(self, image_ref: str) -> PackageInventory:
        if not self._syft_image:
            raise CollectionError("syft_collector_unavailable")
        raw = self._runner.run([
            "docker",
            "run",
            "--rm",
            "-v",
            "/var/run/docker.sock:/var/run/docker.sock:ro",
            self._syft_image,
            image_ref,
            "-o",
            "spdx-json",
        ])
        return summarize_spdx(_load_object(raw, reason="sbom_output_invalid"))


class GrypeAdapter:
    def __init__(
        self,
        runner: CommandRunner,
        *,
        grype_image: str | None,
    ) -> None:
        self._runner = runner
        self._grype_image = grype_image
        if grype_image is not None and grype_image not in APPROVED_GRYPE_IMAGES:
            raise CollectionError("grype_image_not_approved")

    def collect(self, image_ref: str) -> VulnerabilityInventory:
        if not self._grype_image:
            raise CollectionError("grype_collector_unavailable")
        raw = self._runner.run([
            "docker",
            "run",
            "--rm",
            "-v",
            "/var/run/docker.sock:/var/run/docker.sock:ro",
            self._grype_image,
            image_ref,
            "-o",
            "json",
        ])
        return summarize_grype(_load_object(raw, reason="grype_output_invalid"))


class DockerContainerAdapter:
    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    def controls(
        self,
        container_ref: str,
        required_controls: Sequence[str],
    ) -> Mapping[str, Any]:
        document = _load_object(
            self._runner.run(["docker", "container", "inspect", container_ref]),
            reason="docker_container_inspect_invalid",
        )
        return normalize_container_inspect(document, required_controls)


class SupplyChainInputBuilder:
    """Application service that composes observation ports into gate inputs."""

    def __init__(
        self,
        *,
        images: ImagePort,
        sboms: SbomPort,
        vulnerabilities: VulnerabilityPort,
        containers: ContainerPort,
    ) -> None:
        self._images = images
        self._sboms = sboms
        self._vulnerabilities = vulnerabilities
        self._containers = containers

    def build(
        self,
        *,
        policy: Mapping[str, Any],
        image_refs: Mapping[str, str],
        container_refs: Mapping[str, str],
        source_sha256: str,
        lockfile_sha256: str,
        infrastructure_sha256: str,
        parent_sbom: Mapping[str, Any],
        security_observations: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        required_images = [str(value) for value in policy.get("required_image_ids", [])]
        required_components = [
            str(value) for value in policy.get("required_components", [])
        ]
        if set(image_refs) != set(required_images):
            raise CollectionError("required_image_mapping_mismatch")
        unsupported_components = set(required_components) - set(COMPONENT_IMAGE_IDS)
        if unsupported_components:
            raise CollectionError("required_component_mapping_missing")

        image_identities = {
            image_id: self._images.identity(image_refs[image_id])
            for image_id in required_images
        }
        image_digests = {
            image_id: image_identities[image_id].manifest_sha256
            for image_id in required_images
        }
        bindings = {
            "source_sha256": _normalize_sha256(
                source_sha256,
                reason="source_digest_invalid",
            ),
            "config_sha256": canonical_sha256(policy),
            "lockfile_sha256": _normalize_sha256(
                lockfile_sha256,
                reason="lockfile_digest_invalid",
            ),
            "infrastructure_sha256": _normalize_sha256(
                infrastructure_sha256,
                reason="infrastructure_digest_invalid",
            ),
            "image_digests": image_digests,
        }

        sbom_cache: dict[str, PackageInventory | None] = {}
        sbom_errors: dict[str, str] = {}
        components: list[dict[str, Any]] = []
        for component_id in required_components:
            image_id = COMPONENT_IMAGE_IDS[component_id]
            image_ref = image_refs[image_id]
            image_identity = image_identities[image_id]
            cache_key = image_identity.local_image_sha256
            if cache_key not in sbom_cache:
                try:
                    sbom_cache[cache_key] = self._sboms.collect(image_ref)
                except CollectionError as exc:
                    sbom_cache[cache_key] = None
                    sbom_errors[image_id] = str(exc).split(":", 1)[0]
            inventory = sbom_cache[cache_key]
            components.append({
                "component_id": component_id,
                "package_count": inventory.package_count if inventory else 0,
                "unknown_license_count": (
                    inventory.unknown_license_count if inventory else None
                ),
                "floating_reference": not (
                    _PINNED_IMAGE_RE.search(image_ref)
                    or image_ref.startswith("sha256:")
                ),
                "deployed_digest": image_digests[image_id],
                "local_image_sha256": image_identity.local_image_sha256,
                "image_id": image_id,
            })
        delta_rows = [
            {
                "component_id": row["component_id"],
                "deployed_digest": row["deployed_digest"],
            }
            for row in components
        ]
        sbom = {
            "schema": "ananta.sfu-broadcast-child-sbom.v1",
            "format": "spdx-2.3",
            "bindings": bindings,
            "components": components,
            "child_delta": {
                "parent_sbom_sha256": canonical_sha256(parent_sbom),
                "component_ids": required_components,
                "delta_sha256": canonical_sha256(delta_rows),
            },
            "collection_errors": sbom_errors,
        }

        scan_errors: dict[str, str] = {}
        scan_results: dict[str, VulnerabilityInventory] = {}
        attempted_scan_digests: set[str] = set()
        for image_id in required_images:
            local_digest = image_identities[image_id].local_image_sha256
            if local_digest in attempted_scan_digests:
                continue
            attempted_scan_digests.add(local_digest)
            try:
                scan_results[local_digest] = self._vulnerabilities.collect(
                    image_refs[image_id]
                )
            except CollectionError as exc:
                scan_errors[image_id] = str(exc).split(":", 1)[0]
        critical_open: int | None = None
        high_open: int | None = None
        if not scan_errors:
            critical_open = sum(row.critical_open for row in scan_results.values())
            high_open = sum(row.high_open for row in scan_results.values())

        if security_observations:
            scan_errors["untrusted_security_observations"] = "ignored"
        provenance = []
        for component in components:
            component_id = component["component_id"]
            subject = component["deployed_digest"]
            provenance.append({
                "component_id": component_id,
                "signature_verified": False,
                "builder_verified": False,
                "subject_sha256": subject,
                "verification_source": "unavailable",
            })
        scans = {
            "schema": "ananta.sfu-broadcast-child-security-scan.v1",
            "bindings": bindings,
            "critical_open": critical_open,
            "high_open": high_open,
            # Grype is not a malware or secret scanner. Unknown is not zero.
            "malware_detected": None,
            "secrets_detected": None,
            "provenance": provenance,
            "exceptions": [],
            "collection_errors": scan_errors,
        }

        required_controls = [
            str(value) for value in policy.get("required_container_controls", [])
        ]
        control_rows = []
        for image_id in required_images:
            container_ref = container_refs.get(image_id)
            if container_ref:
                try:
                    controls = dict(
                        self._containers.controls(container_ref, required_controls)
                    )
                except CollectionError as exc:
                    controls = _unavailable_controls(
                        required_controls,
                        reason=str(exc).split(":", 1)[0],
                    )
            else:
                controls = _unavailable_controls(
                    required_controls,
                    reason="container_mapping_missing",
                )
            control_rows.append({"component_id": image_id, **controls})
        container_evidence = {
            "schema": "ananta.sfu-broadcast-container-controls.v1",
            "bindings": bindings,
            "components": control_rows,
        }
        return sbom, scans, container_evidence


def _unavailable_controls(
    required_controls: Sequence[str],
    *,
    reason: str,
) -> dict[str, Any]:
    return {
        **{str(control): False for control in required_controls},
        "capabilities_added": None,
        "seccomp": {"available": False, "enforced": False},
        "apparmor": {"available": False, "enforced": False},
        "forbidden_responsibilities": None,
        "observation_errors": [reason],
    }


def _parse_pairs(values: Sequence[str], *, option: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key or not item or key in parsed:
            raise CollectionError(f"{option}_mapping_invalid")
        parsed[key] = item
    return parsed


def _source_files(manifest_path: Path) -> list[Path]:
    manifest = read_bounded_json(manifest_path)
    execution = manifest.get("execution")
    values = execution.get("source_files") if isinstance(execution, Mapping) else None
    if not isinstance(values, list) or not values:
        raise CollectionError("manifest_source_files_invalid")
    return [ROOT / str(value) for value in values]


def _paths(values: Sequence[Path], defaults: Sequence[Path]) -> list[Path]:
    return list(values) if values else list(defaults)


def _image_repository(image_ref: str) -> str | None:
    if image_ref.startswith("sha256:"):
        return None
    repository = image_ref.split("@", 1)[0]
    slash = repository.rfind("/")
    colon = repository.rfind(":")
    if colon > slash:
        repository = repository[:colon]
    return repository or None


def validate_parent_sbom(parent_sbom: Mapping[str, Any]) -> None:
    if parent_sbom.get("schema") != PARENT_SBOM_SCHEMA:
        raise CollectionError("parent_sbom_schema_invalid")
    if parent_sbom.get("status") != "passed":
        raise CollectionError("parent_sbom_status_not_passed")
    if parse_utc(parent_sbom.get("produced_at")) is None:
        raise CollectionError("parent_sbom_timestamp_invalid")
    _normalize_sha256(
        parent_sbom.get("source_sha256"),
        reason="parent_sbom_source_binding_invalid",
    )
    _normalize_sha256(
        parent_sbom.get("config_sha256"),
        reason="parent_sbom_config_binding_invalid",
    )


def _bundle_parent(paths: Sequence[Path]) -> Path:
    if len(paths) != 3 or len({path.name for path in paths}) != 3:
        raise CollectionError("output_bundle_paths_invalid")
    parents = {path.parent.resolve() for path in paths}
    if len(parents) != 1:
        raise CollectionError("output_bundle_parent_mismatch")
    return next(iter(parents))


def _unlink_file_or_symlink(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        raise CollectionError("output_bundle_path_not_file")


def invalidate_output_set(paths: Sequence[Path]) -> None:
    parent = _bundle_parent(paths)
    parent.mkdir(parents=True, exist_ok=True)
    _unlink_file_or_symlink(parent / _BUNDLE_LINK_NAME)
    for path in paths:
        _unlink_file_or_symlink(path)


def publish_output_set(
    documents: Mapping[Path, Mapping[str, Any]],
    *,
    replace: Callable[[str | Path, str | Path], None] = os.replace,
) -> None:
    paths = list(documents)
    parent = _bundle_parent(paths)
    parent.mkdir(parents=True, exist_ok=True)
    generation = Path(tempfile.mkdtemp(
        prefix=_BUNDLE_DIRECTORY_PREFIX,
        dir=parent,
    ))
    temporary_links: list[Path] = []
    current = parent / _BUNDLE_LINK_NAME
    try:
        for output, document in documents.items():
            payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
            target = generation / output.name
            with target.open("x", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        invalidate_output_set(paths)
        for output in paths:
            temporary = parent / f".{output.name}.{uuid4().hex}.link"
            os.symlink(f"{_BUNDLE_LINK_NAME}/{output.name}", temporary)
            temporary_links.append(temporary)
            replace(temporary, output)
        temporary_current = parent / f".{_BUNDLE_LINK_NAME}.{uuid4().hex}.link"
        os.symlink(generation.name, temporary_current)
        temporary_links.append(temporary_current)
        # This single atomic switch makes all three stable symlinks valid.
        replace(temporary_current, current)
    except Exception:
        _unlink_file_or_symlink(current)
        for path in paths:
            _unlink_file_or_symlink(path)
        for temporary in temporary_links:
            _unlink_file_or_symlink(temporary)
        shutil.rmtree(generation, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate fail-closed raw SFU broadcast supply-chain inputs.",
    )
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--parent-sbom", type=Path, default=DEFAULT_PARENT_SBOM)
    parser.add_argument(
        "--image",
        action="append",
        default=[],
        metavar="ID=REF",
        help="Required image mapping; REF is resolved through docker image inspect.",
    )
    parser.add_argument(
        "--container",
        action="append",
        default=[],
        metavar="ID=NAME",
        help="Optional running-container mapping for runtime hardening inspection.",
    )
    parser.add_argument("--source-file", action="append", type=Path, default=[])
    parser.add_argument("--lockfile", action="append", type=Path, default=[])
    parser.add_argument("--infrastructure", action="append", type=Path, default=[])
    parser.add_argument(
        "--syft-image",
        help="Optional digest-pinned anchore/syft container image.",
    )
    parser.add_argument(
        "--grype-image",
        help="Optional digest-pinned anchore/grype container image.",
    )
    parser.add_argument(
        "--security-observations",
        type=Path,
        help="Optional externally produced malware, secret, and provenance observations.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--sbom-output", type=Path, default=DEFAULT_SBOM_OUTPUT)
    parser.add_argument("--scan-output", type=Path, default=DEFAULT_SCAN_OUTPUT)
    parser.add_argument(
        "--container-output",
        type=Path,
        default=DEFAULT_CONTAINER_OUTPUT,
    )
    args = parser.parse_args()

    try:
        output_paths = [
            args.sbom_output,
            args.scan_output,
            args.container_output,
        ]
        invalidate_output_set(output_paths)
        policy = read_bounded_json(args.policy)
        parent_sbom = read_bounded_json(args.parent_sbom)
        validate_parent_sbom(parent_sbom)
        image_refs = _parse_pairs(args.image, option="image")
        container_refs = _parse_pairs(args.container, option="container")
        security_observations = (
            read_bounded_json(args.security_observations)
            if args.security_observations
            else None
        )
        source_files = (
            list(args.source_file)
            if args.source_file
            else _source_files(args.manifest)
        )
        runner = CommandRunner(timeout_seconds=args.timeout_seconds)
        builder = SupplyChainInputBuilder(
            images=DockerImageAdapter(runner),
            sboms=DockerSpdxAdapter(runner, syft_image=args.syft_image),
            vulnerabilities=GrypeAdapter(runner, grype_image=args.grype_image),
            containers=DockerContainerAdapter(runner),
        )
        sbom, scans, container_evidence = builder.build(
            policy=policy,
            image_refs=image_refs,
            container_refs=container_refs,
            source_sha256=digest_named_files(source_files),
            lockfile_sha256=digest_named_files(
                _paths(args.lockfile, DEFAULT_LOCKFILES)
            ),
            infrastructure_sha256=digest_named_files(
                _paths(args.infrastructure, DEFAULT_INFRASTRUCTURE_FILES)
            ),
            parent_sbom=parent_sbom,
            security_observations=security_observations,
        )
        publish_output_set({
            args.sbom_output: sbom,
            args.scan_output: scans,
            args.container_output: container_evidence,
        })
    except (CollectionError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({
            "ok": False,
            "reason_code": str(exc).split(":", 1)[0],
        }, sort_keys=True))
        return 2

    print(json.dumps({
        "ok": True,
        "outputs": {
            "sbom": os.fspath(args.sbom_output),
            "scans": os.fspath(args.scan_output),
            "containers": os.fspath(args.container_output),
        },
        "collection": {
            "sbom_errors": sbom["collection_errors"],
            "scan_errors": scans["collection_errors"],
            "provenance_verified": all(
                row["signature_verified"] and row["builder_verified"]
                for row in scans["provenance"]
            ),
            "malware_observed": scans["malware_detected"] is not None,
            "secrets_observed": scans["secrets_detected"] is not None,
        },
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
