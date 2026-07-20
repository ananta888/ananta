"""Shared wire contract for one Hub-delegated reconciliation attempt.

The Hub-owned job and ledger contracts stay in :mod:`ananta_contracts`. This
module adds the closed execution metadata required to stage one encrypted
audio artifact and run an admitted local ASR pass plan. It has no Hub or
worker implementation imports and is the wire source-of-truth for both.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from ananta_contracts.speech_reconciliation import (
    CONTRACT_VERSION,
    SpeechReconciliationBudgetLedger,
    SpeechReconciliationCheckpoint,
    SpeechReconciliationContractError,
    SpeechReconciliationJob,
    SpeechReconciliationResult,
    SpeechResourceVector,
    assert_result_matches_job,
    canonical_json,
    canonical_sha256,
)

WORKER_TASK_TYPE = "speech_reconciliation_attempt"
MAX_AUDIO_CIPHERTEXT_BYTES = 128 * 1024 * 1024
MAX_AUDIO_PLAINTEXT_BYTES = 96 * 1024 * 1024
MAX_DECODED_PCM_BYTES = 256 * 1024 * 1024
MAX_ENSEMBLE_PASSES = 32
MAX_PARALLEL_PASSES = 8
MAX_WORKER_RESULT_BYTES = 1024 * 1024
MAX_WORKER_RESULT_NODES = 100_000
MAX_WORKER_RESULT_DEPTH = 24

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_MODEL_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+/-]{0,511}$")
_ARTIFACT = re.compile(r"^artifact://speech-evidence/[A-Za-z0-9][A-Za-z0-9_./:-]{0,470}$")
_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_OUTCOME_BINDING_FIELDS = (
    "contract_version",
    "job_id",
    "attempt_id",
    "fencing_token_digest",
    "fencing_epoch",
    "consent_id",
    "consent_version",
    "revocation_epoch",
    "input_manifest_digest",
    "policy_digest",
    "ledger_sequence",
    "key_epoch",
)
_AUDIO_CONTENT_TYPES = frozenset(
    {
        "audio/wav",
        "audio/x-wav",
        "audio/mpeg",
        "audio/ogg",
        "audio/webm",
        "audio/mp4",
    }
)


@dataclass(frozen=True)
class SpeechReconciliationAudioArtifact:
    artifact_ref: str
    transport_digest: str
    content_digest: str
    filename: str
    content_type: str
    ciphertext_bytes: int
    plaintext_bytes: int
    decoded_pcm_bytes: int
    duration_ms: int
    key_epoch: int

    @classmethod
    def from_mapping(cls, value: object) -> "SpeechReconciliationAudioArtifact":
        data = _closed(value, frozenset(cls.__dataclass_fields__), "audio_artifact")
        artifact_ref = _string(data["artifact_ref"], "artifact_ref", pattern=_ARTIFACT)
        if ".." in artifact_ref.split("/"):
            raise SpeechReconciliationContractError("speech_reconciliation_artifact_ref_invalid")
        return cls(
            artifact_ref=artifact_ref,
            transport_digest=_digest(data["transport_digest"], "transport_digest"),
            content_digest=_digest(data["content_digest"], "content_digest"),
            filename=_string(data["filename"], "filename", pattern=_FILENAME),
            content_type=_enum(data["content_type"], _AUDIO_CONTENT_TYPES, "content_type"),
            ciphertext_bytes=_integer(
                data["ciphertext_bytes"],
                "ciphertext_bytes",
                minimum=16,
                maximum=MAX_AUDIO_CIPHERTEXT_BYTES,
            ),
            plaintext_bytes=_integer(
                data["plaintext_bytes"],
                "plaintext_bytes",
                minimum=1,
                maximum=MAX_AUDIO_PLAINTEXT_BYTES,
            ),
            decoded_pcm_bytes=_integer(
                data["decoded_pcm_bytes"],
                "decoded_pcm_bytes",
                minimum=2,
                maximum=MAX_DECODED_PCM_BYTES,
            ),
            duration_ms=_integer(data["duration_ms"], "duration_ms", minimum=1, maximum=8 * 60 * 60 * 1000),
            key_epoch=_integer(data["key_epoch"], "key_epoch", minimum=1),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def aad_mapping(self, job: SpeechReconciliationJob) -> dict[str, object]:
        """Return all immutable bindings authenticated by the artifact AEAD."""

        return {
            "contract_version": CONTRACT_VERSION,
            "job_id": job.job_id,
            "attempt_id": job.attempt_id,
            "fencing_token_digest": job.fencing_token_digest,
            "fencing_epoch": job.fencing_epoch,
            "consent_id": job.consent_id,
            "consent_version": job.consent_version,
            "revocation_epoch": job.revocation_epoch,
            "input_manifest_digest": job.input_manifest_digest,
            "policy_digest": job.policy_digest,
            "ledger_sequence": job.ledger_sequence,
            "key_epoch": job.key_epoch,
            "artifact_ref": self.artifact_ref,
            "content_digest": self.content_digest,
            "filename": self.filename,
            "content_type": self.content_type,
            "plaintext_bytes": self.plaintext_bytes,
            "decoded_pcm_bytes": self.decoded_pcm_bytes,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class SpeechReconciliationPass:
    pass_id: str
    model_id: str
    model_revision: str
    variant_id: str
    language: str | None

    @classmethod
    def from_mapping(cls, value: object) -> "SpeechReconciliationPass":
        data = _closed(value, frozenset(cls.__dataclass_fields__), "execution_plan.pass")
        language = data["language"]
        if language is not None:
            language = _string(language, "language", pattern=re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$"))
        return cls(
            pass_id=_string(data["pass_id"], "pass_id", pattern=_IDENTIFIER),
            model_id=_string(data["model_id"], "model_id", pattern=_IDENTIFIER),
            model_revision=_string(data["model_revision"], "model_revision", pattern=_MODEL_REVISION),
            variant_id=_string(data["variant_id"], "variant_id", pattern=_IDENTIFIER),
            language=language,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SpeechReconciliationExecutionPlan:
    max_parallel_passes: int
    pass_deadline_ms: int
    passes: tuple[SpeechReconciliationPass, ...]

    @classmethod
    def from_mapping(cls, value: object) -> "SpeechReconciliationExecutionPlan":
        data = _closed(value, frozenset(cls.__dataclass_fields__), "execution_plan")
        raw_passes = data["passes"]
        if not isinstance(raw_passes, list) or not 1 <= len(raw_passes) <= MAX_ENSEMBLE_PASSES:
            raise SpeechReconciliationContractError("speech_reconciliation_pass_count_invalid")
        passes = tuple(SpeechReconciliationPass.from_mapping(item) for item in raw_passes)
        pass_ids = [item.pass_id for item in passes]
        identities = [(item.model_id, item.model_revision, item.variant_id, item.language) for item in passes]
        if len(pass_ids) != len(set(pass_ids)) or len(identities) != len(set(identities)):
            raise SpeechReconciliationContractError("speech_reconciliation_pass_duplicate")
        return cls(
            max_parallel_passes=_integer(
                data["max_parallel_passes"],
                "max_parallel_passes",
                minimum=1,
                maximum=MAX_PARALLEL_PASSES,
            ),
            pass_deadline_ms=_integer(data["pass_deadline_ms"], "pass_deadline_ms", minimum=1, maximum=3_600_000),
            passes=passes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_parallel_passes": self.max_parallel_passes,
            "pass_deadline_ms": self.pass_deadline_ms,
            "passes": [item.to_dict() for item in self.passes],
        }


@dataclass(frozen=True)
class SpeechReconciliationWorkerTask:
    contract_version: str
    task_type: str
    job: SpeechReconciliationJob
    budget_ledger: SpeechReconciliationBudgetLedger
    audio_artifact: SpeechReconciliationAudioArtifact
    execution_plan: SpeechReconciliationExecutionPlan

    @classmethod
    def from_mapping(cls, value: object) -> "SpeechReconciliationWorkerTask":
        data = _closed(value, frozenset(cls.__dataclass_fields__), "worker_task")
        if data["contract_version"] != CONTRACT_VERSION or data["task_type"] != WORKER_TASK_TYPE:
            raise SpeechReconciliationContractError("speech_reconciliation_worker_contract_invalid")
        job = SpeechReconciliationJob.from_mapping(data["job"])
        ledger = SpeechReconciliationBudgetLedger.from_mapping(data["budget_ledger"])
        artifact = SpeechReconciliationAudioArtifact.from_mapping(data["audio_artifact"])
        plan = SpeechReconciliationExecutionPlan.from_mapping(data["execution_plan"])
        _validate_bindings(job, ledger, artifact)
        return cls(
            contract_version=CONTRACT_VERSION,
            task_type=WORKER_TASK_TYPE,
            job=job,
            budget_ledger=ledger,
            audio_artifact=artifact,
            execution_plan=plan,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "task_type": self.task_type,
            "job": self.job.to_dict(),
            "budget_ledger": self.budget_ledger.to_dict(),
            "audio_artifact": self.audio_artifact.to_dict(),
            "execution_plan": self.execution_plan.to_dict(),
        }

    @property
    def binding_digest(self) -> str:
        return canonical_sha256(
            {
                "job": self.job.to_dict(),
                "budget_ledger": self.budget_ledger.to_dict(),
                "audio_artifact": self.audio_artifact.to_dict(),
                "execution_plan": self.execution_plan.to_dict(),
            }
        )


@dataclass(frozen=True)
class SpeechReconciliationWorkerOutcome:
    """Closed terminal execution result returned to the Hub.

    This is intentionally distinct from :class:`SpeechReconciliationResult`:
    workers may report a checkpoint and a proposed transcript, but only the
    Hub can materialize and publish the resulting dataset.
    """

    contract_version: str
    job_id: str
    attempt_id: str
    fencing_token_digest: str
    fencing_epoch: int
    consent_id: str
    consent_version: int
    revocation_epoch: int
    input_manifest_digest: str
    policy_digest: str
    ledger_sequence: int
    key_epoch: int
    status: str
    candidate_set_digest: str | None
    candidate_count: int
    successful_candidate_count: int
    failed_candidate_count: int
    quality_score_micros: int | None
    previous_quality_score_micros: int | None
    graph_digest: str | None
    resolution_hash: str | None
    unresolved_count: int
    unresolved_region_ids: tuple[str, ...] | None
    unresolved_high_quality_conflict_count: int | None
    publishable: bool
    reason_code: str
    checkpoint: SpeechReconciliationCheckpoint | None
    transcript: Mapping[str, Any] | None
    retryable: bool

    @classmethod
    def from_mapping(cls, value: object) -> "SpeechReconciliationWorkerOutcome":
        expected_fields = frozenset(cls.__dataclass_fields__)
        optional_quality_fields = frozenset(
            {
                "quality_score_micros",
                "previous_quality_score_micros",
                "unresolved_region_ids",
                "unresolved_high_quality_conflict_count",
            }
        )
        data = _closed_with_optional(
            value,
            expected_fields,
            optional_quality_fields,
            "worker_outcome",
        )
        if data["contract_version"] != CONTRACT_VERSION:
            raise SpeechReconciliationContractError("speech_reconciliation_contract_version_invalid")
        status = _enum(
            data["status"],
            frozenset({"completed", "partial", "failed", "cancelled"}),
            "status",
        )
        candidate_count = _integer(
            data["candidate_count"],
            "candidate_count",
            minimum=0,
            maximum=MAX_ENSEMBLE_PASSES,
        )
        successful = _integer(
            data["successful_candidate_count"],
            "successful_candidate_count",
            minimum=0,
            maximum=MAX_ENSEMBLE_PASSES,
        )
        failed = _integer(
            data["failed_candidate_count"],
            "failed_candidate_count",
            minimum=0,
            maximum=MAX_ENSEMBLE_PASSES,
        )
        if successful + failed != candidate_count:
            raise SpeechReconciliationContractError("speech_reconciliation_candidate_count_inconsistent")
        quality_score = _optional_integer(
            data.get("quality_score_micros"),
            "quality_score_micros",
            minimum=0,
            maximum=1_000_000,
        )
        previous_quality_score = _optional_integer(
            data.get("previous_quality_score_micros"),
            "previous_quality_score_micros",
            minimum=0,
            maximum=1_000_000,
        )
        if previous_quality_score is not None and quality_score is None:
            raise SpeechReconciliationContractError("speech_reconciliation_quality_observation_invalid")
        candidate_digest = _optional_digest(data["candidate_set_digest"], "candidate_set_digest")
        if (candidate_count == 0) != (candidate_digest is None):
            raise SpeechReconciliationContractError("speech_reconciliation_candidate_digest_inconsistent")
        graph_digest = _optional_digest(data["graph_digest"], "graph_digest")
        resolution_hash = _optional_digest(data["resolution_hash"], "resolution_hash")
        checkpoint_raw = data["checkpoint"]
        checkpoint = None if checkpoint_raw is None else SpeechReconciliationCheckpoint.from_mapping(checkpoint_raw)
        transcript = _bounded_json_mapping(data["transcript"], "transcript")
        publishable = _boolean(data["publishable"], "publishable")
        retryable = _boolean(data["retryable"], "retryable")
        unresolved_count = _integer(
            data["unresolved_count"],
            "unresolved_count",
            minimum=0,
            maximum=1_000_000,
        )
        unresolved_high_quality_conflict_count = _optional_integer(
            data.get("unresolved_high_quality_conflict_count"),
            "unresolved_high_quality_conflict_count",
            minimum=0,
            maximum=1_000_000,
        )
        if (
            unresolved_high_quality_conflict_count is not None
            and unresolved_high_quality_conflict_count > unresolved_count
        ):
            raise SpeechReconciliationContractError("speech_reconciliation_high_quality_conflict_count_inconsistent")
        unresolved_region_ids = _optional_digest_tuple(
            data.get("unresolved_region_ids"),
            "unresolved_region_ids",
            maximum=MAX_WORKER_RESULT_NODES,
        )
        if unresolved_region_ids is not None and len(unresolved_region_ids) != unresolved_count:
            raise SpeechReconciliationContractError("speech_reconciliation_unresolved_region_count_inconsistent")
        if publishable:
            if (
                status not in {"completed", "partial"}
                or transcript is None
                or graph_digest is None
                or resolution_hash is None
                or successful < 1
                or unresolved_count != 0
            ):
                raise SpeechReconciliationContractError("speech_reconciliation_publishable_result_invalid")
        elif transcript is not None:
            raise SpeechReconciliationContractError("speech_reconciliation_unpublished_transcript_forbidden")
        if status == "completed" and not publishable:
            raise SpeechReconciliationContractError("speech_reconciliation_completed_result_not_publishable")
        if status in {"failed", "cancelled"} and publishable:
            raise SpeechReconciliationContractError("speech_reconciliation_terminal_result_publishable")
        if retryable and status not in {"failed", "cancelled"}:
            raise SpeechReconciliationContractError("speech_reconciliation_retryable_status_invalid")

        outcome = cls(
            contract_version=CONTRACT_VERSION,
            job_id=_string(data["job_id"], "job_id", pattern=_IDENTIFIER),
            attempt_id=_string(data["attempt_id"], "attempt_id", pattern=_IDENTIFIER),
            fencing_token_digest=_digest(data["fencing_token_digest"], "fencing_token_digest"),
            fencing_epoch=_integer(data["fencing_epoch"], "fencing_epoch", minimum=1),
            consent_id=_string(data["consent_id"], "consent_id", pattern=_IDENTIFIER),
            consent_version=_integer(data["consent_version"], "consent_version", minimum=1),
            revocation_epoch=_integer(data["revocation_epoch"], "revocation_epoch", minimum=0),
            input_manifest_digest=_digest(data["input_manifest_digest"], "input_manifest_digest"),
            policy_digest=_digest(data["policy_digest"], "policy_digest"),
            ledger_sequence=_integer(data["ledger_sequence"], "ledger_sequence", minimum=0),
            key_epoch=_integer(data["key_epoch"], "key_epoch", minimum=1),
            status=status,
            candidate_set_digest=candidate_digest,
            candidate_count=candidate_count,
            successful_candidate_count=successful,
            failed_candidate_count=failed,
            quality_score_micros=quality_score,
            previous_quality_score_micros=previous_quality_score,
            graph_digest=graph_digest,
            resolution_hash=resolution_hash,
            unresolved_count=unresolved_count,
            unresolved_region_ids=unresolved_region_ids,
            unresolved_high_quality_conflict_count=unresolved_high_quality_conflict_count,
            publishable=publishable,
            reason_code=_string(data["reason_code"], "reason_code", pattern=_IDENTIFIER),
            checkpoint=checkpoint,
            transcript=transcript,
            retryable=retryable,
        )
        _validate_outcome_checkpoint(outcome)
        return outcome

    @classmethod
    def failure(
        cls,
        job: SpeechReconciliationJob,
        *,
        status: str,
        reason_code: str,
        retryable: bool = False,
    ) -> "SpeechReconciliationWorkerOutcome":
        return cls.from_mapping(
            {
                **_job_result_bindings(job),
                "status": status,
                "candidate_set_digest": None,
                "candidate_count": 0,
                "successful_candidate_count": 0,
                "failed_candidate_count": 0,
                "quality_score_micros": None,
                "previous_quality_score_micros": None,
                "graph_digest": None,
                "resolution_hash": None,
                "unresolved_count": 0,
                "unresolved_region_ids": [],
                "unresolved_high_quality_conflict_count": 0,
                "publishable": False,
                "reason_code": reason_code,
                "checkpoint": None,
                "transcript": None,
                "retryable": retryable,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "job_id": self.job_id,
            "attempt_id": self.attempt_id,
            "fencing_token_digest": self.fencing_token_digest,
            "fencing_epoch": self.fencing_epoch,
            "consent_id": self.consent_id,
            "consent_version": self.consent_version,
            "revocation_epoch": self.revocation_epoch,
            "input_manifest_digest": self.input_manifest_digest,
            "policy_digest": self.policy_digest,
            "ledger_sequence": self.ledger_sequence,
            "key_epoch": self.key_epoch,
            "status": self.status,
            "candidate_set_digest": self.candidate_set_digest,
            "candidate_count": self.candidate_count,
            "successful_candidate_count": self.successful_candidate_count,
            "failed_candidate_count": self.failed_candidate_count,
            "quality_score_micros": self.quality_score_micros,
            "previous_quality_score_micros": self.previous_quality_score_micros,
            "graph_digest": self.graph_digest,
            "resolution_hash": self.resolution_hash,
            "unresolved_count": self.unresolved_count,
            "unresolved_region_ids": (None if self.unresolved_region_ids is None else list(self.unresolved_region_ids)),
            "unresolved_high_quality_conflict_count": self.unresolved_high_quality_conflict_count,
            "publishable": self.publishable,
            "reason_code": self.reason_code,
            "checkpoint": self.checkpoint.to_dict() if self.checkpoint is not None else None,
            "transcript": dict(self.transcript) if self.transcript is not None else None,
            "retryable": self.retryable,
        }


def assert_worker_outcome_matches_job(
    job: SpeechReconciliationJob,
    outcome: SpeechReconciliationWorkerOutcome,
) -> None:
    expected = _job_result_bindings(job)
    if any(getattr(outcome, field) != value for field, value in expected.items()):
        raise SpeechReconciliationContractError("speech_reconciliation_worker_result_binding_mismatch")
    if outcome.checkpoint is not None:
        assert_result_matches_job(job, outcome.checkpoint)
        if outcome.checkpoint.ledger_sequence != job.ledger_sequence:
            raise SpeechReconciliationContractError("speech_reconciliation_worker_result_ledger_mismatch")


def _job_result_bindings(job: SpeechReconciliationJob) -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "job_id": job.job_id,
        "attempt_id": job.attempt_id,
        "fencing_token_digest": job.fencing_token_digest,
        "fencing_epoch": job.fencing_epoch,
        "consent_id": job.consent_id,
        "consent_version": job.consent_version,
        "revocation_epoch": job.revocation_epoch,
        "input_manifest_digest": job.input_manifest_digest,
        "policy_digest": job.policy_digest,
        "ledger_sequence": job.ledger_sequence,
        "key_epoch": job.key_epoch,
    }


def _validate_outcome_checkpoint(outcome: SpeechReconciliationWorkerOutcome) -> None:
    checkpoint = outcome.checkpoint
    if checkpoint is None:
        return
    for field in _OUTCOME_BINDING_FIELDS:
        if getattr(checkpoint, field) != getattr(outcome, field):
            raise SpeechReconciliationContractError("speech_reconciliation_checkpoint_binding_mismatch")


def _validate_bindings(
    job: SpeechReconciliationJob,
    ledger: SpeechReconciliationBudgetLedger,
    artifact: SpeechReconciliationAudioArtifact,
) -> None:
    if (
        ledger.job_id != job.job_id
        or ledger.attempt_id != job.attempt_id
        or ledger.fencing_epoch != job.fencing_epoch
        or ledger.sequence != job.ledger_sequence
        or ledger.stage != job.stage
    ):
        raise SpeechReconciliationContractError("speech_reconciliation_ledger_binding_mismatch")
    if artifact.artifact_ref != job.input_artifact_ref or artifact.key_epoch != job.key_epoch:
        raise SpeechReconciliationContractError("speech_reconciliation_audio_binding_mismatch")
    if artifact.duration_ms > job.source_duration_ms:
        raise SpeechReconciliationContractError("speech_reconciliation_audio_duration_invalid")
    required_disk = artifact.ciphertext_bytes + artifact.plaintext_bytes + artifact.decoded_pcm_bytes
    if required_disk > ledger.remaining.disk_bytes:
        raise SpeechReconciliationContractError("speech_reconciliation_stage_disk_budget_exceeded")
    if ledger.remaining.wall_time_ms < 1:
        raise SpeechReconciliationContractError("speech_reconciliation_stage_time_budget_exceeded")


def _closed(value: object, fields: frozenset[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value) or set(value) != fields:
        raise SpeechReconciliationContractError("speech_reconciliation_shape_invalid", name)
    return value


def _closed_with_optional(
    value: object,
    fields: frozenset[str],
    optional: frozenset[str],
    name: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise SpeechReconciliationContractError("speech_reconciliation_shape_invalid", name)
    required = fields - optional
    if not required <= set(value) or set(value) - fields:
        raise SpeechReconciliationContractError("speech_reconciliation_shape_invalid", name)
    return value


def _integer(value: object, name: str, *, minimum: int, maximum: int = 2**63 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise SpeechReconciliationContractError("speech_reconciliation_integer_invalid", name)
    return value


def _optional_integer(
    value: object,
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    return None if value is None else _integer(value, name, minimum=minimum, maximum=maximum)


def _string(value: object, name: str, *, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise SpeechReconciliationContractError("speech_reconciliation_identifier_invalid", name)
    return value


def _digest(value: object, name: str) -> str:
    return _string(value, name, pattern=_DIGEST)


def _optional_digest(value: object, name: str) -> str | None:
    return None if value is None else _digest(value, name)


def _optional_digest_tuple(
    value: object,
    name: str,
    *,
    maximum: int,
) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > maximum:
        raise SpeechReconciliationContractError("speech_reconciliation_digest_list_invalid", name)
    values = tuple(_digest(item, name) for item in value)
    if values != tuple(sorted(set(values))):
        raise SpeechReconciliationContractError("speech_reconciliation_digest_list_invalid", name)
    return values


def _enum(value: object, allowed: frozenset[str], name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise SpeechReconciliationContractError("speech_reconciliation_enum_invalid", name)
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise SpeechReconciliationContractError("speech_reconciliation_boolean_invalid", name)
    return value


def _bounded_json_mapping(value: object, name: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise SpeechReconciliationContractError("speech_reconciliation_shape_invalid", name)
    stack: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_WORKER_RESULT_NODES or depth > MAX_WORKER_RESULT_DEPTH:
            raise SpeechReconciliationContractError("speech_reconciliation_worker_result_complexity_exceeded")
        if isinstance(current, Mapping):
            if len(current) > 10_000 or any(not isinstance(key, str) or len(key) > 128 for key in current):
                raise SpeechReconciliationContractError("speech_reconciliation_worker_result_shape_invalid")
            if any(
                marker in key.casefold()
                for key in current
                for marker in ("private_key", "raw_key", "secret", "local_path", "nonce")
            ):
                raise SpeechReconciliationContractError("speech_reconciliation_worker_result_sensitive_field")
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list | tuple):
            if len(current) > 100_000:
                raise SpeechReconciliationContractError("speech_reconciliation_worker_result_shape_invalid")
            stack.extend((item, depth + 1) for item in current)
        elif current is None or isinstance(current, bool | int | float | str):
            if isinstance(current, str) and len(current) > MAX_WORKER_RESULT_BYTES:
                raise SpeechReconciliationContractError("speech_reconciliation_worker_result_too_large")
        else:
            raise SpeechReconciliationContractError("speech_reconciliation_worker_result_value_invalid")
    payload = canonical_json(value)
    if len(payload) > MAX_WORKER_RESULT_BYTES:
        raise SpeechReconciliationContractError("speech_reconciliation_worker_result_too_large")
    normalized = json.loads(payload)
    if not isinstance(normalized, dict):
        raise SpeechReconciliationContractError("speech_reconciliation_shape_invalid", name)
    return normalized


__all__ = [
    "CONTRACT_VERSION",
    "MAX_AUDIO_CIPHERTEXT_BYTES",
    "MAX_AUDIO_PLAINTEXT_BYTES",
    "MAX_DECODED_PCM_BYTES",
    "MAX_ENSEMBLE_PASSES",
    "MAX_PARALLEL_PASSES",
    "MAX_WORKER_RESULT_BYTES",
    "SpeechReconciliationAudioArtifact",
    "SpeechReconciliationBudgetLedger",
    "SpeechReconciliationCheckpoint",
    "SpeechReconciliationContractError",
    "SpeechReconciliationExecutionPlan",
    "SpeechReconciliationJob",
    "SpeechReconciliationPass",
    "SpeechReconciliationResult",
    "SpeechReconciliationWorkerTask",
    "SpeechReconciliationWorkerOutcome",
    "SpeechResourceVector",
    "WORKER_TASK_TYPE",
    "assert_result_matches_job",
    "assert_worker_outcome_matches_job",
    "canonical_sha256",
]
