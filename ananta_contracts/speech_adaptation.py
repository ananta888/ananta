"""Closed, dependency-light contracts for local speech adaptation.

This module deliberately does not import the text LoRA contract.  Speech
adaptation has different privacy, scope and lifecycle bindings and therefore
uses an independent wire version and job type.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Mapping

CONTRACT_VERSION = "ananta.speech-adaptation.v1"
TRAIN_JOB_TYPE = "speech_adaptation_train"
RESULT_TYPE = "speech_adaptation_result"
SUPPORTED_BACKENDS = frozenset({"mock", "openvoice_v2"})
SUPPORTED_DIRECTIONS = frozenset({"sender_to_receiver", "receiver_to_sender"})
SUPPORTED_SCENARIOS = frozenset(
    {
        "success",
        "dataset_only",
        "cancel",
        "deadline",
        "lease_lost",
        "checkpoint_resume",
        "evaluation_fail",
        "publish_fail",
        "subprocess_cancel",
    }
)

MAX_STEPS = 100_000
MAX_BATCH_SIZE = 64
MAX_CHECKPOINTS = 128
MAX_WALL_SECONDS = 8 * 60 * 60
MAX_RAM_BYTES = 512 * 1024**3
MAX_VRAM_BYTES = 256 * 1024**3
MAX_DISK_BYTES = 1024**4
MAX_ARTIFACT_BYTES = 8 * 1024**3
MAX_EVENTS = 10_000
MAX_DEADLINE_AHEAD_MS = 24 * 60 * 60 * 1000

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_REF_RE = re.compile(r"^artifact://[A-Za-z0-9][A-Za-z0-9_./:-]{0,500}$")


class SpeechAdaptationContractError(ValueError):
    """Stable fail-closed contract rejection safe for transport."""

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        status_code: int = 422,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.code = reason_code
        self.status_code = status_code
        self.http_status = status_code
        self.retryable = retryable


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SpeechAdaptationContractError(
            "speech_contract_not_canonical",
            "speech adaptation contract must be finite canonical JSON",
        ) from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SpeechAdaptationContractError("speech_contract_invalid", f"{field} must be an object")
    return value


def _closed(value: Any, field: str, allowed: frozenset[str]) -> Mapping[str, Any]:
    result = _mapping(value, field)
    if any(not isinstance(key, str) for key in result):
        raise SpeechAdaptationContractError(
            "speech_contract_unknown_field",
            f"{field} contains a non-string field name",
        )
    unknown = sorted(set(result) - allowed)
    if unknown:
        raise SpeechAdaptationContractError(
            "speech_contract_unknown_field",
            f"{field} contains unknown fields: {', '.join(unknown[:10])}",
        )
    return result


def _text(value: Any, field: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise SpeechAdaptationContractError("speech_contract_invalid", f"{field} must be a string")
    result = value.strip()
    if not result or len(result) > maximum:
        raise SpeechAdaptationContractError(
            "speech_contract_invalid",
            f"{field} is required and must contain at most {maximum} characters",
        )
    return result


def _identifier(value: Any, field: str) -> str:
    result = _text(value, field, maximum=192)
    if not _IDENTIFIER_RE.fullmatch(result):
        raise SpeechAdaptationContractError("speech_contract_identifier_invalid", f"{field} is invalid")
    return result


def _digest(value: Any, field: str) -> str:
    result = value.strip() if isinstance(value, str) else ""
    if not _DIGEST_RE.fullmatch(result):
        raise SpeechAdaptationContractError(
            "speech_contract_digest_invalid",
            f"{field} must be a lowercase SHA-256 digest",
        )
    return result


def _integer(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SpeechAdaptationContractError("speech_contract_invalid", f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise SpeechAdaptationContractError(
            "speech_contract_limit_exceeded",
            f"{field} must be between {minimum} and {maximum}",
        )
    return value


def _number(value: Any, field: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpeechAdaptationContractError("speech_contract_invalid", f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise SpeechAdaptationContractError(
            "speech_contract_limit_exceeded",
            f"{field} must be finite and between {minimum} and {maximum}",
        )
    return result


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise SpeechAdaptationContractError("speech_contract_invalid", f"{field} must be a boolean")
    return value


def _artifact_ref(value: Any, field: str, *, prefix: str) -> str:
    result = _text(value, field)
    if not _ARTIFACT_REF_RE.fullmatch(result) or ".." in result.split("/") or not result.startswith(prefix):
        raise SpeechAdaptationContractError(
            "speech_contract_artifact_ref_invalid",
            f"{field} must be an immutable {prefix} reference",
        )
    forbidden = ("browser", "peer-buffer", "quarantine", "delay-buffer", "live-buffer")
    if any(part in result.casefold() for part in forbidden):
        raise SpeechAdaptationContractError(
            "speech_contract_mutable_source_forbidden",
            f"{field} references a mutable source",
        )
    return result


@dataclass(frozen=True)
class SpeechDatasetBinding:
    dataset_id: str
    dataset_version: str
    storage_ref: str
    dataset_digest: str
    split_digest: str
    lineage_digest: str
    train_sample_count: int
    validation_sample_count: int
    immutable: bool

    @classmethod
    def from_mapping(cls, value: Any) -> "SpeechDatasetBinding":
        data = _closed(
            value,
            "dataset",
            frozenset(
                {
                    "dataset_id",
                    "dataset_version",
                    "storage_ref",
                    "dataset_digest",
                    "split_digest",
                    "lineage_digest",
                    "train_sample_count",
                    "validation_sample_count",
                    "immutable",
                }
            ),
        )
        immutable = _boolean(data.get("immutable"), "dataset.immutable")
        if not immutable:
            raise SpeechAdaptationContractError(
                "speech_dataset_not_immutable",
                "speech training accepts only immutable dataset versions",
            )
        return cls(
            dataset_id=_identifier(data.get("dataset_id"), "dataset.dataset_id"),
            dataset_version=_identifier(data.get("dataset_version"), "dataset.dataset_version"),
            storage_ref=_artifact_ref(
                data.get("storage_ref"),
                "dataset.storage_ref",
                prefix="artifact://speech-datasets/",
            ),
            dataset_digest=_digest(data.get("dataset_digest"), "dataset.dataset_digest"),
            split_digest=_digest(data.get("split_digest"), "dataset.split_digest"),
            lineage_digest=_digest(data.get("lineage_digest"), "dataset.lineage_digest"),
            train_sample_count=_integer(
                data.get("train_sample_count"),
                "dataset.train_sample_count",
                minimum=1,
                maximum=10_000_000,
            ),
            validation_sample_count=_integer(
                data.get("validation_sample_count"),
                "dataset.validation_sample_count",
                minimum=1,
                maximum=2_000_000,
            ),
            immutable=immutable,
        )


@dataclass(frozen=True)
class SpeechBaseModelBinding:
    model_id: str
    artifact_ref: str
    model_digest: str

    @classmethod
    def from_mapping(cls, value: Any) -> "SpeechBaseModelBinding":
        data = _closed(value, "base_model", frozenset({"model_id", "artifact_ref", "model_digest"}))
        return cls(
            model_id=_identifier(data.get("model_id"), "base_model.model_id"),
            artifact_ref=_artifact_ref(
                data.get("artifact_ref"),
                "base_model.artifact_ref",
                prefix="artifact://speech-models/",
            ),
            model_digest=_digest(data.get("model_digest"), "base_model.model_digest"),
        )


def speech_scope_digest(*, pair_id: str, direction: str, speaker_digest: str) -> str:
    return canonical_sha256(
        {
            "direction": direction,
            "pair_id": pair_id,
            "speaker_digest": speaker_digest,
        }
    )


@dataclass(frozen=True)
class SpeechScopeBinding:
    pair_id: str
    direction: str
    speaker_digest: str
    scope_digest: str

    @classmethod
    def from_mapping(cls, value: Any) -> "SpeechScopeBinding":
        data = _closed(value, "scope", frozenset({"pair_id", "direction", "speaker_digest", "scope_digest"}))
        pair_id = _identifier(data.get("pair_id"), "scope.pair_id")
        direction = _text(data.get("direction"), "scope.direction", maximum=32)
        if direction not in SUPPORTED_DIRECTIONS:
            raise SpeechAdaptationContractError(
                "speech_scope_direction_invalid",
                "scope.direction is not supported",
            )
        speaker_digest = _digest(data.get("speaker_digest"), "scope.speaker_digest")
        scope_digest = _digest(data.get("scope_digest"), "scope.scope_digest")
        expected = speech_scope_digest(pair_id=pair_id, direction=direction, speaker_digest=speaker_digest)
        if scope_digest != expected:
            raise SpeechAdaptationContractError(
                "speech_scope_digest_mismatch",
                "scope digest does not match pair, direction and speaker",
            )
        return cls(pair_id=pair_id, direction=direction, speaker_digest=speaker_digest, scope_digest=scope_digest)


@dataclass(frozen=True)
class SpeechConsentBinding:
    consent_id: str
    consent_version: int
    consent_digest: str
    scope_digest: str
    purpose: str
    granted: bool
    expires_at_ms: int
    export_allowed: bool

    @classmethod
    def from_mapping(cls, value: Any) -> "SpeechConsentBinding":
        data = _closed(
            value,
            "consent",
            frozenset(
                {
                    "consent_id",
                    "consent_version",
                    "consent_digest",
                    "scope_digest",
                    "purpose",
                    "granted",
                    "expires_at_ms",
                    "export_allowed",
                }
            ),
        )
        purpose = _text(data.get("purpose"), "consent.purpose", maximum=64)
        if purpose != "speech_adaptation_training":
            raise SpeechAdaptationContractError(
                "speech_consent_purpose_mismatch",
                "consent purpose must be speech_adaptation_training",
            )
        granted = _boolean(data.get("granted"), "consent.granted")
        if not granted:
            raise SpeechAdaptationContractError("speech_consent_missing", "active speech training consent is required")
        return cls(
            consent_id=_identifier(data.get("consent_id"), "consent.consent_id"),
            consent_version=_integer(
                data.get("consent_version"),
                "consent.consent_version",
                minimum=1,
                maximum=2**31 - 1,
            ),
            consent_digest=_digest(data.get("consent_digest"), "consent.consent_digest"),
            scope_digest=_digest(data.get("scope_digest"), "consent.scope_digest"),
            purpose=purpose,
            granted=granted,
            expires_at_ms=_integer(
                data.get("expires_at_ms"),
                "consent.expires_at_ms",
                minimum=1,
                maximum=2**63 - 1,
            ),
            export_allowed=_boolean(data.get("export_allowed"), "consent.export_allowed"),
        )


def speech_configuration_digest(values: Mapping[str, Any]) -> str:
    return canonical_sha256({key: values[key] for key in sorted(values) if key != "config_digest"})


@dataclass(frozen=True)
class SpeechTrainingConfiguration:
    backend: str
    backend_digest: str
    seed: int
    max_steps: int
    batch_size: int
    checkpoint_interval_steps: int
    learning_rate: float
    scenario: str
    config_digest: str

    @classmethod
    def from_mapping(cls, value: Any) -> "SpeechTrainingConfiguration":
        data = _closed(
            value,
            "configuration",
            frozenset(
                {
                    "backend",
                    "backend_digest",
                    "seed",
                    "max_steps",
                    "batch_size",
                    "checkpoint_interval_steps",
                    "learning_rate",
                    "scenario",
                    "config_digest",
                }
            ),
        )
        backend = _text(data.get("backend"), "configuration.backend", maximum=64).casefold()
        if backend not in SUPPORTED_BACKENDS:
            raise SpeechAdaptationContractError("speech_backend_forbidden", "configuration.backend is not allowlisted")
        scenario = _text(data.get("scenario"), "configuration.scenario", maximum=64).casefold()
        if scenario not in SUPPORTED_SCENARIOS:
            raise SpeechAdaptationContractError("speech_mock_scenario_invalid", "configuration.scenario is invalid")
        if backend != "mock" and scenario != "success":
            raise SpeechAdaptationContractError(
                "speech_mock_scenario_forbidden",
                "failure scenarios are restricted to the mock backend",
            )
        raw = {
            "backend": backend,
            "backend_digest": _digest(data.get("backend_digest"), "configuration.backend_digest"),
            "seed": _integer(data.get("seed"), "configuration.seed", minimum=0, maximum=2**31 - 1),
            "max_steps": _integer(data.get("max_steps"), "configuration.max_steps", minimum=1, maximum=MAX_STEPS),
            "batch_size": _integer(
                data.get("batch_size"),
                "configuration.batch_size",
                minimum=1,
                maximum=MAX_BATCH_SIZE,
            ),
            "checkpoint_interval_steps": _integer(
                data.get("checkpoint_interval_steps"),
                "configuration.checkpoint_interval_steps",
                minimum=1,
                maximum=MAX_STEPS,
            ),
            "learning_rate": _number(
                data.get("learning_rate"),
                "configuration.learning_rate",
                minimum=1e-8,
                maximum=1.0,
            ),
            "scenario": scenario,
        }
        if raw["checkpoint_interval_steps"] > raw["max_steps"]:
            raise SpeechAdaptationContractError(
                "speech_checkpoint_schedule_invalid",
                "checkpoint interval must not exceed max_steps",
            )
        config_digest = _digest(data.get("config_digest"), "configuration.config_digest")
        if config_digest != speech_configuration_digest(raw):
            raise SpeechAdaptationContractError(
                "speech_config_digest_mismatch",
                "configuration digest does not match bounded values",
            )
        return cls(**raw, config_digest=config_digest)


def speech_budget_digest(values: Mapping[str, Any]) -> str:
    return canonical_sha256({key: values[key] for key in sorted(values) if key != "budget_digest"})


@dataclass(frozen=True)
class SpeechResourceBudget:
    max_wall_seconds: int
    max_ram_bytes: int
    max_vram_bytes: int
    max_disk_bytes: int
    max_artifact_bytes: int
    max_checkpoints: int
    max_events: int
    budget_digest: str

    @classmethod
    def from_mapping(cls, value: Any) -> "SpeechResourceBudget":
        data = _closed(
            value,
            "budget",
            frozenset(
                {
                    "max_wall_seconds",
                    "max_ram_bytes",
                    "max_vram_bytes",
                    "max_disk_bytes",
                    "max_artifact_bytes",
                    "max_checkpoints",
                    "max_events",
                    "budget_digest",
                }
            ),
        )
        raw = {
            "max_wall_seconds": _integer(
                data.get("max_wall_seconds"),
                "budget.max_wall_seconds",
                minimum=1,
                maximum=MAX_WALL_SECONDS,
            ),
            "max_ram_bytes": _integer(
                data.get("max_ram_bytes"),
                "budget.max_ram_bytes",
                minimum=64 * 1024**2,
                maximum=MAX_RAM_BYTES,
            ),
            "max_vram_bytes": _integer(
                data.get("max_vram_bytes"),
                "budget.max_vram_bytes",
                minimum=0,
                maximum=MAX_VRAM_BYTES,
            ),
            "max_disk_bytes": _integer(
                data.get("max_disk_bytes"),
                "budget.max_disk_bytes",
                minimum=1024,
                maximum=MAX_DISK_BYTES,
            ),
            "max_artifact_bytes": _integer(
                data.get("max_artifact_bytes"),
                "budget.max_artifact_bytes",
                minimum=1,
                maximum=MAX_ARTIFACT_BYTES,
            ),
            "max_checkpoints": _integer(
                data.get("max_checkpoints"),
                "budget.max_checkpoints",
                minimum=1,
                maximum=MAX_CHECKPOINTS,
            ),
            "max_events": _integer(data.get("max_events"), "budget.max_events", minimum=1, maximum=MAX_EVENTS),
        }
        budget_digest = _digest(data.get("budget_digest"), "budget.budget_digest")
        if budget_digest != speech_budget_digest(raw):
            raise SpeechAdaptationContractError(
                "speech_budget_digest_mismatch",
                "budget digest does not match bounded values",
            )
        return cls(**raw, budget_digest=budget_digest)


def speech_attempt_digest(*, job_id: str, attempt_id: str, attempt_number: int) -> str:
    return canonical_sha256({"attempt_id": attempt_id, "attempt_number": attempt_number, "job_id": job_id})


@dataclass(frozen=True)
class SpeechAttemptBinding:
    attempt_id: str
    attempt_number: int
    attempt_digest: str

    @classmethod
    def from_mapping(cls, value: Any, *, job_id: str) -> "SpeechAttemptBinding":
        data = _closed(value, "attempt", frozenset({"attempt_id", "attempt_number", "attempt_digest"}))
        attempt_id = _identifier(data.get("attempt_id"), "attempt.attempt_id")
        attempt_number = _integer(
            data.get("attempt_number"),
            "attempt.attempt_number",
            minimum=1,
            maximum=10_000,
        )
        attempt_digest = _digest(data.get("attempt_digest"), "attempt.attempt_digest")
        if attempt_digest != speech_attempt_digest(
            job_id=job_id,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
        ):
            raise SpeechAdaptationContractError(
                "speech_attempt_digest_mismatch",
                "attempt digest does not match job and attempt",
            )
        return cls(attempt_id=attempt_id, attempt_number=attempt_number, attempt_digest=attempt_digest)


def speech_fencing_digest(
    *,
    attempt_id: str,
    epoch: int,
    lease_id: str,
    lease_expires_at_ms: int,
) -> str:
    return canonical_sha256(
        {
            "attempt_id": attempt_id,
            "epoch": epoch,
            "lease_expires_at_ms": lease_expires_at_ms,
            "lease_id": lease_id,
        }
    )


@dataclass(frozen=True)
class SpeechFencingBinding:
    lease_id: str
    epoch: int
    lease_expires_at_ms: int
    fencing_digest: str

    @classmethod
    def from_mapping(cls, value: Any, *, attempt_id: str) -> "SpeechFencingBinding":
        data = _closed(
            value,
            "fencing",
            frozenset({"lease_id", "epoch", "lease_expires_at_ms", "fencing_digest"}),
        )
        lease_id = _identifier(data.get("lease_id"), "fencing.lease_id")
        epoch = _integer(data.get("epoch"), "fencing.epoch", minimum=1, maximum=2**63 - 1)
        lease_expires_at_ms = _integer(
            data.get("lease_expires_at_ms"),
            "fencing.lease_expires_at_ms",
            minimum=1,
            maximum=2**63 - 1,
        )
        fencing_digest = _digest(data.get("fencing_digest"), "fencing.fencing_digest")
        expected = speech_fencing_digest(
            attempt_id=attempt_id,
            epoch=epoch,
            lease_id=lease_id,
            lease_expires_at_ms=lease_expires_at_ms,
        )
        if fencing_digest != expected:
            raise SpeechAdaptationContractError(
                "speech_fencing_digest_mismatch",
                "fencing digest does not match attempt, lease and epoch",
            )
        return cls(
            lease_id=lease_id,
            epoch=epoch,
            lease_expires_at_ms=lease_expires_at_ms,
            fencing_digest=fencing_digest,
        )


@dataclass(frozen=True)
class SpeechResumeBinding:
    checkpoint_ref: str
    checkpoint_digest: str
    checkpoint_step: int
    source_attempt_digest: str
    dataset_digest: str
    split_digest: str
    model_digest: str
    scope_digest: str
    config_digest: str

    @classmethod
    def from_mapping(cls, value: Any) -> "SpeechResumeBinding":
        data = _closed(
            value,
            "resume",
            frozenset(
                {
                    "checkpoint_ref",
                    "checkpoint_digest",
                    "checkpoint_step",
                    "source_attempt_digest",
                    "dataset_digest",
                    "split_digest",
                    "model_digest",
                    "scope_digest",
                    "config_digest",
                }
            ),
        )
        return cls(
            checkpoint_ref=_artifact_ref(
                data.get("checkpoint_ref"),
                "resume.checkpoint_ref",
                prefix="artifact://speech-checkpoints/",
            ),
            checkpoint_digest=_digest(data.get("checkpoint_digest"), "resume.checkpoint_digest"),
            checkpoint_step=_integer(
                data.get("checkpoint_step"),
                "resume.checkpoint_step",
                minimum=1,
                maximum=MAX_STEPS,
            ),
            source_attempt_digest=_digest(
                data.get("source_attempt_digest"),
                "resume.source_attempt_digest",
            ),
            dataset_digest=_digest(data.get("dataset_digest"), "resume.dataset_digest"),
            split_digest=_digest(data.get("split_digest"), "resume.split_digest"),
            model_digest=_digest(data.get("model_digest"), "resume.model_digest"),
            scope_digest=_digest(data.get("scope_digest"), "resume.scope_digest"),
            config_digest=_digest(data.get("config_digest"), "resume.config_digest"),
        )


@dataclass(frozen=True)
class SpeechArtifactTarget:
    target_id: str
    artifact_ref: str
    target_digest: str

    @classmethod
    def from_mapping(cls, value: Any) -> "SpeechArtifactTarget":
        data = _closed(value, "artifact_target", frozenset({"target_id", "artifact_ref", "target_digest"}))
        target_id = _identifier(data.get("target_id"), "artifact_target.target_id")
        artifact_ref = _artifact_ref(
            data.get("artifact_ref"),
            "artifact_target.artifact_ref",
            prefix="artifact://speech-adapters/",
        )
        target_digest = _digest(data.get("target_digest"), "artifact_target.target_digest")
        expected = canonical_sha256({"artifact_ref": artifact_ref, "target_id": target_id})
        if target_digest != expected:
            raise SpeechAdaptationContractError(
                "speech_artifact_target_digest_mismatch",
                "artifact target digest does not match its immutable binding",
            )
        return cls(target_id=target_id, artifact_ref=artifact_ref, target_digest=target_digest)


def speech_job_binding_digest(job: Mapping[str, str]) -> str:
    expected = {
        "artifact_target_digest",
        "attempt_digest",
        "budget_digest",
        "config_digest",
        "consent_digest",
        "dataset_digest",
        "fencing_digest",
        "lineage_digest",
        "model_digest",
        "scope_digest",
        "split_digest",
    }
    if set(job) != expected:
        raise SpeechAdaptationContractError(
            "speech_binding_set_invalid",
            "job binding digest requires the complete closed digest set",
        )
    return canonical_sha256(dict(job))


@dataclass(frozen=True)
class SpeechAdaptationJob:
    contract_version: str
    job_type: str
    job_id: str
    dataset: SpeechDatasetBinding
    base_model: SpeechBaseModelBinding
    scope: SpeechScopeBinding
    consent: SpeechConsentBinding
    configuration: SpeechTrainingConfiguration
    budget: SpeechResourceBudget
    attempt: SpeechAttemptBinding
    fencing: SpeechFencingBinding
    artifact_target: SpeechArtifactTarget
    deadline_at_ms: int
    binding_digest: str
    resume: SpeechResumeBinding | None = None

    @classmethod
    def from_mapping(cls, value: Any, *, now_ms: int | None = None) -> "SpeechAdaptationJob":
        data = _closed(
            value,
            "job",
            frozenset(
                {
                    "contract_version",
                    "job_type",
                    "job_id",
                    "dataset",
                    "base_model",
                    "scope",
                    "consent",
                    "configuration",
                    "budget",
                    "attempt",
                    "fencing",
                    "artifact_target",
                    "deadline_at_ms",
                    "binding_digest",
                    "resume",
                }
            ),
        )
        version = _text(data.get("contract_version"), "contract_version", maximum=64)
        if version != CONTRACT_VERSION:
            raise SpeechAdaptationContractError(
                "speech_contract_version_unsupported",
                f"contract_version must be {CONTRACT_VERSION}",
            )
        job_type = _text(data.get("job_type"), "job_type", maximum=64)
        if job_type != TRAIN_JOB_TYPE:
            raise SpeechAdaptationContractError(
                "speech_job_type_unsupported",
                f"job_type must be {TRAIN_JOB_TYPE}",
            )
        job_id = _identifier(data.get("job_id"), "job_id")
        dataset = SpeechDatasetBinding.from_mapping(data.get("dataset"))
        base_model = SpeechBaseModelBinding.from_mapping(data.get("base_model"))
        scope = SpeechScopeBinding.from_mapping(data.get("scope"))
        consent = SpeechConsentBinding.from_mapping(data.get("consent"))
        configuration = SpeechTrainingConfiguration.from_mapping(data.get("configuration"))
        budget = SpeechResourceBudget.from_mapping(data.get("budget"))
        attempt = SpeechAttemptBinding.from_mapping(data.get("attempt"), job_id=job_id)
        fencing = SpeechFencingBinding.from_mapping(data.get("fencing"), attempt_id=attempt.attempt_id)
        artifact_target = SpeechArtifactTarget.from_mapping(data.get("artifact_target"))
        deadline_at_ms = _integer(data.get("deadline_at_ms"), "deadline_at_ms", minimum=1, maximum=2**63 - 1)
        effective_now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        if deadline_at_ms <= effective_now:
            raise SpeechAdaptationContractError("speech_deadline_stale", "speech training deadline has expired")
        if deadline_at_ms - effective_now > MAX_DEADLINE_AHEAD_MS:
            raise SpeechAdaptationContractError(
                "speech_deadline_out_of_bounds",
                "speech training deadline exceeds the maximum admission horizon",
            )
        if consent.scope_digest != scope.scope_digest:
            raise SpeechAdaptationContractError(
                "speech_consent_scope_mismatch",
                "consent is not bound to the requested pair direction and speaker",
            )
        if consent.expires_at_ms < deadline_at_ms:
            raise SpeechAdaptationContractError(
                "speech_consent_expires_before_deadline",
                "consent must remain valid through the job deadline",
            )
        if fencing.lease_expires_at_ms <= effective_now or fencing.lease_expires_at_ms > deadline_at_ms:
            raise SpeechAdaptationContractError(
                "speech_lease_invalid",
                "execution lease must be current and bounded by the job deadline",
            )
        if budget.max_wall_seconds * 1000 > deadline_at_ms - effective_now:
            raise SpeechAdaptationContractError(
                "speech_budget_deadline_mismatch",
                "wall-time budget exceeds the admitted deadline",
            )
        if budget.max_wall_seconds * 1000 > fencing.lease_expires_at_ms - effective_now:
            raise SpeechAdaptationContractError(
                "speech_budget_lease_mismatch",
                "wall-time budget exceeds the immutable execution lease",
            )
        resume = SpeechResumeBinding.from_mapping(data.get("resume")) if data.get("resume") is not None else None
        if resume is not None:
            expected_resume = {
                "dataset_digest": dataset.dataset_digest,
                "split_digest": dataset.split_digest,
                "model_digest": base_model.model_digest,
                "scope_digest": scope.scope_digest,
                "config_digest": configuration.config_digest,
            }
            mismatches = sorted(name for name, expected in expected_resume.items() if getattr(resume, name) != expected)
            if mismatches or resume.checkpoint_step >= configuration.max_steps:
                raise SpeechAdaptationContractError(
                    "speech_resume_binding_mismatch",
                    "resume checkpoint does not match the admitted job bindings",
                )
        digest_fields = {
            "artifact_target_digest": artifact_target.target_digest,
            "attempt_digest": attempt.attempt_digest,
            "budget_digest": budget.budget_digest,
            "config_digest": configuration.config_digest,
            "consent_digest": consent.consent_digest,
            "dataset_digest": dataset.dataset_digest,
            "fencing_digest": fencing.fencing_digest,
            "lineage_digest": dataset.lineage_digest,
            "model_digest": base_model.model_digest,
            "scope_digest": scope.scope_digest,
            "split_digest": dataset.split_digest,
        }
        binding_digest = _digest(data.get("binding_digest"), "binding_digest")
        if binding_digest != speech_job_binding_digest(digest_fields):
            raise SpeechAdaptationContractError(
                "speech_job_binding_digest_mismatch",
                "job binding digest does not match the admitted immutable inputs",
            )
        return cls(
            contract_version=version,
            job_type=job_type,
            job_id=job_id,
            dataset=dataset,
            base_model=base_model,
            scope=scope,
            consent=consent,
            configuration=configuration,
            budget=budget,
            attempt=attempt,
            fencing=fencing,
            artifact_target=artifact_target,
            deadline_at_ms=deadline_at_ms,
            binding_digest=binding_digest,
            resume=resume,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SpeechArtifactDescriptor:
    artifact_id: str
    artifact_ref: str
    sha256: str
    size_bytes: int
    media_type: str

    @classmethod
    def from_mapping(cls, value: Any) -> "SpeechArtifactDescriptor":
        data = _closed(
            value,
            "artifact",
            frozenset({"artifact_id", "artifact_ref", "sha256", "size_bytes", "media_type"}),
        )
        media_type = _text(data.get("media_type"), "artifact.media_type", maximum=128)
        if media_type not in {"application/vnd.ananta.speech-adapter", "application/json"}:
            raise SpeechAdaptationContractError(
                "speech_artifact_media_type_invalid",
                "artifact media type is not supported",
            )
        return cls(
            artifact_id=_identifier(data.get("artifact_id"), "artifact.artifact_id"),
            artifact_ref=_artifact_ref(
                data.get("artifact_ref"),
                "artifact.artifact_ref",
                prefix="artifact://speech-adapters/",
            ),
            sha256=_digest(data.get("sha256"), "artifact.sha256"),
            size_bytes=_integer(
                data.get("size_bytes"),
                "artifact.size_bytes",
                minimum=1,
                maximum=MAX_ARTIFACT_BYTES,
            ),
            media_type=media_type,
        )


@dataclass(frozen=True)
class SpeechAdaptationResult:
    contract_version: str
    result_type: str
    job_id: str
    attempt_id: str
    binding_digest: str
    fencing_digest: str
    status: str
    events_digest: str
    evaluation_report_digest: str | None
    checkpoint_digest: str | None
    artifact: SpeechArtifactDescriptor | None
    reason_code: str | None

    @classmethod
    def from_mapping(cls, value: Any) -> "SpeechAdaptationResult":
        data = _closed(
            value,
            "result",
            frozenset(
                {
                    "contract_version",
                    "result_type",
                    "job_id",
                    "attempt_id",
                    "binding_digest",
                    "fencing_digest",
                    "status",
                    "events_digest",
                    "evaluation_report_digest",
                    "checkpoint_digest",
                    "artifact",
                    "reason_code",
                }
            ),
        )
        version = _text(data.get("contract_version"), "contract_version", maximum=64)
        result_type = _text(data.get("result_type"), "result_type", maximum=64)
        if version != CONTRACT_VERSION or result_type != RESULT_TYPE:
            raise SpeechAdaptationContractError(
                "speech_result_contract_mismatch",
                "speech result version or type is unsupported",
            )
        status = _text(data.get("status"), "status", maximum=32)
        if status not in {"completed", "dataset_only", "cancelled", "failed"}:
            raise SpeechAdaptationContractError("speech_result_status_invalid", "speech result status is invalid")
        artifact = SpeechArtifactDescriptor.from_mapping(data.get("artifact")) if data.get("artifact") else None
        if status == "completed" and artifact is None:
            raise SpeechAdaptationContractError(
                "speech_result_artifact_missing",
                "completed speech training requires an adapter artifact",
            )
        if status != "completed" and artifact is not None:
            raise SpeechAdaptationContractError(
                "speech_result_artifact_forbidden",
                "non-completed speech training must not publish an adapter artifact",
            )
        evaluation_value = data.get("evaluation_report_digest")
        checkpoint_value = data.get("checkpoint_digest")
        if status == "completed" and (evaluation_value is None or checkpoint_value is None):
            raise SpeechAdaptationContractError(
                "speech_result_evidence_missing",
                "completed speech training requires evaluation and checkpoint evidence",
            )
        reason_value = data.get("reason_code")
        reason_code = _identifier(reason_value, "reason_code") if reason_value is not None else None
        if status in {"cancelled", "failed"} and reason_code is None:
            raise SpeechAdaptationContractError(
                "speech_result_reason_missing",
                "cancelled or failed speech training requires a reason code",
            )
        if status in {"completed", "dataset_only"} and reason_code is not None:
            raise SpeechAdaptationContractError(
                "speech_result_reason_forbidden",
                "successful or dataset-only speech results cannot carry an error reason",
            )
        return cls(
            contract_version=version,
            result_type=result_type,
            job_id=_identifier(data.get("job_id"), "job_id"),
            attempt_id=_identifier(data.get("attempt_id"), "attempt_id"),
            binding_digest=_digest(data.get("binding_digest"), "binding_digest"),
            fencing_digest=_digest(data.get("fencing_digest"), "fencing_digest"),
            status=status,
            events_digest=_digest(data.get("events_digest"), "events_digest"),
            evaluation_report_digest=(
                _digest(evaluation_value, "evaluation_report_digest") if evaluation_value is not None else None
            ),
            checkpoint_digest=(
                _digest(checkpoint_value, "checkpoint_digest") if checkpoint_value is not None else None
            ),
            artifact=artifact,
            reason_code=reason_code,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
