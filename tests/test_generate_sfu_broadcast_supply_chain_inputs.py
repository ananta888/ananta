import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from scripts.generate_sfu_broadcast_supply_chain_inputs import (
    APPROVED_GRYPE_IMAGES,
    APPROVED_SYFT_IMAGES,
    CollectionError,
    CommandRunner,
    DockerImageAdapter,
    DockerSpdxAdapter,
    GrypeAdapter,
    ImageIdentity,
    PackageInventory,
    SupplyChainInputBuilder,
    VulnerabilityInventory,
    digest_named_files,
    invalidate_output_set,
    normalize_container_inspect,
    publish_output_set,
    summarize_grype,
    summarize_spdx,
    validate_parent_sbom,
)


class _Images:
    def identity(self, image_ref: str) -> ImageIdentity:
        local, manifest = {
            "hub@sha256:" + "1" * 64: ("a" * 64, "1" * 64),
            "frontend:local": ("b" * 64, "2" * 64),
            "sfu@sha256:" + "3" * 64: ("c" * 64, "3" * 64),
            "turn@sha256:" + "4" * 64: ("d" * 64, "4" * 64),
            "browser:local": ("b" * 64, "2" * 64),
        }[image_ref]
        return ImageIdentity(local_image_sha256=local, manifest_sha256=manifest)


class _Sboms:
    def collect(self, image_ref: str) -> PackageInventory:
        return PackageInventory(package_count=7, unknown_license_count=0)


class _Vulnerabilities:
    def collect(self, image_ref: str) -> VulnerabilityInventory:
        return VulnerabilityInventory(critical_open=0, high_open=0)


class _Containers:
    def controls(
        self,
        container_ref: str,
        required_controls: Sequence[str],
    ) -> Mapping[str, Any]:
        return {
            **{control: True for control in required_controls},
            "capabilities_added": [],
            "seccomp": {"available": True, "enforced": True},
            "apparmor": {"available": False, "enforced": False},
            "forbidden_responsibilities": [],
            "observation_errors": [],
        }


def test_digest_named_files_binds_names_order_and_bytes(tmp_path: Path) -> None:
    left = tmp_path / "a.lock"
    right = tmp_path / "b.lock"
    left.write_text("ab", encoding="utf-8")
    right.write_text("c", encoding="utf-8")

    first = digest_named_files([right, left], root=tmp_path)
    second = digest_named_files([left, right], root=tmp_path)
    right.write_text("bc", encoding="utf-8")
    changed = digest_named_files([left, right], root=tmp_path)

    assert first == second
    assert changed != first
    assert len(first) == 64


def test_scanner_normalizers_count_unknown_licenses_and_unique_findings() -> None:
    inventory = summarize_spdx({
        "spdxVersion": "SPDX-2.3",
        "packages": [
            {"licenseDeclared": "MIT", "licenseConcluded": "NOASSERTION"},
            {"licenseDeclared": "NOASSERTION", "licenseConcluded": "NONE"},
        ],
    })
    findings = summarize_grype({
        "matches": [
            {
                "vulnerability": {"id": "CVE-1", "severity": "Critical"},
                "artifact": {"name": "lib-a", "version": "1"},
            },
            {
                "vulnerability": {"id": "CVE-1", "severity": "Critical"},
                "artifact": {"name": "lib-a", "version": "1"},
            },
            {
                "vulnerability": {"id": "CVE-2", "severity": "High"},
                "artifact": {"name": "lib-b", "version": "2"},
            },
        ],
    })

    assert inventory == PackageInventory(package_count=2, unknown_license_count=1)
    assert findings == VulnerabilityInventory(critical_open=1, high_open=1)


