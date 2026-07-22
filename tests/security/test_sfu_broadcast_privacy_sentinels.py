from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest

from agent.services.sfu_broadcast_privacy_sentinel import (
    MANIFEST_SCHEMA,
    REQUIRED_SURFACES,
    SENTINEL_CATEGORIES,
    SENTINEL_SCHEMA,
    SentinelSet,
    SfuBroadcastPrivacyScanConfigurationError,
    configuration_failure_report,
    scan_sfu_broadcast_privacy_surfaces,
)

SURFACE_FORMATS = {
    "hub": "json_log",
    "sfu": "text_log",
    "turn": "text_log",
    "browser": "browser_json",
    "metrics": "openmetrics",
    "trace": "otel_json",
    "crashdump": "binary_dump",
    "test_artifact": "artifact_json",
}


def _sentinel_document() -> dict:
    return {
        "schema": SENTINEL_SCHEMA,
        "sentinels": {
            "media": "MEDIA-CANARY-7f1b83ad",
            "transcript": "TRANSCRIPT-CANARY-92ac07de",
            "semantic": "SEMANTIC-CANARY-00d91cc4",
            "evidence": "EVIDENCE-CANARY-18ef72a1",
            "key": "KEY-CANARY-25cae300",
            "token": "TOKEN-CANARY-3a411df0",
            "credential": "CREDENTIAL-CANARY-4bd8e99c",
            "private_ip": "10.203.44.71",
            "sdp_ice": "ICE-CANDIDATE-CANARY-5ce8100b",
        },
    }


def _scan_case(tmp_path: Path, *, file_size_max: int = 4096, bytes_scanned_max: int = 32768):
    sources = []
    for surface in sorted(REQUIRED_SURFACES):
        relative = Path("run") / f"{surface}.scan"
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"clean-{surface}".encode("ascii"))
        sources.append(
            {
                "surface": surface,
                "path": relative.as_posix(),
                "format": SURFACE_FORMATS[surface],
                "required": True,
            }
        )
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "policy_version": "1.0",
        "limits": {
            "file_size_max": file_size_max,
            "bytes_scanned_max": bytes_scanned_max,
        },
        "required_surfaces": sorted(REQUIRED_SURFACES),
        "sources": sources,
    }
    return manifest, sources


@pytest.mark.parametrize("category", sorted(SENTINEL_CATEGORIES))
def test_positive_fixture_per_category_reports_only_path_category_and_reason(tmp_path: Path, category: str):
    manifest, sources = _scan_case(tmp_path)
    sentinel_document = _sentinel_document()
    source = sources[sorted(SENTINEL_CATEGORIES).index(category) % len(sources)]
    leaked = sentinel_document["sentinels"][category]
    (tmp_path / source["path"]).write_bytes(b"prefix:" + leaked.encode("utf-8") + b":suffix")

    report = scan_sfu_broadcast_privacy_surfaces(
        root=tmp_path,
        manifest_document=manifest,
        sentinel_document=sentinel_document,
    )

    assert report["decision"] == "block"
    assert report["measurements"]["finding_count"] == 1
    assert report["findings"] == [
        {
            "path": source["path"],
            "surface": source["surface"],
            "category": category,
            "reason_code": f"sfu_privacy_{category}_sentinel_detected",
        }
    ]
    encoded_report = json.dumps(report, sort_keys=True)
    assert leaked not in encoded_report
    assert "prefix:" not in encoded_report
    assert ":suffix" not in encoded_report


