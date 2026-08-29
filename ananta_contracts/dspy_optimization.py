"""Dependency-free contracts for Hub-governed prompt optimization."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Mapping

from ananta_contracts.provider_execution import ProviderExecutionBinding

DSPY_OPTIMIZATION_SPEC_SCHEMA = "ananta.dspy-optimization-spec.v1"
PROMPT_PROGRAM_SCHEMA = "ananta.prompt-program.v1"
DATASET_MANIFEST_SCHEMA = "ananta.optimization-dataset.v1"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_PROGRAM_KINDS = frozenset({"planning_structured_tasks", "rag_answer", "structured_extraction"})
_OPTIMIZERS = frozenset({"labeled_few_shot", "bootstrap_few_shot"})
_ROLES = frozenset({"student", "teacher", "judge", "prompt_proposer"})


class OptimizationRunState(StrEnum):
    REQUESTED = "requested"
    ADMITTED = "admitted"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"
    COMPLETED = "completed"

    def assert_transition(self, target: "OptimizationRunState") -> None:
        allowed = {
            self.REQUESTED: {self.ADMITTED, self.CANCELLED, self.FAILED},
            self.ADMITTED: {self.RUNNING, self.CANCELLING, self.CANCELLED, self.FAILED},
            self.RUNNING: {self.CANCELLING, self.CANCELLED, self.FAILED, self.COMPLETED},
            self.CANCELLING: {self.CANCELLED, self.FAILED},
            self.CANCELLED: set(),
            self.FAILED: set(),
            self.COMPLETED: set(),
        }[self]
        if target not in allowed:
            raise ValueError("dspy_run_transition_invalid")


@dataclass(frozen=True, slots=True)
class OptimizationBudgets:
    max_model_calls: int
    max_tokens: int
    max_cost_micros: int
    timeout_seconds: int
    max_concurrency: int
    max_dataset_records: int
    max_artifact_bytes: int
    max_retries: int = 2

    def __post_init__(self) -> None:
        limits = (
            (self.max_model_calls, 1, 10_000),
            (self.max_tokens, 1, 50_000_000),
            (self.max_cost_micros, 0, 10_000_000_000),
            (self.timeout_seconds, 1, 86_400),
            (self.max_concurrency, 1, 16),
            (self.max_dataset_records, 1, 100_000),
            (self.max_artifact_bytes, 1_024, 100 * 1024 * 1024),
            (self.max_retries, 0, 10),
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high
            for value, low, high in limits
        ):
            raise ValueError("dspy_budget_invalid")


@dataclass(frozen=True, slots=True)
class OptimizationSpecV1:
    tenant_id: str
    spec_id: str
    program_kind: str
    dataset_manifest_digest: str
    metric_set_digest: str
    optimizer_id: str
    optimizer_config_digest: str
    seed: int
    provider_bindings: Mapping[str, Mapping[str, Any]]
    budgets: OptimizationBudgets | Mapping[str, Any]
    schema: str = DSPY_OPTIMIZATION_SPEC_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != DSPY_OPTIMIZATION_SPEC_SCHEMA:
            raise ValueError("dspy_spec_schema_unsupported")
        for value, field in ((self.tenant_id, "tenant_id"), (self.spec_id, "spec_id")):
            require_id(value, field)
        if self.program_kind not in _PROGRAM_KINDS:
            raise ValueError("dspy_program_kind_denied")
        if self.optimizer_id not in _OPTIMIZERS:
            raise ValueError("dspy_optimizer_denied")
        for value, field in (
            (self.dataset_manifest_digest, "dataset_manifest_digest"),
            (self.metric_set_digest, "metric_set_digest"),
            (self.optimizer_config_digest, "optimizer_config_digest"),
        ):
            require_digest(value, field)
        if not 0 <= self.seed <= 2**31 - 1:
            raise ValueError("dspy_seed_invalid")
        object.__setattr__(self, "budgets", _budgets(self.budgets))
        bindings: dict[str, dict[str, Any]] = {}
        if not self.provider_bindings:
            raise ValueError("dspy_provider_bindings_required")
        for role, raw in self.provider_bindings.items():
            if role not in _ROLES:
                raise ValueError("dspy_provider_role_invalid")
            bindings[role] = ProviderExecutionBinding.from_mapping(raw).to_dict()
        if "student" not in bindings:
            raise ValueError("dspy_student_binding_required")
        object.__setattr__(self, "provider_bindings", bindings)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["budgets"] = asdict(self.budgets)
        value["provider_bindings"] = {key: dict(item) for key, item in sorted(self.provider_bindings.items())}
        return value

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "OptimizationSpecV1":
        allowed = set(cls.__dataclass_fields__)
        if set(raw) - allowed:
            raise ValueError("dspy_spec_unknown_field")
        return cls(**dict(raw))


@dataclass(frozen=True, slots=True)
class DatasetManifestV1:
    tenant_id: str
    dataset_id: str
    version: int
    content_digest: str
    record_schema_digest: str
    split_digests: Mapping[str, str]
    split_record_ids: Mapping[str, tuple[str, ...] | list[str]]
    license_id: str
    sensitivity: str
    retention_days: int
    source_refs: tuple[str, ...] | list[str] = ()
    schema: str = DATASET_MANIFEST_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != DATASET_MANIFEST_SCHEMA or self.version < 1:
            raise ValueError("dspy_dataset_schema_invalid")
        require_id(self.tenant_id, "tenant_id")
        require_id(self.dataset_id, "dataset_id")
        require_id(self.license_id, "license_id")
        require_digest(self.content_digest, "content_digest")
        require_digest(self.record_schema_digest, "record_schema_digest")
        if set(self.split_digests) != {"train", "validation", "test"}:
            raise ValueError("dspy_dataset_splits_invalid")
        if set(self.split_record_ids) != {"train", "validation", "test"}:
            raise ValueError("dspy_dataset_splits_invalid")
        for digest in self.split_digests.values():
            require_digest(digest, "split_digest")
        normalized = {key: tuple(str(item) for item in values) for key, values in self.split_record_ids.items()}
        sets = [set(values) for values in normalized.values()]
        if any(len(values) != len(set(values)) for values in normalized.values()) or any(
            left & right for index, left in enumerate(sets) for right in sets[index + 1 :]
        ):
            raise ValueError("dspy_dataset_split_leakage")
        if sum(len(values) for values in normalized.values()) > 100_000:
            raise ValueError("dspy_dataset_too_large")
        if self.sensitivity not in {"public", "internal", "confidential", "restricted"}:
            raise ValueError("dspy_dataset_sensitivity_invalid")
        if not 1 <= self.retention_days <= 3_650:
            raise ValueError("dspy_dataset_retention_invalid")
        if any(not str(value).startswith("SRC_") for value in self.source_refs):
            raise ValueError("dspy_dataset_source_ref_invalid")
        object.__setattr__(self, "split_record_ids", normalized)
        object.__setattr__(self, "source_refs", tuple(self.source_refs))

    @property
    def digest(self) -> str:
        return canonical_digest(asdict(self))


@dataclass(frozen=True, slots=True)
class PromptProgramV1:
    tenant_id: str
    program_id: str
    program_kind: str
    module_graph: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]]
    signatures: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]]
    demonstrations: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]]
    model_roles: Mapping[str, str]
    source_program_digest: str
    exporter_version: str
    schema: str = PROMPT_PROGRAM_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != PROMPT_PROGRAM_SCHEMA:
            raise ValueError("dspy_prompt_program_schema_unsupported")
        require_id(self.tenant_id, "tenant_id")
        require_id(self.program_id, "program_id")
        require_id(self.exporter_version, "exporter_version")
        require_digest(self.source_program_digest, "source_program_digest")
        if self.program_kind not in _PROGRAM_KINDS:
            raise ValueError("dspy_program_kind_denied")
        for collection in (self.module_graph, self.signatures, self.demonstrations):
            if not isinstance(collection, (tuple, list)) or len(collection) > 256:
                raise ValueError("dspy_prompt_program_collection_invalid")
        rendered = canonical_json(asdict(self))
        if len(rendered.encode()) > 5 * 1024 * 1024:
            raise ValueError("dspy_prompt_program_too_large")
        _reject_unsafe_state(asdict(self))
        object.__setattr__(self, "module_graph", tuple(dict(item) for item in self.module_graph))
        object.__setattr__(self, "signatures", tuple(dict(item) for item in self.signatures))
        object.__setattr__(self, "demonstrations", tuple(dict(item) for item in self.demonstrations))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("module_graph", "signatures", "demonstrations"):
            value[key] = [dict(item) for item in getattr(self, key)]
        return value

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def require_id(value: object, field: str) -> str:
    candidate = str(value or "").strip()
    if not _ID.fullmatch(candidate):
        raise ValueError(f"dspy_{field}_invalid")
    return candidate


def require_digest(value: object, field: str) -> str:
    candidate = str(value or "").strip()
    if not _DIGEST.fullmatch(candidate):
        raise ValueError(f"dspy_{field}_invalid")
    return candidate


def _budgets(value: OptimizationBudgets | Mapping[str, Any]) -> OptimizationBudgets:
    return value if isinstance(value, OptimizationBudgets) else OptimizationBudgets(**dict(value))


def _reject_unsafe_state(value: Any, path: tuple[str, ...] = ()) -> None:
    forbidden = {
        "api_key",
        "authorization",
        "base_url",
        "api_base",
        "model_list",
        "callable",
        "class_path",
        "file_path",
    }
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in forbidden:
                raise ValueError("dspy_prompt_program_unsafe_state")
            _reject_unsafe_state(item, (*path, str(key)))
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_unsafe_state(item, path)
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise ValueError("dspy_prompt_program_non_json_state")


__all__ = [
    "DATASET_MANIFEST_SCHEMA",
    "DSPY_OPTIMIZATION_SPEC_SCHEMA",
    "PROMPT_PROGRAM_SCHEMA",
    "DatasetManifestV1",
    "OptimizationBudgets",
    "OptimizationRunState",
    "OptimizationSpecV1",
    "PromptProgramV1",
    "canonical_digest",
    "canonical_json",
]