def test_container_inspect_does_not_overclaim_external_controls() -> None:
    controls = normalize_container_inspect(
        {
            "Config": {
                "User": "65532:65532",
                "Env": ["API_TOKEN=plain-text"],
                "Cmd": ["/server"],
                "Healthcheck": {"Test": ["CMD", "/healthcheck"]},
            },
            "HostConfig": {
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "CapAdd": [],
                "SecurityOpt": [
                    "no-new-privileges:true",
                    "seccomp=default",
                ],
                "Memory": 1024,
                "PidsLimit": 64,
                "NanoCpus": 1_000_000_000,
            },
            "AppArmorProfile": "docker-default",
        },
        [
            "non_root",
            "read_only_rootfs",
            "capabilities_drop_all",
            "network_policy",
            "secret_refs_only",
            "healthcheck",
            "resource_limits",
            "forbidden_control_logic_absent",
        ],
    )

    assert controls["non_root"] is False
    assert controls["read_only_rootfs"] is True
    assert controls["capabilities_drop_all"] is True
    assert controls["healthcheck"] is True
    assert controls["resource_limits"] is True
    assert controls["seccomp"] == {"available": True, "enforced": True}
    assert controls["apparmor"] == {"available": True, "enforced": True}
    assert controls["network_policy"] is False
    assert controls["secret_refs_only"] is False
    assert controls["forbidden_control_logic_absent"] is False


def test_builder_binds_real_digests_and_leaves_unobserved_claims_unknown() -> None:
    policy = {
        "required_image_ids": ["hub", "frontend", "sfu", "turn", "browser"],
        "required_components": [
            "hub",
            "frontend",
            "sfu",
            "turn",
            "browser-adapter",
            "livekit-sdk",
        ],
        "required_container_controls": ["non_root"],
    }
    image_refs = {
        "hub": "hub@sha256:" + "1" * 64,
        "frontend": "frontend:local",
        "sfu": "sfu@sha256:" + "3" * 64,
        "turn": "turn@sha256:" + "4" * 64,
        "browser": "browser:local",
    }
    builder = SupplyChainInputBuilder(
        images=_Images(),
        sboms=_Sboms(),
        vulnerabilities=_Vulnerabilities(),
        containers=_Containers(),
    )

    sbom, scans, containers = builder.build(
        policy=policy,
        image_refs=image_refs,
        container_refs={image_id: image_id for image_id in image_refs},
        source_sha256="a" * 64,
        lockfile_sha256="b" * 64,
        infrastructure_sha256="c" * 64,
        parent_sbom={"schema": "parent"},
    )

    assert sbom["bindings"] == scans["bindings"] == containers["bindings"]
    assert sbom["bindings"]["image_digests"]["sfu"] == "3" * 64
    assert {
        row["component_id"] for row in sbom["components"]
    } == set(policy["required_components"])
    assert next(
        row for row in sbom["components"] if row["component_id"] == "hub"
    )["floating_reference"] is False
    assert next(
        row for row in sbom["components"] if row["component_id"] == "frontend"
    )["floating_reference"] is True
    assert scans["critical_open"] == 0
    assert scans["high_open"] == 0
    assert scans["malware_detected"] is None
    assert scans["secrets_detected"] is None
    assert all(row["signature_verified"] is False for row in scans["provenance"])
    assert all(row["builder_verified"] is False for row in scans["provenance"])
    assert next(
        row for row in sbom["components"] if row["component_id"] == "sfu"
    )["local_image_sha256"] == "c" * 64


def test_free_security_observations_cannot_create_formal_claims() -> None:
    policy = {
        "required_image_ids": ["hub", "frontend", "sfu", "turn", "browser"],
        "required_components": list((
            "hub", "frontend", "sfu", "turn", "browser-adapter", "livekit-sdk",
        )),
        "required_container_controls": ["non_root"],
    }
    image_refs = {
        "hub": "hub@sha256:" + "1" * 64,
        "frontend": "frontend:local",
        "sfu": "sfu@sha256:" + "3" * 64,
        "turn": "turn@sha256:" + "4" * 64,
        "browser": "browser:local",
    }
    builder = SupplyChainInputBuilder(
        images=_Images(),
        sboms=_Sboms(),
        vulnerabilities=_Vulnerabilities(),
        containers=_Containers(),
    )
    _, scans, _ = builder.build(
        policy=policy,
        image_refs=image_refs,
        container_refs={},
        source_sha256="a" * 64,
        lockfile_sha256="b" * 64,
        infrastructure_sha256="c" * 64,
        parent_sbom={"schema": "parent"},
        security_observations={
            "malware_detected": 0,
            "secrets_detected": 0,
            "provenance": [{
                "component_id": "hub",
                "signature_verified": True,
                "builder_verified": True,
                "subject_sha256": "1" * 64,
            }],
            "exceptions": [{"approval_signature_verified": True}],
        },
    )

    assert scans["malware_detected"] is None
    assert scans["secrets_detected"] is None
    assert scans["exceptions"] == []
    assert all(row["signature_verified"] is False for row in scans["provenance"])
    assert scans["collection_errors"]["untrusted_security_observations"] == "ignored"


