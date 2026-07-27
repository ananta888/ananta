from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import pytest

from agent.services.semantic_media_program_evidence import ProgramEvidenceError, canonical_sha256, source_hash
from scripts.build_semantic_media_containers import build_source_sha256
from scripts.generate_semantic_media_supply_chain_reports import SOURCE_BINDINGS
from scripts.run_semantic_media_supply_chain_gate import COMPONENTS, evaluate, static_hardening_checks, unavailable


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _reports(*, critical: int = 0, high: int = 0, exception: bool = False):
    source = build_source_sha256()
    policy = source_hash(Path(__file__).resolve().parents[1], SOURCE_BINDINGS)
    components = []
    images = []
    manifest_images = []
    for name in sorted(COMPONENTS):
        image = _digest(f"image:{name}")
        manifest_images.append({"component": name, "image_digest": image, "reference": f"test-{name}:local"})
        components.append(
            {
                "name": name,
                "image_digest": image,
                "packages": [
                    {"name": f"package-{name}", "version": "1.0.0", "license": "Apache-2.0", "origin": "registry"}
                ],
            }
        )
        images.append(
            {
                "component": name,
                "image_digest": image,
                "critical": critical if name == "hub" else 0,
                "high": high if name == "hub" else 0,
                "findings": (
                    [
                        {
                            "finding_id": f"CVE-critical-{index}", "severity": "critical",
                            "package": "package-hub", "version": "1.0.0", "fix_state": "fixed",
                        }
                        for index in range(critical)
                    ]
                    + [
                        {
                            "finding_id": f"CVE-high-{index}", "severity": "high",
                            "package": "package-hub", "version": "1.0.0", "fix_state": "fixed",
                        }
                        for index in range(high)
                    ]
                    if name == "hub" else []
                ),
                "exceptions": (
                    [
                        {
                            "finding_id": "CVE-high-0",
                            "owner": "security",
                            "rationale": "bounded-pilot-only",
                            "expires_on": "2026-08-01",
                        }
                    ]
                    if exception and name == "hub"
                    else []
                ),
            }
        )
    manifest = {
        "schema": "ananta.semantic-media-container-builds.v2",
        "source_sha256": source,
        "images": manifest_images,
        "status": "passed",
    }
    binding = canonical_sha256(manifest)
    return (
        {
            "schema": "ananta.semantic-media-sbom.v2",
            "source_sha256": source,
            "policy_sha256": policy,
            "build_manifest_sha256": binding,
            "components": components,
        },
        {
            "schema": "ananta.semantic-media-vulnerability-report.v2",
            "source_sha256": source,
            "policy_sha256": policy,
            "build_manifest_sha256": binding,
            "images": images,
        },
        manifest,
    )


def test_complete_clean_sbom_scanner_and_hardening_evidence_pass() -> None:
    sbom, scanner, manifest = _reports()
    evidence = evaluate(sbom, scanner, build_manifest=manifest, as_of=dt.date(2026, 7, 19))
    assert evidence.status == "failed"
    assert set(evidence.reason_codes) == {
        "container_internal_network_missing",
        "model_manifest_binding_missing",
    }
    hardening = static_hardening_checks()
    assert all(
        passed
        for reason, passed in hardening.items()
        if reason
        not in {
            "container_internal_network_missing",
            "model_manifest_binding_missing",
        }
    )
    assert hardening["container_external_digest_pin_missing"]
    assert hardening["container_secret_boundary_missing"]
    assert hardening["hub_dependency_pin_missing"]
    assert not hardening["model_manifest_binding_missing"]
    assert not hardening["container_internal_network_missing"]


def test_critical_or_unaccepted_high_blocks_and_missing_scanner_is_unverified() -> None:
    sbom, scanner, manifest = _reports(critical=1, high=2, exception=True)
    evidence = evaluate(sbom, scanner, build_manifest=manifest, as_of=dt.date(2026, 7, 19))
    assert evidence.status == "failed"
    assert "semantic_media_critical_vulnerability" in evidence.reason_codes
    assert "semantic_media_high_vulnerability_unaccepted" in evidence.reason_codes
    assert unavailable().status == "unverified" and unavailable().release_blocking


def test_stale_build_source_and_scanner_image_drift_fail_closed() -> None:
    sbom, scanner, manifest = _reports()
    stale = dict(manifest, source_sha256=_digest("stale-source"))
    with pytest.raises(ProgramEvidenceError, match="semantic_media_build_manifest_invalid_or_stale"):
        evaluate(sbom, scanner, build_manifest=stale, as_of=dt.date(2026, 7, 19))

    scanner["images"][0]["image_digest"] = _digest("different-image")
    evidence = evaluate(sbom, scanner, build_manifest=manifest, as_of=dt.date(2026, 7, 19))
    assert evidence.status == "failed"
    assert "semantic_media_scanner_image_mismatch" in evidence.reason_codes
