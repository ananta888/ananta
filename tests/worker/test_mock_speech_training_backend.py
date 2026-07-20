from __future__ import annotations

from ananta_contracts.speech_adaptation import SpeechAdaptationJob
from tests.speech_adaptation_support import (
    AlwaysActiveAuthority,
    MemoryArtifactPort,
    SyntheticDatasetResolver,
    digest,
    speech_job,
    speech_job_payload,
)
from worker.speech_training.backend_registry import SpeechTrainingBackendRegistry
from worker.speech_training.backends import MockSpeechTrainingBackend
from worker.speech_training.result_publisher import SpeechResultPublisher
from worker.speech_training.runner import SpeechTrainingRunner


def _run(root, scenario: str = "success"):
    authority = AlwaysActiveAuthority()
    port = MemoryArtifactPort()
    runner = SpeechTrainingRunner(
        registry=SpeechTrainingBackendRegistry([MockSpeechTrainingBackend()]),
        authority=authority,
        dataset_resolver=SyntheticDatasetResolver(root / "dataset"),
        result_publisher=SpeechResultPublisher(port, root=root),
        workspace_root=root,
        model_root=root / "models",
        clock_ms=lambda: 1_000_001,
    )
    result = runner.run(speech_job(scenario=scenario))
    return result, port, authority


def _adapter_publications(port):
    return [item for item in port.publications if item["media_type"] == "application/vnd.ananta.speech-adapter"]


def test_same_bound_inputs_produce_identical_result_and_adapter_bytes(tmp_path) -> None:
    first, first_port, _ = _run(tmp_path / "first")
    second, second_port, _ = _run(tmp_path / "second")

    assert first.to_dict() == second.to_dict()
    assert first.status == "completed"
    assert [item["content"] for item in first_port.publications] == [
        item["content"] for item in second_port.publications
    ]
    assert first.checkpoint_digest == second.checkpoint_digest
    assert first.evaluation_report_digest == second.evaluation_report_digest
    assert not (tmp_path / "first" / "speech-job-test" / "speech-attempt-test").exists()


def test_mock_dataset_only_and_mandatory_evaluation_failure_are_fail_closed(tmp_path) -> None:
    dataset_only, port, _ = _run(tmp_path / "dataset-only", "dataset_only")
    assert dataset_only.status == "dataset_only"
    assert port.publications == []

    failed, port, _ = _run(tmp_path / "evaluation-fail", "evaluation_fail")
    assert failed.status == "failed"
    assert failed.reason_code == "speech_evaluation_policy_failed"
    assert _adapter_publications(port) == []


def test_mock_cancel_deadline_lease_and_publish_fail_do_not_publish(tmp_path) -> None:
    expected = {
        "cancel": "speech_training_cancelled",
        "deadline": "speech_deadline_expired",
        "lease_lost": "speech_lease_lost",
        "publish_fail": "speech_mock_publish_failed",
    }
    for scenario, reason in expected.items():
        result, port, _ = _run(tmp_path / scenario, scenario)
        assert result.status in {"cancelled", "failed"}
        assert result.reason_code == reason
        assert _adapter_publications(port) == []


def test_mock_resume_requires_and_verifies_staged_checkpoint(tmp_path) -> None:
    payload = speech_job_payload(scenario="checkpoint_resume", max_steps=4)
    payload["resume"] = {
        "checkpoint_ref": "artifact://speech-checkpoints/test/checkpoint",
        "checkpoint_digest": digest("checkpoint"),
        "checkpoint_step": 2,
        "source_attempt_digest": digest("source-attempt"),
        "dataset_digest": payload["dataset"]["dataset_digest"],
        "split_digest": payload["dataset"]["split_digest"],
        "model_digest": payload["base_model"]["model_digest"],
        "scope_digest": payload["scope"]["scope_digest"],
        "config_digest": payload["configuration"]["config_digest"],
    }
    job = SpeechAdaptationJob.from_mapping(payload, now_ms=1_000_000)
    root = tmp_path / "resume"
    checkpoint = (
        root
        / job.job_id
        / job.attempt.attempt_id
        / "checkpoints"
        / "resume"
        / f"{job.resume.checkpoint_digest}.checkpoint"
    )
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"checkpoint")
    authority = AlwaysActiveAuthority()
    port = MemoryArtifactPort()
    runner = SpeechTrainingRunner(
        registry=SpeechTrainingBackendRegistry([MockSpeechTrainingBackend()]),
        authority=authority,
        dataset_resolver=SyntheticDatasetResolver(root / "dataset"),
        result_publisher=SpeechResultPublisher(port, root=root),
        workspace_root=root,
        model_root=root / "models",
        clock_ms=lambda: 1_000_001,
    )
    result = runner.run(job)
    assert result.status == "completed"
    assert len(port.publications) == 3
    assert not (root / job.job_id / job.attempt.attempt_id).exists()
