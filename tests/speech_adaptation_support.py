from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ananta_contracts.speech_adaptation import (
    CONTRACT_VERSION,
    TRAIN_JOB_TYPE,
    SpeechAdaptationJob,
    canonical_sha256,
    speech_attempt_digest,
    speech_budget_digest,
    speech_configuration_digest,
    speech_fencing_digest,
    speech_job_binding_digest,
    speech_scope_digest,
)
from worker.speech_training.backend import SpeechDatasetView
from worker.speech_training.result_publisher import PublicationReceipt


def digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def speech_job_payload(
    *,
    now_ms: int = 1_000_000,
    scenario: str = "success",
    max_steps: int = 3,
    job_id: str = "speech-job-test",
    artifact_id: str = "speech-adapter-test",
) -> dict[str, Any]:
    pair_id = "pair-test"
    direction = "sender_to_receiver"
    speaker_digest = digest("speaker")
    scope_digest = speech_scope_digest(
        pair_id=pair_id,
        direction=direction,
        speaker_digest=speaker_digest,
    )
    configuration: dict[str, Any] = {
        "backend": "mock",
        "backend_digest": digest("mock-backend-v1"),
        "seed": 7,
        "max_steps": max_steps,
        "batch_size": 1,
        "checkpoint_interval_steps": min(2, max_steps),
        "learning_rate": 0.001,
        "scenario": scenario,
    }
    configuration["config_digest"] = speech_configuration_digest(configuration)
    budget: dict[str, Any] = {
        "max_wall_seconds": 30,
        "max_ram_bytes": 8 * 1024**3,
        "max_vram_bytes": 0,
        "max_disk_bytes": 64 * 1024**2,
        "max_artifact_bytes": 1024 * 1024,
        "max_checkpoints": 4,
        "max_events": 100,
    }
    budget["budget_digest"] = speech_budget_digest(budget)
    attempt_id = "speech-attempt-test"
    attempt_digest = speech_attempt_digest(job_id=job_id, attempt_id=attempt_id, attempt_number=1)
    lease_id = "speech-lease-test"
    lease_expires_at_ms = now_ms + 50_000
    fencing_digest = speech_fencing_digest(
        attempt_id=attempt_id,
        epoch=1,
        lease_id=lease_id,
        lease_expires_at_ms=lease_expires_at_ms,
    )
    artifact_ref = f"artifact://speech-adapters/test/{artifact_id}"
    target_digest = canonical_sha256({"artifact_ref": artifact_ref, "target_id": artifact_id})
    bindings = {
        "artifact_target_digest": target_digest,
        "attempt_digest": attempt_digest,
        "budget_digest": budget["budget_digest"],
        "config_digest": configuration["config_digest"],
        "consent_digest": digest("consent"),
        "dataset_digest": digest("dataset"),
        "fencing_digest": fencing_digest,
        "lineage_digest": digest("lineage"),
        "model_digest": digest("model"),
        "scope_digest": scope_digest,
        "split_digest": digest("split"),
    }
    return {
        "contract_version": CONTRACT_VERSION,
        "job_type": TRAIN_JOB_TYPE,
        "job_id": job_id,
        "dataset": {
            "dataset_id": "speech-dataset-test",
            "dataset_version": "v1",
            "storage_ref": "artifact://speech-datasets/test/v1",
            "dataset_digest": bindings["dataset_digest"],
            "split_digest": bindings["split_digest"],
            "lineage_digest": bindings["lineage_digest"],
            "train_sample_count": 4,
            "validation_sample_count": 2,
            "immutable": True,
        },
        "base_model": {
            "model_id": "openvoice-v2-test",
            "artifact_ref": "artifact://speech-models/openvoice-v2-test",
            "model_digest": bindings["model_digest"],
        },
        "scope": {
            "pair_id": pair_id,
            "direction": direction,
            "speaker_digest": speaker_digest,
            "scope_digest": scope_digest,
        },
        "consent": {
            "consent_id": "speech-consent-test",
            "consent_version": 1,
            "consent_digest": bindings["consent_digest"],
            "scope_digest": scope_digest,
            "purpose": "speech_adaptation_training",
            "granted": True,
            "expires_at_ms": now_ms + 120_000,
            "export_allowed": False,
        },
        "configuration": configuration,
        "budget": budget,
        "attempt": {
            "attempt_id": attempt_id,
            "attempt_number": 1,
            "attempt_digest": attempt_digest,
        },
        "fencing": {
            "lease_id": lease_id,
            "epoch": 1,
            "lease_expires_at_ms": lease_expires_at_ms,
            "fencing_digest": fencing_digest,
        },
        "artifact_target": {
            "target_id": artifact_id,
            "artifact_ref": artifact_ref,
            "target_digest": target_digest,
        },
        "deadline_at_ms": now_ms + 60_000,
        "binding_digest": speech_job_binding_digest(bindings),
        "resume": None,
    }


def speech_job(**values: Any) -> SpeechAdaptationJob:
    now_ms = int(values.get("now_ms", 1_000_000))
    return SpeechAdaptationJob.from_mapping(speech_job_payload(**values), now_ms=now_ms)


class AlwaysActiveAuthority:
    def __init__(self, fail_phase: str | None = None, reason_code: str = "speech_lease_lost") -> None:
        self.fail_phase = fail_phase
        self.reason_code = reason_code
        self.phases: list[str] = []

    def verify(self, job: SpeechAdaptationJob, *, phase: str) -> tuple[bool, str | None]:
        del job
        self.phases.append(phase)
        return (phase != self.fail_phase, self.reason_code if phase == self.fail_phase else None)


class SyntheticDatasetResolver:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.opened = 0

    def open_admitted(self, job: SpeechAdaptationJob) -> SpeechDatasetView:
        self.opened += 1
        self.root.mkdir(parents=True, exist_ok=True)
        return SpeechDatasetView(
            root=self.root,
            dataset_digest=job.dataset.dataset_digest,
            split_digest=job.dataset.split_digest,
            train_sample_count=job.dataset.train_sample_count,
            validation_sample_count=job.dataset.validation_sample_count,
        )


class MemoryArtifactPort:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.publications: list[dict[str, Any]] = []

    def publish(self, **values: Any) -> PublicationReceipt:
        stream = values.pop("stream")
        content = stream.read()
        if self.fail:
            raise RuntimeError("Hub publication unavailable")
        assert hashlib.sha256(content).hexdigest() == values["sha256"]
        self.publications.append({**values, "content": content})
        return PublicationReceipt(
            artifact_id=values["target_id"],
            artifact_ref=values["target_ref"],
            sha256=values["sha256"],
            size_bytes=values["size_bytes"],
        )
