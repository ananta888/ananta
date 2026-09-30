"""Immutable dataset, model, scope, consent, configuration, budget, attempt, fencing,
resume and artifact bindings of a speech-adaptation job.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ananta_contracts.speech_adaptation_primitives import (
    _artifact_ref,
    _boolean,
    canonical_sha256,
    _closed,
    _digest,
    _identifier,
    _integer,
    MAX_ARTIFACT_BYTES,
    MAX_BATCH_SIZE,
    MAX_CHECKPOINTS,
    MAX_DISK_BYTES,
    MAX_EVENTS,
    MAX_RAM_BYTES,
    MAX_STEPS,
    MAX_VRAM_BYTES,
    MAX_WALL_SECONDS,
    _number,
    SpeechAdaptationContractError,
    SUPPORTED_BACKENDS,
    SUPPORTED_DIRECTIONS,
    SUPPORTED_SCENARIOS,
    _text,
)


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


__all__ = [
    "SpeechArtifactTarget",
    "SpeechAttemptBinding",
    "SpeechBaseModelBinding",
    "SpeechConsentBinding",
    "SpeechDatasetBinding",
    "SpeechFencingBinding",
    "SpeechResourceBudget",
    "SpeechResumeBinding",
    "SpeechScopeBinding",
    "SpeechTrainingConfiguration",
    "speech_attempt_digest",
    "speech_budget_digest",
    "speech_configuration_digest",
    "speech_fencing_digest",
    "speech_job_binding_digest",
    "speech_scope_digest",
]