def test_clean_all_surface_fixture_allows_only_one_way_control_forms(tmp_path: Path):
    manifest, sources = _scan_case(tmp_path)
    sentinel_document = _sentinel_document()
    controls = []
    for value in sentinel_document["sentinels"].values():
        encoded = value.encode("utf-8")
        controls.append(hashlib.sha256(encoded).hexdigest())
        controls.append(hmac.new(b"unit-only-control-key", encoded, hashlib.sha256).hexdigest()[:24])
    clean_content = "\n".join(controls).encode("ascii")
    for source in sources:
        (tmp_path / source["path"]).write_bytes(clean_content)

    first = scan_sfu_broadcast_privacy_surfaces(
        root=tmp_path,
        manifest_document=manifest,
        sentinel_document=sentinel_document,
    )
    second = scan_sfu_broadcast_privacy_surfaces(
        root=tmp_path,
        manifest_document=manifest,
        sentinel_document=sentinel_document,
    )

    assert first == second
    assert first["decision"] == "allow"
    assert first["reason_codes"] == ["sfu_privacy_scan_clean"]
    assert first["measurements"]["files_scanned"] == len(REQUIRED_SURFACES)
    assert first["findings"] == []


def test_reversible_base64_form_is_not_allowlisted(tmp_path: Path):
    manifest, sources = _scan_case(tmp_path)
    sentinel_document = _sentinel_document()
    token = sentinel_document["sentinels"]["token"].encode("utf-8")
    (tmp_path / sources[0]["path"]).write_bytes(base64.b64encode(token))

    report = scan_sfu_broadcast_privacy_surfaces(
        root=tmp_path,
        manifest_document=manifest,
        sentinel_document=sentinel_document,
    )

    assert report["decision"] == "block"
    assert report["findings"][0]["category"] == "token"
    assert sentinel_document["sentinels"]["token"] not in json.dumps(report, sort_keys=True)


@pytest.mark.parametrize(
    ("failure", "expected_reason"),
    [
        ("missing", "sfu_privacy_required_source_unreadable"),
        ("unsupported", "sfu_privacy_source_unsupported"),
        ("truncated", "sfu_privacy_source_truncated"),
        ("global_budget", "sfu_privacy_scan_bytes_exceeded"),
    ],
)
def test_unreadable_unsupported_and_truncated_sources_fail_closed(
    tmp_path: Path,
    failure: str,
    expected_reason: str,
):
    file_size_max = 64 if failure == "truncated" else 4096
    bytes_scanned_max = 20 if failure == "global_budget" else 32768
    manifest, sources = _scan_case(
        tmp_path,
        file_size_max=file_size_max,
        bytes_scanned_max=bytes_scanned_max,
    )
    target = tmp_path / sources[0]["path"]
    if failure == "missing":
        target.unlink()
    elif failure == "unsupported":
        target.unlink()
        target.mkdir()
    elif failure == "truncated":
        target.write_bytes(b"clean" * 100)

    report = scan_sfu_broadcast_privacy_surfaces(
        root=tmp_path,
        manifest_document=manifest,
        sentinel_document=_sentinel_document(),
    )

    assert report["decision"] == "block"
    assert expected_reason in report["reason_codes"]
    assert report["measurements"]["bytes_scanned"] <= bytes_scanned_max
    assert all(result["bytes_scanned"] <= file_size_max for result in report["sources"])


def test_ambiguous_sentinels_and_path_traversal_are_rejected_without_input_echo(tmp_path: Path):
    sentinels = _sentinel_document()
    sentinels["sentinels"]["media"] = sentinels["sentinels"]["token"]
    with pytest.raises(SfuBroadcastPrivacyScanConfigurationError, match="sfu_privacy_sentinel_ambiguous"):
        SentinelSet.from_mapping(sentinels)

    manifest, _sources = _scan_case(tmp_path)
    manifest["sources"][0]["path"] = "../outside.log"
    with pytest.raises(SfuBroadcastPrivacyScanConfigurationError, match="sfu_privacy_source_path_invalid"):
        scan_sfu_broadcast_privacy_surfaces(
            root=tmp_path,
            manifest_document=manifest,
            sentinel_document=_sentinel_document(),
        )


def test_configuration_failure_report_is_deterministic_content_free_and_blocking():
    first = configuration_failure_report("sfu_privacy_input_document_unavailable")
    second = configuration_failure_report("sfu_privacy_input_document_unavailable")
    assert first == second
    assert first["decision"] == "block"
    assert first["findings"] == []
    assert first["reason_codes"] == ["sfu_privacy_input_document_unavailable"]
