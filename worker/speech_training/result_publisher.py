"""Worker-to-Hub artifact publication boundary.

The publisher exposes artifact bytes to a Hub-owned port; it never registers,
approves or activates an adapter and never returns a worker-local path.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Protocol

from worker.speech_training.backend import SpeechAdapterArtifact, SpeechCheckpoint, SpeechTrainingBackendError
from worker.speech_training.contracts import (
    SpeechAdaptationJob,
    SpeechArtifactDescriptor,
    canonical_json,
)


@dataclass(frozen=True)
class PublicationReceipt:
    artifact_id: str
    artifact_ref: str
    sha256: str
    size_bytes: int


class HubSpeechArtifactPort(Protocol):
    def publish(
        self,
        *,
        job_id: str,
        attempt_id: str,
        fencing_digest: str,
        binding_digest: str,
        target_id: str,
        target_ref: str,
        sha256: str,
        size_bytes: int,
        media_type: str,
        stream: BinaryIO,
    ) -> PublicationReceipt: ...


class SpeechResultPublisher:
    def __init__(self, port: HubSpeechArtifactPort, *, root: Path) -> None:
        self._port = port
        self._root = root.resolve()

    def publish(self, job: SpeechAdaptationJob, artifact: SpeechAdapterArtifact) -> SpeechArtifactDescriptor:
        path = artifact.path.resolve(strict=True)
        try:
            path.relative_to(self._root)
        except ValueError as exc:
            raise SpeechTrainingBackendError(
                "speech_artifact_boundary_violation",
                "adapter artifact escaped the worker artifact root",
            ) from exc
        size = path.stat().st_size
        if size != artifact.size_bytes or size <= 0 or size > job.budget.max_artifact_bytes:
            raise SpeechTrainingBackendError("speech_artifact_budget_exceeded", "adapter artifact exceeds its budget")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != artifact.sha256:
            raise SpeechTrainingBackendError("speech_artifact_digest_mismatch", "adapter artifact digest changed")
        with path.open("rb") as stream:
            receipt = self._port.publish(
                job_id=job.job_id,
                attempt_id=job.attempt.attempt_id,
                fencing_digest=job.fencing.fencing_digest,
                binding_digest=job.binding_digest,
                target_id=job.artifact_target.target_id,
                target_ref=job.artifact_target.artifact_ref,
                sha256=digest,
                size_bytes=size,
                media_type=artifact.media_type,
                stream=stream,
            )
        expected = (
            job.artifact_target.target_id,
            job.artifact_target.artifact_ref,
            digest,
            size,
        )
        actual = (receipt.artifact_id, receipt.artifact_ref, receipt.sha256, receipt.size_bytes)
        if actual != expected:
            raise SpeechTrainingBackendError(
                "speech_artifact_receipt_mismatch",
                "Hub artifact receipt does not match the fenced publication",
            )
        return SpeechArtifactDescriptor(
            artifact_id=receipt.artifact_id,
            artifact_ref=receipt.artifact_ref,
            sha256=receipt.sha256,
            size_bytes=receipt.size_bytes,
            media_type=artifact.media_type,
        )

    def publish_checkpoint(self, job: SpeechAdaptationJob, checkpoint: SpeechCheckpoint) -> PublicationReceipt:
        path = checkpoint.path.resolve(strict=True)
        try:
            path.relative_to(self._root)
        except ValueError as exc:
            raise SpeechTrainingBackendError(
                "speech_checkpoint_boundary_violation",
                "checkpoint escaped the worker publication root",
            ) from exc
        size = path.stat().st_size
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != checkpoint.sha256 or size <= 0 or size > job.budget.max_disk_bytes:
            raise SpeechTrainingBackendError(
                "speech_checkpoint_publish_binding_mismatch",
                "checkpoint changed before Hub publication",
            )
        target_id = f"speech-checkpoint-{digest[:32]}"
        target_ref = f"artifact://speech-checkpoints/{job.job_id}/{job.attempt.attempt_id}/{digest}"
        with path.open("rb") as stream:
            receipt = self._port.publish(
                job_id=job.job_id,
                attempt_id=job.attempt.attempt_id,
                fencing_digest=job.fencing.fencing_digest,
                binding_digest=job.binding_digest,
                target_id=target_id,
                target_ref=target_ref,
                sha256=digest,
                size_bytes=size,
                media_type="application/vnd.ananta.speech-checkpoint",
                stream=stream,
            )
        if (
            receipt.artifact_id != target_id
            or receipt.artifact_ref != target_ref
            or receipt.sha256 != digest
            or receipt.size_bytes != size
        ):
            raise SpeechTrainingBackendError(
                "speech_checkpoint_receipt_mismatch",
                "Hub checkpoint receipt does not match the fenced publication",
            )
        return receipt

    def publish_evaluation(
        self,
        job: SpeechAdaptationJob,
        report: Mapping[str, Any],
    ) -> PublicationReceipt:
        content = canonical_json(dict(report))
        size = len(content)
        maximum = min(job.budget.max_artifact_bytes, 8 * 1024**2)
        if not 1 <= size <= maximum:
            raise SpeechTrainingBackendError(
                "speech_evaluation_report_budget_exceeded",
                "evaluation report exceeds its publication budget",
            )
        digest = hashlib.sha256(content).hexdigest()
        target_id = f"speech-evaluation-{digest[:32]}"
        target_ref = f"artifact://speech-evaluations/{job.job_id}/{job.attempt.attempt_id}/{digest}"
        receipt = self._port.publish(
            job_id=job.job_id,
            attempt_id=job.attempt.attempt_id,
            fencing_digest=job.fencing.fencing_digest,
            binding_digest=job.binding_digest,
            target_id=target_id,
            target_ref=target_ref,
            sha256=digest,
            size_bytes=size,
            media_type="application/vnd.ananta.speech-evaluation+json",
            stream=io.BytesIO(content),
        )
        if (
            receipt.artifact_id != target_id
            or receipt.artifact_ref != target_ref
            or receipt.sha256 != digest
            or receipt.size_bytes != size
        ):
            raise SpeechTrainingBackendError(
                "speech_evaluation_receipt_mismatch",
                "Hub evaluation receipt does not match the fenced publication",
            )
        return receipt
