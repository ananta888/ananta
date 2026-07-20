"""Strict shared wire contracts for Hub-delegated speech reconciliation.

The module has no Hub or worker imports. Both sides validate the same closed
payloads before decrypting input or accepting output.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from ananta_contracts.speech_reconciliation_state import validate_stage

CONTRACT_VERSION = "ananta.speech-reconciliation.v1"
RESOURCE_FIELDS = (
    "wall_time_ms",
    "cpu_time_ms",
    "gpu_time_ms",
    "memory_byte_ms",
    "disk_bytes",
    "checkpoint_bytes",
    "energy_millijoules",
)
MAX_RESOURCE_VALUE = 2**63 - 1
MAX_SOURCE_DURATION_MS = 8 * 60 * 60 * 1000
MAX_RESEARCH_FACTOR = 100
NORMAL_MAX_FACTOR = 20
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_ARTIFACT = re.compile(r"^artifact://[A-Za-z0-9][A-Za-z0-9_./:-]{0,500}$")


class SpeechReconciliationContractError(ValueError):
    def __init__(self, reason_code: str, message: str | None = None) -> None:
        self.reason_code = reason_code
        super().__init__(message or reason_code)


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise SpeechReconciliationContractError("speech_reconciliation_not_canonical") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _closed(value: Any, *, fields: frozenset[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise SpeechReconciliationContractError("speech_reconciliation_shape_invalid", f"{name} must be an object")
    if set(value) != fields:
        raise SpeechReconciliationContractError(
            "speech_reconciliation_shape_invalid", f"{name} has unknown/missing fields"
        )
    return value


def _integer(value: Any, name: str, *, minimum: int = 0, maximum: int = MAX_RESOURCE_VALUE) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise SpeechReconciliationContractError("speech_reconciliation_integer_invalid", name)
    return value


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise SpeechReconciliationContractError("speech_reconciliation_identifier_invalid", name)
    return value


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise SpeechReconciliationContractError("speech_reconciliation_digest_invalid", name)
    return value


def _artifact(value: Any, name: str, *, prefix: str) -> str:
    if (
        not isinstance(value, str)
        or not _ARTIFACT.fullmatch(value)
        or not value.startswith(prefix)
        or ".." in value.split("/")
    ):
        raise SpeechReconciliationContractError("speech_reconciliation_artifact_ref_invalid", name)
    return value


@dataclass(frozen=True)
class SpeechResourceVector:
    wall_time_ms: int = 0
    cpu_time_ms: int = 0
    gpu_time_ms: int = 0
    memory_byte_ms: int = 0
    disk_bytes: int = 0
    checkpoint_bytes: int = 0
    energy_millijoules: int = 0

    @classmethod
    def from_mapping(cls, value: Any, name: str = "resources") -> "SpeechResourceVector":
        data = _closed(value, fields=frozenset(RESOURCE_FIELDS), name=name)
        return cls(**{field: _integer(data[field], f"{name}.{field}") for field in RESOURCE_FIELDS})

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    def add(self, other: "SpeechResourceVector") -> "SpeechResourceVector":
        values: dict[str, int] = {}
        for field in RESOURCE_FIELDS:
            value = getattr(self, field) + getattr(other, field)
            if value > MAX_RESOURCE_VALUE:
                raise SpeechReconciliationContractError("speech_reconciliation_budget_overflow")
            values[field] = value
        return SpeechResourceVector(**values)

    def subtract(self, other: "SpeechResourceVector") -> "SpeechResourceVector":
        values = {field: getattr(self, field) - getattr(other, field) for field in RESOURCE_FIELDS}
        if any(value < 0 for value in values.values()):
            raise SpeechReconciliationContractError("speech_reconciliation_budget_exceeded")
        return SpeechResourceVector(**values)

    def covers(self, other: "SpeechResourceVector") -> bool:
        return all(getattr(self, field) >= getattr(other, field) for field in RESOURCE_FIELDS)


@dataclass(frozen=True)
class SpeechReconciliationBudgetLedger:
    contract_version: str
    job_id: str
    attempt_id: str
    fencing_epoch: int
    sequence: int
    stage: str
    source_duration_ms: int
    compute_factor: int
    allocated: SpeechResourceVector
    reserved: SpeechResourceVector
    consumed: SpeechResourceVector
    remaining: SpeechResourceVector

    @classmethod
    def from_mapping(cls, value: Any) -> "SpeechReconciliationBudgetLedger":
        fields = frozenset(
            {
                "contract_version",
                "job_id",
                "attempt_id",
                "fencing_epoch",
                "sequence",
                "stage",
                "source_duration_ms",
                "compute_factor",
                "allocated",
                "reserved",
                "consumed",
                "remaining",
            }
        )
        data = _closed(value, fields=fields, name="budget_ledger")
        if data["contract_version"] != CONTRACT_VERSION:
            raise SpeechReconciliationContractError("speech_reconciliation_contract_version_invalid")
        try:
            stage = validate_stage(_identifier(data["stage"], "stage"))
        except ValueError as exc:
            raise SpeechReconciliationContractError("speech_reconciliation_stage_invalid") from exc
        allocated = SpeechResourceVector.from_mapping(data["allocated"], "allocated")
        reserved = SpeechResourceVector.from_mapping(data["reserved"], "reserved")
        consumed = SpeechResourceVector.from_mapping(data["consumed"], "consumed")
        remaining = SpeechResourceVector.from_mapping(data["remaining"], "remaining")
        expected = allocated.subtract(consumed.add(reserved))
        if remaining != expected:
            raise SpeechReconciliationContractError("speech_reconciliation_budget_arithmetic_invalid")
        return cls(
            contract_version=CONTRACT_VERSION,
            job_id=_identifier(data["job_id"], "job_id"),
            attempt_id=_identifier(data["attempt_id"], "attempt_id"),
            fencing_epoch=_integer(data["fencing_epoch"], "fencing_epoch", minimum=1),
            sequence=_integer(data["sequence"], "sequence", minimum=0),
            stage=stage,
            source_duration_ms=_integer(
                data["source_duration_ms"], "source_duration_ms", minimum=1, maximum=MAX_SOURCE_DURATION_MS
            ),
            compute_factor=_integer(data["compute_factor"], "compute_factor", minimum=1, maximum=MAX_RESEARCH_FACTOR),
            allocated=allocated,
            reserved=reserved,
            consumed=consumed,
            remaining=remaining,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SpeechReconciliationJob:
    contract_version: str
    job_id: str
    attempt_id: str
    fencing_token_digest: str
    fencing_epoch: int
    consent_id: str
    consent_version: int
    revocation_epoch: int
    input_manifest_digest: str
    input_lineage_digest: str
    input_artifact_ref: str
    policy_digest: str
    research_policy_ref: str | None
    source_duration_ms: int
    max_compute_factor: int
    ledger_sequence: int
    key_epoch: int
    deadline_at_ms: int
    stage: str

    @classmethod
    def from_mapping(
        cls,
        value: Any,
        *,
        now_ms: int | None = None,
    ) -> "SpeechReconciliationJob":
        fields = frozenset(cls.__dataclass_fields__)
        data = _closed(value, fields=fields, name="job")
        if data["contract_version"] != CONTRACT_VERSION:
            raise SpeechReconciliationContractError("speech_reconciliation_contract_version_invalid")
        factor = _integer(data["max_compute_factor"], "max_compute_factor", minimum=1, maximum=MAX_RESEARCH_FACTOR)
        research = data["research_policy_ref"]
        if research is not None:
            research = _artifact(research, "research_policy_ref", prefix="artifact://speech-policies/")
        if factor > NORMAL_MAX_FACTOR and research is None:
            raise SpeechReconciliationContractError("speech_reconciliation_research_policy_required")
        deadline_at_ms = _integer(data["deadline_at_ms"], "deadline_at_ms", minimum=1)
        if now_ms is not None:
            if deadline_at_ms <= now_ms:
                raise SpeechReconciliationContractError("speech_reconciliation_deadline_expired")
            if deadline_at_ms > now_ms + 30 * 24 * 60 * 60 * 1_000:
                raise SpeechReconciliationContractError("speech_reconciliation_deadline_out_of_bounds")
        return cls(
            contract_version=CONTRACT_VERSION,
            job_id=_identifier(data["job_id"], "job_id"),
            attempt_id=_identifier(data["attempt_id"], "attempt_id"),
            fencing_token_digest=_digest(data["fencing_token_digest"], "fencing_token_digest"),
            fencing_epoch=_integer(data["fencing_epoch"], "fencing_epoch", minimum=1),
            consent_id=_identifier(data["consent_id"], "consent_id"),
            consent_version=_integer(
                data["consent_version"],
                "consent_version",
                minimum=1,
                maximum=2**31 - 1,
            ),
            revocation_epoch=_integer(data["revocation_epoch"], "revocation_epoch"),
            input_manifest_digest=_digest(data["input_manifest_digest"], "input_manifest_digest"),
            input_lineage_digest=_digest(data["input_lineage_digest"], "input_lineage_digest"),
            input_artifact_ref=_artifact(
                data["input_artifact_ref"], "input_artifact_ref", prefix="artifact://speech-evidence/"
            ),
            policy_digest=_digest(data["policy_digest"], "policy_digest"),
            research_policy_ref=research,
            source_duration_ms=_integer(
                data["source_duration_ms"], "source_duration_ms", minimum=1, maximum=MAX_SOURCE_DURATION_MS
            ),
            max_compute_factor=factor,
            ledger_sequence=_integer(data["ledger_sequence"], "ledger_sequence"),
            key_epoch=_integer(data["key_epoch"], "key_epoch", minimum=1),
            deadline_at_ms=deadline_at_ms,
            stage=_validated_stage(data["stage"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_BINDING_FIELDS = {
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
}


def _result_bindings(data: Mapping[str, Any]) -> dict[str, Any]:
    if data["contract_version"] != CONTRACT_VERSION:
        raise SpeechReconciliationContractError("speech_reconciliation_contract_version_invalid")
    return {
        "contract_version": CONTRACT_VERSION,
        "job_id": _identifier(data["job_id"], "job_id"),
        "attempt_id": _identifier(data["attempt_id"], "attempt_id"),
        "fencing_token_digest": _digest(data["fencing_token_digest"], "fencing_token_digest"),
        "fencing_epoch": _integer(data["fencing_epoch"], "fencing_epoch", minimum=1),
        "consent_id": _identifier(data["consent_id"], "consent_id"),
        "consent_version": _integer(data["consent_version"], "consent_version", minimum=1),
        "revocation_epoch": _integer(data["revocation_epoch"], "revocation_epoch"),
        "input_manifest_digest": _digest(data["input_manifest_digest"], "input_manifest_digest"),
        "policy_digest": _digest(data["policy_digest"], "policy_digest"),
        "ledger_sequence": _integer(data["ledger_sequence"], "ledger_sequence"),
        "key_epoch": _integer(data["key_epoch"], "key_epoch", minimum=1),
    }


@dataclass(frozen=True)
class SpeechReconciliationCheckpoint:
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
    checkpoint_digest: str
    checkpoint_ref: str
    checkpoint_sequence: int
    stage: str
    state_digest: str

    @classmethod
    def from_mapping(cls, value: Any) -> "SpeechReconciliationCheckpoint":
        data = _closed(value, fields=frozenset(cls.__dataclass_fields__), name="checkpoint")
        bindings = _result_bindings(data)
        return cls(
            **bindings,
            checkpoint_digest=_digest(data["checkpoint_digest"], "checkpoint_digest"),
            checkpoint_ref=_artifact(
                data["checkpoint_ref"], "checkpoint_ref", prefix="artifact://speech-reconciliation-checkpoints/"
            ),
            checkpoint_sequence=_integer(data["checkpoint_sequence"], "checkpoint_sequence", minimum=1),
            stage=_validated_stage(data["stage"]),
            state_digest=_digest(data["state_digest"], "state_digest"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SpeechReconciliationResult:
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
    dataset_manifest_digest: str | None
    dataset_artifact_ref: str | None
    checkpoint_digest: str | None
    evaluation_digest: str | None
    adapter_digest: str | None
    resolved_count: int
    unresolved_count: int
    rejected_count: int
    quarantined_count: int
    reason_code: str

    @classmethod
    def from_mapping(cls, value: Any) -> "SpeechReconciliationResult":
        data = _closed(value, fields=frozenset(cls.__dataclass_fields__), name="result")
        bindings = _result_bindings(data)
        status = _identifier(data["status"], "status")
        if status not in {"completed", "dataset_only_completed", "failed", "cancelled"}:
            raise SpeechReconciliationContractError("speech_reconciliation_result_status_invalid")
        manifest = data["dataset_manifest_digest"]
        ref = data["dataset_artifact_ref"]
        if status in {"completed", "dataset_only_completed"}:
            manifest = _digest(manifest, "dataset_manifest_digest")
            ref = _artifact(ref, "dataset_artifact_ref", prefix="artifact://speech-datasets/")
        elif manifest is not None or ref is not None:
            raise SpeechReconciliationContractError("speech_reconciliation_result_artifact_forbidden")
        optional_digests = {
            name: _digest(data[name], name) if data[name] is not None else None
            for name in ("checkpoint_digest", "evaluation_digest", "adapter_digest")
        }
        return cls(
            **bindings,
            status=status,
            dataset_manifest_digest=manifest,
            dataset_artifact_ref=ref,
            **optional_digests,
            resolved_count=_integer(data["resolved_count"], "resolved_count"),
            unresolved_count=_integer(data["unresolved_count"], "unresolved_count"),
            rejected_count=_integer(data["rejected_count"], "rejected_count"),
            quarantined_count=_integer(data["quarantined_count"], "quarantined_count"),
            reason_code=_identifier(data["reason_code"], "reason_code"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assert_result_matches_job(
    job: SpeechReconciliationJob,
    result: SpeechReconciliationResult | SpeechReconciliationCheckpoint,
) -> None:
    checks = {
        "job_id": job.job_id,
        "attempt_id": job.attempt_id,
        "fencing_token_digest": job.fencing_token_digest,
        "fencing_epoch": job.fencing_epoch,
        "consent_id": job.consent_id,
        "consent_version": job.consent_version,
        "revocation_epoch": job.revocation_epoch,
        "input_manifest_digest": job.input_manifest_digest,
        "policy_digest": job.policy_digest,
        "key_epoch": job.key_epoch,
    }
    if any(getattr(result, field) != expected for field, expected in checks.items()):
        raise SpeechReconciliationContractError("speech_reconciliation_binding_mismatch")
    if result.ledger_sequence < job.ledger_sequence:
        raise SpeechReconciliationContractError("speech_reconciliation_ledger_stale")


def _validated_stage(value: Any) -> str:
    try:
        return validate_stage(_identifier(value, "stage"))
    except ValueError as exc:
        raise SpeechReconciliationContractError("speech_reconciliation_stage_invalid") from exc


__all__ = [
    "CONTRACT_VERSION",
    "MAX_RESEARCH_FACTOR",
    "NORMAL_MAX_FACTOR",
    "RESOURCE_FIELDS",
    "SpeechReconciliationBudgetLedger",
    "SpeechReconciliationCheckpoint",
    "SpeechReconciliationContractError",
    "SpeechReconciliationJob",
    "SpeechReconciliationResult",
    "SpeechResourceVector",
    "assert_result_matches_job",
    "canonical_sha256",
]
