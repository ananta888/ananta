from __future__ import annotations

import pytest

from scripts import generate_semantic_media_supply_chain_reports as generator
from scripts.build_semantic_media_containers import BUILD_COMPONENTS, build_source_sha256
from scripts.generate_semantic_media_supply_chain_reports import (
    COMPONENT_SOURCES,
    SupplyReportError,
    _load_exceptions,
    normalize_exceptions,
    normalize_findings,
    normalize_packages,
)


def _build_manifest() -> dict[str, object]:
    return {
        "schema": "ananta.semantic-media-container-builds.v2",
        "source_sha256": build_source_sha256(),
        "images": [
            {
                "component": component,
                "image_digest": "a" * 64,
                "reference": COMPONENT_SOURCES[component],
            }
            for component in sorted(BUILD_COMPONENTS)
        ],
        "status": "passed",
    }


def test_normalizes_content_free_packages_and_findings() -> None:
    packages = normalize_packages(
        {
            "artifacts": [
                {"name": "demo", "version": "1.2.3", "type": "python", "licenses": [{"value": "MIT"}]},
                {"name": "demo", "version": "1.2.3", "type": "python", "licenses": [{"value": "MIT"}]},
            ]
        }
    )
    findings = normalize_findings(
        {
            "matches": [
                {
                    "artifact": {"name": "demo", "version": "1.2.3"},
                    "vulnerability": {"id": "CVE-2026-0001", "severity": "High", "fix": {"state": "fixed"}},
                }
            ]
        }
    )
    assert packages == [{"name": "demo", "version": "1.2.3", "license": "MIT", "origin": "python"}]
    assert findings == [{
        "finding_id": "CVE-2026-0001", "severity": "high", "package": "demo",
        "version": "1.2.3", "fix_state": "fixed",
    }]


def test_exception_must_reference_an_actual_high_finding() -> None:
    with pytest.raises(SupplyReportError, match="vulnerability_exception_finding_missing"):
        normalize_exceptions(
            [{"finding_id": "CVE-missing", "owner": "security", "rationale": "test", "expires_on": "2026-08-01"}],
            [],
        )


@pytest.mark.parametrize(
    ("configured", "reason"),
    [
        (
            [
                {"finding_id": "CVE-2026-0001", "owner": "security", "rationale": "bounded", "expires_on": "never"}
            ],
            "vulnerability_exception_expiry_invalid",
        ),
        (
            [
                {
                    "finding_id": "CVE-2026-0001",
                    "owner": "security",
                    "rationale": "bounded",
                    "expires_on": "2026-08-01",
                },
                {
                    "finding_id": "CVE-2026-0001",
                    "owner": "security",
                    "rationale": "duplicate",
                    "expires_on": "2026-08-02",
                },
            ],
            "vulnerability_exception_duplicate",
        ),
    ],
)
def test_exception_requires_iso_expiry_and_unique_finding(configured, reason: str) -> None:
    with pytest.raises(SupplyReportError, match=reason):
        normalize_exceptions(
            configured,
            [
                {
                    "finding_id": "CVE-2026-0001",
                    "severity": "high",
                    "package": "demo",
                    "version": "1.0.0",
                    "fix_state": "fixed",
                }
            ],
        )


def test_exception_registry_must_cover_turn_and_every_scanned_component(tmp_path) -> None:
    path = tmp_path / "exceptions.json"
    path.write_text(
        '{"schema":"ananta.semantic-media-vulnerability-exceptions.v1","components":{"frontend":[],"hub":[],"reconciliation":[],"sfu":[],"training":[]}}',
        encoding="utf-8",
    )

    with pytest.raises(SupplyReportError, match="vulnerability_exceptions_invalid"):
        _load_exceptions(path)


def test_generator_scans_only_images_bound_to_current_build_manifest(monkeypatch) -> None:
    monkeypatch.setattr(generator, "_image_digest", lambda _source, _timeout: "a" * 64)
    monkeypatch.setattr(
        generator,
        "_run_json",
        lambda command, _timeout, _reason: (
            {
                "artifacts": [
                    {"name": "demo", "version": "1", "type": "python", "licenses": [{"value": "MIT"}]}
                ]
            }
            if command[0] == "syft"
            else {"matches": []}
        ),
    )
    manifest = _build_manifest()
    sbom, scanner = generator.generate(
        COMPONENT_SOURCES,
        build_manifest=manifest,
        syft="syft",
        grype="grype",
        exceptions={},
    )
    assert sbom["schema"] == "ananta.semantic-media-sbom.v2"
    assert scanner["schema"] == "ananta.semantic-media-vulnerability-report.v2"
    assert sbom["source_sha256"] == manifest["source_sha256"]
    assert sbom["build_manifest_sha256"] == scanner["build_manifest_sha256"]


def test_generator_rejects_local_image_digest_drift(monkeypatch) -> None:
    monkeypatch.setattr(generator, "_image_digest", lambda _source, _timeout: "b" * 64)
    with pytest.raises(SupplyReportError, match="supply_image_not_bound_to_build_manifest"):
        generator.generate(
            COMPONENT_SOURCES,
            build_manifest=_build_manifest(),
            syft="syft",
            grype="grype",
            exceptions={},
        )
