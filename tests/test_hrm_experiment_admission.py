from __future__ import annotations

from copy import deepcopy

import pytest

from agent.services.hrm_experiments.admission import (
    HrmAdmissionError,
    HrmAdmissionScope,
    HrmArtifactInspection,
    HrmManifestAdmissionService,
)


class _Artifacts:
    def __init__(self, inspection: HrmArtifactInspection) -> None:
        self.inspection = inspection

    def inspect_locator(self, _locator: str) -> HrmArtifactInspection:
        return self.inspection

    def inspect_digest(self, _content_digest: str) -> HrmArtifactInspection:
        return self.inspection


class _Repository:
    def save_dataset(self, manifest):
        return deepcopy(dict(manifest))

    def save_checkpoint(self, manifest):
        return deepcopy(dict(manifest))


def _scope() -> dict:
    return {"tenant_id": "tenant-a", "project_id": "project-a"}


def _dataset() -> dict:
    return {
        "schema": "ananta.hrm-experiments.puzzle-dataset.v1",
        "dataset_id": "dataset-sudoku-1",
        "scope": _scope(),
        "puzzle_type": "sudoku",
        "source": {
            "kind": "fixture",
            "locator": "fixture:sudoku-contract-smoke-v1",
            "version": "v1",
            "digest": "a" * 64,
            "license_spdx": "MIT",
        },
        "split": "contract-smoke",
        "record_count": 1,
        "dimensions": {"max_rows": 9, "max_columns": 9, "max_elements": 81},
        "canonical_content_digest": "b" * 64,
        "generator_parameters": {"seed": 7, "augmentation_count": 0},
        "codec_version": "sudoku-codec-v1",
        "normalizer_version": "sudoku-normalizer-v1",
        "validator_version": "sudoku-validator-v1",
        "plugin": {
            "plugin_id": "sudoku",
            "version": "v1",
            "digest": "c" * 64,
            "signature_verified": True,
        },
        "provenance": {
            "imported_at": "2026-08-22T12:00:00Z",
            "importer_version": "hrm-importer-v1",
            "policy_digest": "d" * 64,
        },
    }


def _checkpoint() -> dict:
    return {
        "schema": "ananta.hrm-experiments.checkpoint.v1",
        "checkpoint_id": "checkpoint-1",
        "scope": _scope(),
        "state": "quarantined",
        "origin": "imported",
        "architecture_id": "hrm-v1",
        "engine_version": "hrm-runner-v1",
        "code_digest": "1" * 64,
        "dataset_digest": "2" * 64,
        "run_digest": "3" * 64,
        "format": "safetensors",
        "size_bytes": 4096,
        "content_digest": "4" * 64,
        "shape_digest": "5" * 64,
        "dtype_allowlist": ["float32"],
        "license_spdx": "MIT",
        "compatibility": {
            "dataset_types": ["sudoku"],
            "runtime_digest": "6" * 64,
            "verified": False,
        },
    }


def test_dataset_admission_requires_verified_canonical_artifact():
    service = HrmManifestAdmissionService(
        artifacts=_Artifacts(
            HrmArtifactInspection(
                content_digest="a" * 64,
                canonical_content_digest="b" * 64,
                size_bytes=128,
                media_type="application/json",
                verified=True,
            )
        ),
        repository=_Repository(),
    )

    admitted = service.admit_dataset(
        _dataset(),
        scope=HrmAdmissionScope("tenant-a", "project-a"),
    )

    assert admitted["dataset_id"] == "dataset-sudoku-1"


def test_dataset_admission_rejects_direct_paths_and_scope_confusion():
    inspection = HrmArtifactInspection(
        content_digest="a" * 64,
        canonical_content_digest="b" * 64,
        size_bytes=128,
        media_type="application/json",
        verified=True,
    )
    service = HrmManifestAdmissionService(
        artifacts=_Artifacts(inspection), repository=_Repository()
    )
    direct = _dataset()
    direct["source"]["locator"] = "/tmp/puzzles.json"

    with pytest.raises(HrmAdmissionError, match="hrm.dataset_locator_forbidden"):
        service.admit_dataset(
            direct, scope=HrmAdmissionScope("tenant-a", "project-a")
        )
    with pytest.raises(HrmAdmissionError, match="hrm.scope_mismatch"):
        service.admit_dataset(
            _dataset(), scope=HrmAdmissionScope("tenant-b", "project-a")
        )


def test_checkpoint_admission_promotes_only_inspected_safetensors():
    service = HrmManifestAdmissionService(
        artifacts=_Artifacts(
            HrmArtifactInspection(
                content_digest="4" * 64,
                size_bytes=4096,
                media_type="application/vnd.safetensors",
                verified=True,
                shape_digest="5" * 64,
                dtypes=("float32",),
                format_name="safetensors",
            )
        ),
        repository=_Repository(),
    )

    admitted = service.admit_checkpoint(
        _checkpoint(),
        scope=HrmAdmissionScope("tenant-a", "project-a"),
        expected_runtime_digest="6" * 64,
    )

    assert admitted["state"] == "verified"
    assert admitted["compatibility"]["verified"] is True


def test_checkpoint_admission_rejects_unverified_or_wrong_dtype():
    service = HrmManifestAdmissionService(
        artifacts=_Artifacts(
            HrmArtifactInspection(
                content_digest="4" * 64,
                size_bytes=4096,
                media_type="application/vnd.safetensors",
                verified=True,
                shape_digest="5" * 64,
                dtypes=("float16",),
                format_name="safetensors",
            )
        ),
        repository=_Repository(),
    )

    with pytest.raises(HrmAdmissionError, match="hrm.checkpoint_dtype_forbidden"):
        service.admit_checkpoint(
            _checkpoint(),
            scope=HrmAdmissionScope("tenant-a", "project-a"),
            expected_runtime_digest="6" * 64,
        )
