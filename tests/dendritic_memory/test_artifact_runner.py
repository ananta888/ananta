from __future__ import annotations

import pytest

from agent.services.dendritic_memory_artifact_service import DendriticMemoryArtifactService
from tests.dendritic_memory.helpers import assignment, pack
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
    deleted = store.delete(manifest=manifest)
    assert deleted["removed_files"] == 2
    assert deleted["human_intervention_required"] is False


def test_executable_pack_requires_structurally_valid_safetensors(tmp_path) -> None:
    manifest, files = pack(executable=True)
    store = DendriticMemoryArtifactService(tmp_path / "packs", max_pack_bytes=1024)
    with pytest.raises(ValueError, match="safetensors_invalid"):
        store.put(manifest=manifest, files=files)


def test_mock_runner_is_deterministic_and_never_orchestrates(tmp_path) -> None:
    store = DendriticMemoryArtifactService(tmp_path / "packs", max_pack_bytes=1024 * 1024)
    runner = DendriticMemoryJobRunner(MockDendriticExperimentBackend(), store, authorization_verifier=lambda _job: True)
    job = assignment()
    first = runner.run(job=job, records=[{"input": "a"}, {"input": "b"}, {"input": "c"}])
    second = runner.run(job=job, records=[{"input": "a"}, {"input": "b"}, {"input": "c"}])
    assert first["state"] == "completed"
    assert first["manifest"] == second["manifest"]
    assert first["schema"] == "ananta.dendritic-memory-worker-result.v1"
    assert first["checkpoint"]["fencing_token"] == 1


def test_runner_cancellation_is_terminal_and_headless(tmp_path) -> None:
    store = DendriticMemoryArtifactService(tmp_path / "packs", max_pack_bytes=1024 * 1024)
    runner = DendriticMemoryJobRunner(MockDendriticExperimentBackend(), store, authorization_verifier=lambda _job: True)
    result = runner.run(
        job=assignment(),
        records=[],
        cancelled=lambda: True,
    )
    assert result["state"] == "cancelled"
    assert result["reason_code"] == "dendritic_worker_cancelled"
    assert result["artifact"] is None


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        ("timeout", "dendritic_worker_timeout"),
        ("retry", "dendritic_worker_retryable_failure"),
        ("corrupt", "dendritic_artifact_digest_mismatch"),
    ],
)
def test_mock_backend_simulates_bounded_failure_modes(tmp_path, scenario: str, reason: str) -> None:
    store = DendriticMemoryArtifactService(tmp_path / scenario, max_pack_bytes=1024 * 1024)
    runner = DendriticMemoryJobRunner(
        MockDendriticExperimentBackend(scenario=scenario),
        store,
        authorization_verifier=lambda _job: True,
    )
    result = runner.run(job=assignment(), records=[{"input": "a"}, {"input": "b"}, {"input": "c"}])
    assert result["state"] == "failed"
    assert result["reason_code"] == reason
