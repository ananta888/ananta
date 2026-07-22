from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent.services.release_evidence_provenance_service import (
    MANIFEST_SCHEMA,
    ReasonCode,
    VerificationContext,
    sha256_bytes,
    verify_evidence_manifest,
)
from scripts.verify_sfu_broadcast_evidence import RepositoryArtifactReader

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
SOURCE_DIGEST = "a" * 40
ARTIFACT_SCHEMA = "ananta.test-sfu-evidence.v1"
GATE_ID = "SFB-TEST-001"
DIGESTS = {
    "config": {"gate-config": "1" * 64},
    "lockfile": {"python-lock": "2" * 64},
    "image": {"hub-image": "3" * 64},
    "infrastructure": {"sfu-infra": "4" * 64},
}


def _artifact(schema: str = ARTIFACT_SCHEMA, status: str = "passed") -> bytes:
    return (json.dumps({"schema": schema, "status": status}, sort_keys=True) + "\n").encode()


def _write_artifact(root: Path, name: str = "evidence.json", content: bytes | None = None) -> tuple[str, bytes]:
    relative = f"artifacts/test-gates/{name}"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    value = content or _artifact()
    path.write_bytes(value)
    return relative, value


def _gate(gate_id: str = GATE_ID) -> dict:
    return {
        "gate_id": gate_id,
        "artifact_schemas": [ARTIFACT_SCHEMA],
        "required_digest_ids": {category: sorted(values) for category, values in DIGESTS.items()},
        "attestation_profile": None,
    }


def _configuration(*gates: dict) -> dict:
    return {
        "schema": "ananta.sfu-broadcast-gate-manifest.v1",
        "evidence_manifest_schema": MANIFEST_SCHEMA,
        "artifact_root": "artifacts/test-gates",
        "default_policy": {"max_age_seconds": 86400, "max_future_skew_seconds": 300},
        "attestation_profiles": {},
        "gates": list(gates or (_gate(),)),
    }


def _bindings(category: str) -> list[dict[str, str]]:
    return [{"id": key, "sha256": value} for key, value in sorted(DIGESTS[category].items())]


def _entry(path: str, content: bytes, gate_id: str = GATE_ID) -> dict:
    return {
        "gate_id": gate_id,
        "artifact_schema": ARTIFACT_SCHEMA,
        "artifact_path": path,
        "artifact_sha256": sha256_bytes(content),
        "git_source_digest": SOURCE_DIGEST,
        "config_digests": _bindings("config"),
        "lockfile_digests": _bindings("lockfile"),
        "image_digests": _bindings("image"),
        "infrastructure_digests": _bindings("infrastructure"),
        "producer_command": "python scripts/run_test_gate.py",
        "freshness": {"produced_at": "2026-01-15T11:00:00Z", "expires_at": "2026-01-16T11:00:00Z"},
        "status": "passed",
    }


def _context(source_digest: str = SOURCE_DIGEST) -> VerificationContext:
    return VerificationContext(
        git_source_digest=source_digest,
        config_digests=DIGESTS["config"],
        lockfile_digests=DIGESTS["lockfile"],
        image_digests=DIGESTS["image"],
        infrastructure_digests=DIGESTS["infrastructure"],
    )


def _verify(root: Path, entries: list[dict], configuration: dict | None = None):
    return verify_evidence_manifest(
        {"schema": MANIFEST_SCHEMA, "entries": entries},
        gate_configuration=configuration or _configuration(),
        context=_context(),
        artifact_reader=RepositoryArtifactReader(root, "artifacts/test-gates"),
        now=NOW,
    )


def test_accepts_fresh_source_bound_artifact_with_all_digest_classes(tmp_path: Path) -> None:
    path, content = _write_artifact(tmp_path)

    report = _verify(tmp_path, [_entry(path, content)])

    assert report.status == "passed"
    assert report.reason_codes == ()


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("artifacts/test-gates/../../secret.json", ReasonCode.ARTIFACT_PATH_INVALID.value),
        ("other/evidence.json", ReasonCode.ARTIFACT_PATH_OUTSIDE_ROOT.value),
    ],
)
def test_rejects_path_traversal_and_paths_outside_artifact_root(tmp_path: Path, path: str, reason: str) -> None:
    _, content = _write_artifact(tmp_path)

    report = _verify(tmp_path, [_entry(path, content)])

    assert report.status == "failed"
    assert reason in report.reason_codes


