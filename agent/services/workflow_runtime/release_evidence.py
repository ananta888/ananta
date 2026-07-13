"""Append-only runtime evidence records for workflow release verification.

This module owns serialization, execution bindings, hash-chain persistence and
loading.  Release policy evaluation remains in :mod:`release_gate`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.conformance import RuntimeObservation

WORKFLOW_RELEASE_INPUT_SCHEMA = "ananta.workflow_runtime_release_input.v1"
WORKFLOW_RELEASE_RUN_EVIDENCE_SCHEMA = "ananta.workflow_runtime_run_evidence.v2"
WORKFLOW_RELEASE_EVIDENCE_VERSION = "2.0.0"
EVIDENCE_CHAIN_GENESIS = "0" * 64
EVIDENCE_PATH_ENV = "ANANTA_WORKFLOW_RELEASE_EVIDENCE_PATH"
EVIDENCE_CONTRACT_HASH_ENV = "ANANTA_WORKFLOW_RELEASE_CONTRACT_HASH"
EVIDENCE_REVISION_ENV = "ANANTA_WORKFLOW_RELEASE_REVISION"
EVIDENCE_BUILD_ID_ENV = "ANANTA_WORKFLOW_RELEASE_BUILD_ID"
EVIDENCE_COMMAND_ID_ENV = "ANANTA_WORKFLOW_RELEASE_COMMAND_ID"
EVIDENCE_COMMAND_HASH_ENV = "ANANTA_WORKFLOW_RELEASE_COMMAND_HASH"
EVIDENCE_RUNTIME_ID_ENV = "ANANTA_WORKFLOW_RELEASE_RUNTIME_ID"
PROOF_CATEGORIES = (
    "port",
    "security",
    "recovery",
    "event",
    "checkpoint",
    "approval",
    "ledger",
    "artifact",
)
_PROOF_STATUSES = frozenset({"passed", "failed", "incompatible", "not_applicable"})


class ReleaseVerificationCommandPort(Protocol):
    command_id: str
    argv: tuple[str, ...]
    evidence_runtime_ids: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeRunEvidence:
    runtime_id: str
    runtime_version: str
    scenario_id: str
    iteration: int
    contract_hash: str
    capabilities: frozenset[str]
    durable: bool
    observation: RuntimeObservation
    proofs: Mapping[str, str]
    evidence_origin: str = "runtime_execution"
    revision: str = ""
    build_id: str = ""
    command_id: str = ""
    command_hash: str = ""
    run_id: str = ""
    chain_index: int = 0
    previous_record_hash: str = ""
    record_hash: str = ""

    def assert_valid(self) -> None:
        if self.evidence_origin != "runtime_execution":
            raise ValueError("workflow_release_evidence_origin_invalid")
        if self.iteration < 1 or not self.runtime_id or not self.runtime_version or not self.scenario_id:
            raise ValueError("workflow_release_evidence_binding_invalid")
        if self.observation.runtime_id != self.runtime_id:
            raise ValueError("workflow_release_evidence_observation_runtime_mismatch")
        if self.observation.capabilities != self.capabilities:
            raise ValueError("workflow_release_evidence_capabilities_mismatch")
        if set(self.proofs) != set(PROOF_CATEGORIES) or set(self.proofs.values()) - _PROOF_STATUSES:
            raise ValueError("workflow_release_evidence_proofs_invalid")
        if not all((self.revision, self.build_id, self.command_id, self.run_id)):
            raise ValueError("workflow_release_evidence_execution_binding_invalid")
        if self.chain_index < 1:
            raise ValueError("workflow_release_evidence_chain_index_invalid")
        for value, code in (
            (self.contract_hash, "contract_hash"),
            (self.command_hash, "command_hash"),
            (self.previous_record_hash, "previous_record_hash"),
            (self.record_hash, "record_hash"),
        ):
            if not _is_sha256(value):
                raise ValueError(f"workflow_release_evidence_{code}_invalid")
        if self.record_hash != sha256_json(self.to_dict(include_record_hash=False)):
            raise ValueError("workflow_release_evidence_record_hash_mismatch")

    def to_dict(self, *, include_record_hash: bool = True) -> dict[str, Any]:
        payload = {
            "schema": WORKFLOW_RELEASE_RUN_EVIDENCE_SCHEMA,
            "evidence_version": WORKFLOW_RELEASE_EVIDENCE_VERSION,
            "runtime_id": self.runtime_id,
            "runtime_version": self.runtime_version,
            "scenario_id": self.scenario_id,
            "iteration": self.iteration,
            "contract_hash": self.contract_hash,
            "capabilities": sorted(self.capabilities),
            "durable": self.durable,
            "observation": {
                "terminal_status": self.observation.terminal_status,
                "event_types": list(self.observation.event_types),
                "artifact_ids": list(self.observation.artifact_ids),
                "gate_ids": list(self.observation.gate_ids),
                "side_effect_operations": list(self.observation.side_effect_operations),
                "policy_decisions": list(self.observation.policy_decisions),
                "budget_usage": dict(sorted(self.observation.budget_usage.items())),
                "unsupported_reason": self.observation.unsupported_reason,
            },
            "proofs": dict(sorted(self.proofs.items())),
            "evidence_origin": self.evidence_origin,
            "revision": self.revision,
            "build_id": self.build_id,
            "command_id": self.command_id,
            "command_hash": self.command_hash,
            "run_id": self.run_id,
            "chain_index": self.chain_index,
            "previous_record_hash": self.previous_record_hash,
        }
        if include_record_hash:
            payload["record_hash"] = self.record_hash
        return payload

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "RuntimeRunEvidence":
        if raw.get("schema") != WORKFLOW_RELEASE_RUN_EVIDENCE_SCHEMA:
            raise ValueError("workflow_release_run_evidence_schema_unsupported")
        if raw.get("evidence_version") != WORKFLOW_RELEASE_EVIDENCE_VERSION:
            raise ValueError("workflow_release_run_evidence_version_unsupported")
        capabilities = _string_set(raw.get("capabilities"), "capabilities")
        observation_raw = raw.get("observation")
        proofs_raw = raw.get("proofs")
        if not isinstance(observation_raw, Mapping) or not isinstance(proofs_raw, Mapping):
            raise ValueError("workflow_release_run_evidence_payload_invalid")
        record = cls(
            runtime_id=str(raw.get("runtime_id") or ""),
            runtime_version=str(raw.get("runtime_version") or ""),
            scenario_id=str(raw.get("scenario_id") or ""),
            iteration=int(raw.get("iteration") or 0),
            contract_hash=str(raw.get("contract_hash") or ""),
            capabilities=capabilities,
            durable=bool(raw.get("durable", False)),
            observation=RuntimeObservation(
                runtime_id=str(raw.get("runtime_id") or ""),
                terminal_status=str(observation_raw.get("terminal_status") or ""),
                capabilities=capabilities,
                event_types=_string_tuple(observation_raw.get("event_types"), "event_types"),
                artifact_ids=_string_tuple(observation_raw.get("artifact_ids"), "artifact_ids"),
                gate_ids=_string_tuple(observation_raw.get("gate_ids"), "gate_ids", allow_empty=True),
                side_effect_operations=_string_tuple(
                    observation_raw.get("side_effect_operations"),
                    "side_effect_operations",
                    allow_empty=True,
                ),
                policy_decisions=_string_tuple(
                    observation_raw.get("policy_decisions"),
                    "policy_decisions",
                ),
                budget_usage=_nonnegative_numbers(observation_raw.get("budget_usage")),
                unsupported_reason=str(observation_raw.get("unsupported_reason") or ""),
            ),
            proofs={str(key): str(value) for key, value in proofs_raw.items()},
            evidence_origin=str(raw.get("evidence_origin") or ""),
            revision=str(raw.get("revision") or ""),
            build_id=str(raw.get("build_id") or ""),
            command_id=str(raw.get("command_id") or ""),
            command_hash=str(raw.get("command_hash") or ""),
            run_id=str(raw.get("run_id") or ""),
            chain_index=int(raw.get("chain_index") or 0),
            previous_record_hash=str(raw.get("previous_record_hash") or ""),
            record_hash=str(raw.get("record_hash") or ""),
        )
        record.assert_valid()
        return record


@dataclass(frozen=True)
class RuntimeEvidenceBinding:
    path: Path
    contract_hash: str
    revision: str
    build_id: str
    command_id: str
    command_hash: str
    runtime_id: str

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "RuntimeEvidenceBinding | None":
        source = os.environ if environ is None else environ
        path = str(source.get(EVIDENCE_PATH_ENV) or "").strip()
        if not path:
            return None
        values = {
            "contract_hash": str(source.get(EVIDENCE_CONTRACT_HASH_ENV) or "").strip(),
            "revision": str(source.get(EVIDENCE_REVISION_ENV) or "").strip(),
            "build_id": str(source.get(EVIDENCE_BUILD_ID_ENV) or "").strip(),
            "command_id": str(source.get(EVIDENCE_COMMAND_ID_ENV) or "").strip(),
            "command_hash": str(source.get(EVIDENCE_COMMAND_HASH_ENV) or "").strip(),
            "runtime_id": str(source.get(EVIDENCE_RUNTIME_ID_ENV) or "").strip(),
        }
        if not all(values.values()):
            raise ValueError("workflow_release_evidence_environment_incomplete")
        if not _is_sha256(values["contract_hash"]) or not _is_sha256(values["command_hash"]):
            raise ValueError("workflow_release_evidence_environment_hash_invalid")
        evidence_path = Path(path)
        if not evidence_path.is_absolute():
            raise ValueError("workflow_release_evidence_path_not_absolute")
        return cls(path=evidence_path, **values)


class RuntimeEvidenceSink:
    """Append-only, process-safe JSONL evidence chain for one command."""

    def __init__(self, binding: RuntimeEvidenceBinding) -> None:
        self._binding = binding

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "RuntimeEvidenceSink | None":
        binding = RuntimeEvidenceBinding.from_environment(environ)
        return cls(binding) if binding is not None else None

    def append(
        self,
        *,
        runtime_id: str,
        runtime_version: str,
        scenario_id: str,
        iteration: int,
        run_id: str,
        capabilities: frozenset[str],
        durable: bool,
        observation: RuntimeObservation,
        proofs: Mapping[str, str],
    ) -> RuntimeRunEvidence:
        if runtime_id != self._binding.runtime_id:
            raise ValueError("workflow_release_evidence_runtime_binding_mismatch")
        self._binding.path.parent.mkdir(parents=True, exist_ok=True)
        with self._binding.path.open("a+", encoding="utf-8") as handle:
            _lock_evidence_file(handle)
            try:
                handle.seek(0)
                existing = [
                    RuntimeRunEvidence.from_mapping(json.loads(line))
                    for line in handle
                    if line.strip()
                ]
                assert_evidence_chain(existing, expected_command_id=self._binding.command_id)
                ordered = sorted(existing, key=lambda item: item.chain_index)
                previous_hash = ordered[-1].record_hash if ordered else EVIDENCE_CHAIN_GENESIS
                record = RuntimeRunEvidence(
                    runtime_id=runtime_id,
                    runtime_version=runtime_version,
                    scenario_id=scenario_id,
                    iteration=iteration,
                    contract_hash=self._binding.contract_hash,
                    capabilities=capabilities,
                    durable=durable,
                    observation=observation,
                    proofs=dict(proofs),
                    revision=self._binding.revision,
                    build_id=self._binding.build_id,
                    command_id=self._binding.command_id,
                    command_hash=self._binding.command_hash,
                    run_id=run_id,
                    chain_index=len(existing) + 1,
                    previous_record_hash=previous_hash,
                )
                record = replace(
                    record,
                    record_hash=sha256_json(record.to_dict(include_record_hash=False)),
                )
                record.assert_valid()
                if any(item.run_id == record.run_id for item in existing):
                    raise ValueError("workflow_release_evidence_run_id_duplicate")
                handle.seek(0, os.SEEK_END)
                handle.write(_canonical_json(record.to_dict()) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                return record
            finally:
                _unlock_evidence_file(handle)


def record_runtime_release_evidence(**values: Any) -> RuntimeRunEvidence | None:
    """Probe helper: a no-op outside an explicitly bound release command."""

    sink = RuntimeEvidenceSink.from_environment()
    return sink.append(**values) if sink is not None else None


def load_runtime_release_evidence_jsonl(
    path: str | Path,
    *,
    expected_contract_hash: str,
    expected_revision: str | None = None,
    expected_build_id: str | None = None,
    expected_command_id: str | None = None,
    expected_command_hash: str | None = None,
) -> tuple[RuntimeRunEvidence, ...]:
    records = tuple(
        RuntimeRunEvidence.from_mapping(json.loads(line))
        for line in Path(path).resolve(strict=True).read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if not records:
        raise ValueError("workflow_release_evidence_records_missing")
    assert_evidence_chain(records, expected_command_id=expected_command_id)
    for record in records:
        if record.contract_hash != expected_contract_hash:
            raise ValueError("workflow_release_evidence_contract_hash_mismatch")
        if expected_revision is not None and record.revision != expected_revision:
            raise ValueError("workflow_release_evidence_revision_mismatch")
        if expected_build_id is not None and record.build_id != expected_build_id:
            raise ValueError("workflow_release_evidence_build_id_mismatch")
        if expected_command_hash is not None and record.command_hash != expected_command_hash:
            raise ValueError("workflow_release_evidence_command_hash_mismatch")
    return records


def load_runtime_release_evidence(
    path: str | Path,
    *,
    expected_contract_hash: str,
) -> tuple[RuntimeRunEvidence, ...]:
    raw = json.loads(Path(path).resolve(strict=True).read_text(encoding="utf-8"))
    if raw.get("schema") != WORKFLOW_RELEASE_INPUT_SCHEMA:
        raise ValueError("workflow_release_input_schema_unsupported")
    if raw.get("evidence_origin") == "fixture_only" or bool(raw.get("fixture_only", False)):
        raise ValueError("workflow_release_input_fixture_only")
    if raw.get("evidence_version") != WORKFLOW_RELEASE_EVIDENCE_VERSION:
        raise ValueError("workflow_release_input_version_unsupported")
    if raw.get("contract_hash") != expected_contract_hash:
        raise ValueError("workflow_release_input_contract_hash_mismatch")
    records_raw = raw.get("records")
    if not isinstance(records_raw, list) or not records_raw:
        raise ValueError("workflow_release_input_records_missing")
    records = tuple(
        RuntimeRunEvidence.from_mapping(item)
        for item in records_raw
        if isinstance(item, Mapping)
    )
    if len(records) != len(records_raw):
        raise ValueError("workflow_release_input_record_invalid")
    if any(record.contract_hash != expected_contract_hash for record in records):
        raise ValueError("workflow_release_input_record_contract_hash_mismatch")
    for command_id in sorted({record.command_id for record in records}):
        assert_evidence_chain(
            tuple(record for record in records if record.command_id == command_id),
            expected_command_id=command_id,
        )
    return records


@dataclass(frozen=True)
class VerificationCommandResult:
    command_id: str
    argv: tuple[str, ...]
    returncode: int
    tests: int
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    revision: str = ""
    build_id: str = ""
    command_hash: str = ""
    evidence_records: int = 0
    evidence_chain_head: str = ""

    @property
    def passed(self) -> bool:
        return (
            self.returncode == 0
            and self.tests > 0
            and self.failures == 0
            and self.errors == 0
            and self.skipped == 0
            and bool(self.revision and self.build_id and _is_sha256(self.command_hash))
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "argv": list(self.argv),
            "returncode": self.returncode,
            "tests": self.tests,
            "failures": self.failures,
            "errors": self.errors,
            "skipped": self.skipped,
            "revision": self.revision,
            "build_id": self.build_id,
            "command_hash": self.command_hash,
            "evidence_records": self.evidence_records,
            "evidence_chain_head": self.evidence_chain_head,
            "status": "passed" if self.passed else "failed",
        }


def release_verification_command_hash(
    command: ReleaseVerificationCommandPort,
    contract_hash: str,
) -> str:
    return sha256_json(
        {
            "command_id": command.command_id,
            "argv": list(command.argv),
            "evidence_runtime_ids": list(command.evidence_runtime_ids),
            "contract_hash": contract_hash,
        }
    )


def assert_evidence_chain(
    records: Sequence[RuntimeRunEvidence],
    *,
    expected_command_id: str | None,
) -> None:
    ordered = sorted(records, key=lambda item: item.chain_index)
    previous_hash = EVIDENCE_CHAIN_GENESIS
    for expected_index, record in enumerate(ordered, start=1):
        record.assert_valid()
        if record.chain_index != expected_index:
            raise ValueError("workflow_release_evidence_chain_index_gap")
        if expected_command_id is not None and record.command_id != expected_command_id:
            raise ValueError("workflow_release_evidence_command_id_mismatch")
        if record.previous_record_hash != previous_hash:
            raise ValueError("workflow_release_evidence_chain_broken")
        previous_hash = record.record_hash


def _string_tuple(value: Any, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"workflow_release_{field}_invalid")
    normalized = tuple(str(item) for item in value)
    if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        raise ValueError(f"workflow_release_{field}_invalid")
    return normalized


def _string_set(value: Any, field: str) -> frozenset[str]:
    return frozenset(_string_tuple(value, field))


def _nonnegative_numbers(value: Any) -> dict[str, int | float]:
    if not isinstance(value, dict) or not value:
        raise ValueError("workflow_release_budget_usage_invalid")
    result: dict[str, int | float] = {}
    for raw_key, raw_value in value.items():
        if (
            not str(raw_key)
            or isinstance(raw_value, bool)
            or not isinstance(raw_value, (int, float))
            or raw_value < 0
        ):
            raise ValueError("workflow_release_budget_usage_invalid")
        result[str(raw_key)] = raw_value
    return result


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _lock_evidence_file(handle: Any) -> None:
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_evidence_file(handle: Any) -> None:
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _is_sha256(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", str(value)))


__all__ = [
    "EVIDENCE_BUILD_ID_ENV",
    "EVIDENCE_CHAIN_GENESIS",
    "EVIDENCE_COMMAND_HASH_ENV",
    "EVIDENCE_COMMAND_ID_ENV",
    "EVIDENCE_CONTRACT_HASH_ENV",
    "EVIDENCE_PATH_ENV",
    "EVIDENCE_REVISION_ENV",
    "EVIDENCE_RUNTIME_ID_ENV",
    "PROOF_CATEGORIES",
    "RuntimeEvidenceBinding",
    "RuntimeEvidenceSink",
    "RuntimeRunEvidence",
    "VerificationCommandResult",
    "assert_evidence_chain",
    "load_runtime_release_evidence",
    "load_runtime_release_evidence_jsonl",
    "record_runtime_release_evidence",
    "release_verification_command_hash",
]
