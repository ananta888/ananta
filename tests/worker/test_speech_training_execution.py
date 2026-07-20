from __future__ import annotations

from tests.speech_adaptation_support import (
    AlwaysActiveAuthority,
    MemoryArtifactPort,
    SyntheticDatasetResolver,
    speech_job,
)
from worker.speech_training.backend_registry import SpeechTrainingBackendRegistry
from worker.speech_training.backends import MockSpeechTrainingBackend
from worker.speech_training.result_publisher import SpeechResultPublisher
from worker.speech_training.runner import ResourceUsage, SpeechTrainingRunner


class _OverBudgetProbe:
    def sample(self, roots):
        del roots
        return ResourceUsage(ram_bytes=10**15, vram_bytes=0, disk_bytes=0)


def _runner(tmp_path, *, authority=None, resource_probe=None, port=None):
    artifact_port = port or MemoryArtifactPort()
    return (
        SpeechTrainingRunner(
            registry=SpeechTrainingBackendRegistry([MockSpeechTrainingBackend()]),
            authority=authority or AlwaysActiveAuthority(),
            dataset_resolver=SyntheticDatasetResolver(tmp_path / "dataset"),
            result_publisher=SpeechResultPublisher(artifact_port, root=tmp_path),
            workspace_root=tmp_path,
            model_root=tmp_path / "models",
            resource_probe=resource_probe,
            clock_ms=lambda: 1_000_001,
        ),
        artifact_port,
    )


def test_authority_is_checked_before_dataset_open_and_every_publish(tmp_path) -> None:
    denied = AlwaysActiveAuthority(fail_phase="before_audio_access", reason_code="speech_consent_revoked")
    resolver = SyntheticDatasetResolver(tmp_path / "dataset")
    port = MemoryArtifactPort()
    runner = SpeechTrainingRunner(
        registry=SpeechTrainingBackendRegistry([MockSpeechTrainingBackend()]),
        authority=denied,
        dataset_resolver=resolver,
        result_publisher=SpeechResultPublisher(port, root=tmp_path),
        workspace_root=tmp_path,
        model_root=tmp_path / "models",
        clock_ms=lambda: 1_000_001,
    )
    result = runner.run(speech_job())
    assert result.status == "cancelled"
    assert result.reason_code == "speech_consent_revoked"
    assert resolver.opened == 0
    assert port.publications == []

    allowed = AlwaysActiveAuthority()
    runner, port = _runner(tmp_path / "allowed", authority=allowed)
    assert runner.run(speech_job()).status == "completed"
    assert allowed.phases == [
        "before_audio_access",
        "after_dataset_open",
        "before_checkpoint",
        "before_checkpoint_publish",
        "before_evaluation_publish",
        "before_artifact_export",
        "before_artifact_publish",
        "after_artifact_publish",
    ]
    assert len(port.publications) == 3


def test_resource_pressure_and_hub_publish_failure_are_terminal_without_artifact(tmp_path) -> None:
    runner, port = _runner(tmp_path / "budget", resource_probe=_OverBudgetProbe())
    result = runner.run(speech_job())
    assert result.status == "cancelled"
    assert result.reason_code == "speech_ram_budget_exceeded"
    assert port.publications == []

    runner, port = _runner(tmp_path / "publish", port=MemoryArtifactPort(fail=True))
    result = runner.run(speech_job())
    assert result.status == "failed"
    assert result.reason_code == "speech_internal_failure"
    assert port.publications == []