def test_rejects_symlink_without_following_it(tmp_path: Path) -> None:
    path, content = _write_artifact(tmp_path, "real.json")
    link = tmp_path / "artifacts/test-gates/link.json"
    link.symlink_to(Path(path).name)

    report = _verify(tmp_path, [_entry("artifacts/test-gates/link.json", content)])

    assert ReasonCode.ARTIFACT_SYMLINK_FORBIDDEN.value in report.reason_codes


def test_rejects_artifact_hash_change(tmp_path: Path) -> None:
    path, original = _write_artifact(tmp_path)
    entry = _entry(path, original)
    (tmp_path / path).write_bytes(_artifact(status="failed"))

    report = _verify(tmp_path, [entry])

    assert ReasonCode.ARTIFACT_DIGEST_MISMATCH.value in report.reason_codes


def test_rejects_git_source_mismatch(tmp_path: Path) -> None:
    path, content = _write_artifact(tmp_path)
    manifest = {"schema": MANIFEST_SCHEMA, "entries": [_entry(path, content)]}

    report = verify_evidence_manifest(
        manifest,
        gate_configuration=_configuration(),
        context=_context("b" * 40),
        artifact_reader=RepositoryArtifactReader(tmp_path, "artifacts/test-gates"),
        now=NOW,
    )

    assert ReasonCode.GIT_SOURCE_DIGEST_MISMATCH.value in report.reason_codes


def test_rejects_clock_skew(tmp_path: Path) -> None:
    path, content = _write_artifact(tmp_path)
    entry = _entry(path, content)
    entry["freshness"] = {"produced_at": "2026-01-15T12:05:01Z", "expires_at": "2026-01-16T12:05:01Z"}

    report = _verify(tmp_path, [entry])

    assert ReasonCode.EVIDENCE_CLOCK_SKEW.value in report.reason_codes


def test_rejects_unknown_gate_and_duplicate_artifact_path(tmp_path: Path) -> None:
    path, content = _write_artifact(tmp_path)
    first = _entry(path, content, gate_id="SFB-UNKNOWN")
    second = copy.deepcopy(first)

    report = _verify(tmp_path, [first, second])

    assert ReasonCode.GATE_UNKNOWN.value in report.reason_codes
    assert ReasonCode.DUPLICATE_ARTIFACT_PATH.value in report.reason_codes


@pytest.mark.parametrize("status", ["partial", "failed", "unverified"])
def test_rejects_non_passing_statuses(tmp_path: Path, status: str) -> None:
    path, content = _write_artifact(tmp_path)
    entry = _entry(path, content)
    entry["status"] = status

    report = _verify(tmp_path, [entry])

    assert ReasonCode.EVIDENCE_STATUS_NOT_PASSED.value in report.reason_codes


def test_report_output_is_reproducibly_sorted(tmp_path: Path) -> None:
    path_b, content_b = _write_artifact(tmp_path, "b.json")
    path_a, content_a = _write_artifact(tmp_path, "a.json")
    gate_a = _gate("SFB-A")
    gate_b = _gate("SFB-B")
    entries = [_entry(path_b, content_b, "SFB-B"), _entry(path_a, content_a, "SFB-A")]

    report = _verify(tmp_path, entries, _configuration(gate_b, gate_a))
    first = json.dumps(report.as_document(), sort_keys=True, separators=(",", ":"))
    second = json.dumps(report.as_document(), sort_keys=True, separators=(",", ":"))

    assert first == second
    assert [entry["gate_id"] for entry in report.as_document()["entries"]] == ["SFB-A", "SFB-B"]


def test_attestation_is_required_only_for_a_configured_profile(tmp_path: Path) -> None:
    path, content = _write_artifact(tmp_path)
    configuration = _configuration()
    configuration["attestation_profiles"] = {
        "release-ed25519": {
            "algorithm": "ed25519",
            "trusted_keys": [
                {
                    "key_id": "release-key",
                    "public_key_path": "config/release/keys/release.pem",
                    "public_key_sha256": "f" * 64,
                }
            ],
        }
    }
    configuration["gates"][0]["attestation_profile"] = "release-ed25519"

    required = _verify(tmp_path, [_entry(path, content)], configuration)
    optional = _verify(tmp_path, [_entry(path, content)])

    assert ReasonCode.ATTESTATION_REQUIRED.value in required.reason_codes
    assert optional.status == "passed"