def test_unapproved_scanner_images_are_rejected() -> None:
    runner = CommandRunner(timeout_seconds=1)
    unknown = "anchore/tool@sha256:" + "f" * 64

    with pytest.raises(CollectionError, match="syft_image_not_approved"):
        DockerSpdxAdapter(runner, syft_image=unknown)
    with pytest.raises(CollectionError, match="grype_image_not_approved"):
        GrypeAdapter(runner, grype_image=unknown)

    assert all("@sha256:" in value for value in APPROVED_SYFT_IMAGES)
    assert all("@sha256:" in value for value in APPROVED_GRYPE_IMAGES)


class _InspectRunner:
    def run(self, command: Sequence[str]) -> str:
        return json.dumps([{
            "Id": "sha256:" + "a" * 64,
            "RepoDigests": ["registry.example/sfu@sha256:" + "b" * 64],
        }])


def test_docker_identity_separates_manifest_from_local_image_id() -> None:
    identity = DockerImageAdapter(_InspectRunner()).identity(
        "registry.example/sfu:v1",
    )

    assert identity.local_image_sha256 == "a" * 64
    assert identity.manifest_sha256 == "b" * 64


@pytest.mark.parametrize(
    ("document", "reason"),
    [
        ({"schema": "wrong", "status": "passed"}, "parent_sbom_schema_invalid"),
        (
            {
                "schema": "ananta.semantic-media-gate-evidence.v1",
                "status": "failed",
            },
            "parent_sbom_status_not_passed",
        ),
        (
            {
                "schema": "ananta.semantic-media-gate-evidence.v1",
                "status": "passed",
                "produced_at": "invalid",
            },
            "parent_sbom_timestamp_invalid",
        ),
        (
            {
                "schema": "ananta.semantic-media-gate-evidence.v1",
                "status": "passed",
                "produced_at": "2026-07-23T00:00:00Z",
                "source_sha256": "invalid",
                "config_sha256": "f" * 64,
            },
            "parent_sbom_source_binding_invalid",
        ),
    ],
)
def test_parent_sbom_validation_rejects_untrusted_inputs(
    document: Mapping[str, Any],
    reason: str,
) -> None:
    with pytest.raises(CollectionError, match=reason):
        validate_parent_sbom(document)


def test_output_bundle_failure_invalidates_all_stable_paths(tmp_path: Path) -> None:
    paths = [
        tmp_path / "sbom.json",
        tmp_path / "scans.json",
        tmp_path / "containers.json",
    ]
    for path in paths:
        path.write_text('{"stale": true}\\n', encoding="utf-8")
    calls = 0

    def fail_before_current(source: str | Path, target: str | Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("simulated_current_switch_failure")
        os.replace(source, target)

    with pytest.raises(OSError, match="simulated_current_switch_failure"):
        publish_output_set(
            {path: {"schema": path.stem} for path in paths},
            replace=fail_before_current,
        )

    assert all(not path.exists() and not path.is_symlink() for path in paths)
    invalidate_output_set(paths)


def test_output_bundle_publishes_all_documents_through_one_pointer(
    tmp_path: Path,
) -> None:
    paths = [
        tmp_path / "sbom.json",
        tmp_path / "scans.json",
        tmp_path / "containers.json",
    ]
    publish_output_set(
        {path: {"schema": path.stem} for path in paths},
    )

    assert all(path.is_symlink() for path in paths)
    assert [json.loads(path.read_text())["schema"] for path in paths] == [
        "sbom",
        "scans",
        "containers",
    ]
