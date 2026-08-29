from __future__ import annotations

import pytest

from agent.services.dendritic_memory_artifact_service import DendriticMemoryArtifactService
from tests.dendritic_memory.helpers import pack, spec
from worker.training.dendritic.backend import MockDendriticExperimentBackend
from worker.training.dendritic.runner import DendriticMemoryJobRunner


def test_artifact_store_rejects_tampering_and_is_idempotent(tmp_path) -> None:
    manifest, files = pack()
    store = DendriticMemoryArtifactService(tmp_path / "packs", max_pack_bytes=1024)
    first = store.put(manifest=manifest, files=files)
    second = store.put(manifest=manifest, files=files)
    assert first == second
    with pytest.raises(ValueError, match="digest_mismatch"):
        store.put(manifest=manifest, files={"weights.safetensors": b"tampered"})


def test_executable_pack_requires_structurally_valid_safetensors(tmp_path) -> None:
    manifest, files = pack(executable=True)
    store = DendriticMemoryArtifactService(tmp_path / "packs", max_pack_bytes=1024)
    with pytest.raises(ValueError, match="safetensors_invalid"):
        store.put(manifest=manifest, files=files)


def test_mock_runner_is_deterministic_and_never_orchestrates(tmp_path) -> None:
    store = DendriticMemoryArtifactService(tmp_path / "packs", max_pack_bytes=1024 * 1024)
    runner = DendriticMemoryJobRunner(MockDendriticExperimentBackend(), store, authorization_verifier=lambda _job: True)
    job = {"tenant_id": "tenant-1", "run_id": "run-1", "spec": spec().to_dict()}
    first = runner.run(job=job, records=[{"input": "a"}, {"input": "b"}, {"input": "c"}])
    second = runner.run(job=job, records=[{"input": "a"}, {"input": "b"}, {"input": "c"}])
    assert first["state"] == "completed"
    assert first["manifest"] == second["manifest"]
    assert first["hub_task_created"] is False
    assert first["worker_delegation_performed"] is False


def test_runner_cancellation_is_terminal_and_headless(tmp_path) -> None:
    store = DendriticMemoryArtifactService(tmp_path / "packs", max_pack_bytes=1024 * 1024)
    runner = DendriticMemoryJobRunner(MockDendriticExperimentBackend(), store, authorization_verifier=lambda _job: True)
    result = runner.run(
        job={"tenant_id": "tenant-1", "run_id": "run-1", "spec": spec().to_dict()},
        records=[],
        cancelled=lambda: True,
    )
    assert result == {
        "state": "cancelled",
        "reason_code": "dendritic_worker_cancelled",
        "hub_task_created": False,
        "worker_delegation_performed": False,
        "human_intervention_required": False,
    }
