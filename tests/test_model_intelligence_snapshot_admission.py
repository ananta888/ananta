from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from agent.services.model_intelligence_snapshot_admission import (
    AnalysisSnapshotAdmissionError,
    AnalysisSnapshotAdmissionPolicy,
    ModelAnalysisSnapshotAdmission,
)
from agent.services.restricted_inference_contract import (
    RestrictedInferenceOperation,
)
from agent.services.restricted_inference_model_manifest import (
    ENGINE_HUGGINGFACE,
    FORMAT_SAFETENSORS,
    ROLE_CONFIG,
    ROLE_WEIGHTS,
    SOURCE_LOCAL_SNAPSHOT,
    ModelManifestFile,
    RestrictedModelManifest,
)


def _file(path: Path, content: bytes) -> ModelManifestFile:
    path.write_bytes(content)
    role = ROLE_WEIGHTS if path.suffix == ".safetensors" else ROLE_CONFIG
    return ModelManifestFile(
        path.name,
        hashlib.sha256(content).hexdigest(),
        len(content),
        role,
    )


def _manifest(files: tuple[ModelManifestFile, ...]) -> RestrictedModelManifest:
    return RestrictedModelManifest(
        manifest_id="analysis-fixture-v1",
        model_id="fixture/tiny-analysis-model",
        engine=ENGINE_HUGGINGFACE,
        model_format=FORMAT_SAFETENSORS,
        revision="0123456789abcdef0123456789abcdef01234567",
        source_type=SOURCE_LOCAL_SNAPSHOT,
        license_id="Apache-2.0",
        operations=(RestrictedInferenceOperation.CLASSIFY,),
        files=files,
        ram_bytes=16 * 1024 * 1024,
        max_batch_size=1,
        max_sequence_length=16,
    )


def _safe_snapshot(root: Path) -> RestrictedModelManifest:
    root.mkdir()
    return _manifest(
        (
            _file(root / "model.safetensors", b"safe-weights"),
            _file(root / "config.json", b"{}"),
        )
    )


def test_admission_is_deterministic_tenant_bound_and_path_free(
    tmp_path: Path,
) -> None:
    root = tmp_path / "snapshot"
    manifest = _safe_snapshot(root)
    admission = ModelAnalysisSnapshotAdmission()

    first = admission.admit(
        tenant_id="tenant-a",
        snapshot_root=root,
        manifest=manifest,
    )
    repeated = admission.admit(
        tenant_id="tenant-a",
        snapshot_root=root,
        manifest=manifest,
    )
    other_tenant = admission.admit(
        tenant_id="tenant-b",
        snapshot_root=root,
        manifest=manifest,
    )

    assert first.manifest == repeated.manifest
    assert first.manifest.snapshot_digest == other_tenant.manifest.snapshot_digest
    assert first.manifest.admission_id != other_tenant.manifest.admission_id
    assert "root" not in first.manifest.to_dict()
    assert first.manifest.file_count == 2


@pytest.mark.parametrize(
    ("name", "expected_code"),
    (
        ("payload.zip", "analysis_snapshot_archive_forbidden"),
        ("weights.pkl", "analysis_snapshot_pickle_forbidden"),
        ("model.py", "analysis_snapshot_executable_forbidden"),
        ("unclassified.bin", "analysis_snapshot_binary_forbidden"),
    ),
)
def test_admission_rejects_unsafe_unlisted_files_before_parser_use(
    tmp_path: Path,
    name: str,
    expected_code: str,
) -> None:
    root = tmp_path / "snapshot"
    manifest = _safe_snapshot(root)
    (root / name).write_bytes(b"unsafe")

    with pytest.raises(AnalysisSnapshotAdmissionError) as captured:
        ModelAnalysisSnapshotAdmission().admit(
            tenant_id="tenant-a",
            snapshot_root=root,
            manifest=manifest,
        )

    assert captured.value.code == expected_code


def test_admission_enforces_file_count_before_hashing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "snapshot"
    manifest = _safe_snapshot(root)
    admission = ModelAnalysisSnapshotAdmission(
        AnalysisSnapshotAdmissionPolicy(max_files=1)
    )

    with pytest.raises(AnalysisSnapshotAdmissionError) as captured:
        admission.admit(
            tenant_id="tenant-a",
            snapshot_root=root,
            manifest=manifest,
        )

    assert captured.value.code == "analysis_snapshot_file_count_exceeded"


def test_admission_rejects_hardlinked_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "snapshot"
    manifest = _safe_snapshot(root)
    os.link(root / "config.json", root / "linked.json")

    with pytest.raises(AnalysisSnapshotAdmissionError) as captured:
        ModelAnalysisSnapshotAdmission().admit(
            tenant_id="tenant-a",
            snapshot_root=root,
            manifest=manifest,
        )

    assert captured.value.code == "analysis_snapshot_hardlink_forbidden"


def test_admission_rejects_sparse_files_above_expansion_budget(
    tmp_path: Path,
) -> None:
    root = tmp_path / "snapshot"
    manifest = _safe_snapshot(root)
    sparse = root / "sparse.json"
    with sparse.open("wb") as handle:
        handle.seek(1024 * 1024)
        handle.write(b"\0")
    allocated = max(sparse.stat().st_blocks * 512, 1)
    if sparse.stat().st_size / allocated <= 2:
        pytest.skip("filesystem does not expose sparse allocation")

    with pytest.raises(AnalysisSnapshotAdmissionError) as captured:
        ModelAnalysisSnapshotAdmission(
            AnalysisSnapshotAdmissionPolicy(max_expansion_ratio=2)
        ).admit(
            tenant_id="tenant-a",
            snapshot_root=root,
            manifest=manifest,
        )

    assert captured.value.code == "analysis_snapshot_sparse_file_forbidden"
