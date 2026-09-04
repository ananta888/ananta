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
OPTIMIZATION_RUN_SCHEMA = "ananta.dspy-optimization-run.v1"
PROMPT_PROGRAM_SCHEMA = "ananta.prompt-program.v1"
DATASET_MANIFEST_SCHEMA = "ananta.optimization-dataset.v1"
PROMOTION_PLAN_SCHEMA = "ananta.dspy-promotion-plan.v1"
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
class OptimizationRunV1:
    tenant_id: str
    run_id: str
    attempt_id: str
    state: OptimizationRunState | str
    revision: int
    spec_digest: str
    created_at: str
    updated_at: str
    reason_code: str
    artifact: Mapping[str, Any] | None = None
    usage: Mapping[str, int] | None = None
    schema: str = OPTIMIZATION_RUN_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != OPTIMIZATION_RUN_SCHEMA:
            raise ValueError("dspy_run_schema_unsupported")
        for value, field in (
            (self.tenant_id, "tenant_id"),
            (self.run_id, "run_id"),
            (self.attempt_id, "attempt_id"),
            (self.reason_code, "reason_code"),
        ):
            require_id(value, field)
        require_digest(self.spec_digest, "spec_digest")
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("dspy_run_revision_invalid")
        try:
            state = OptimizationRunState(self.state)
        except ValueError as exc:
            raise ValueError("dspy_run_state_invalid") from exc
        for value in (self.created_at, self.updated_at):
            if not isinstance(value, str) or len(value) > 40 or not value.endswith("Z"):
                raise ValueError("dspy_run_timestamp_invalid")
        artifact = None if self.artifact is None else dict(self.artifact)
        if artifact is not None:
            allowed = {"schema", "digest", "size_bytes", "media_type", "producer_run_id", "tenant_id", "artifact_ref"}
            if set(artifact) - allowed:
                raise ValueError("dspy_run_artifact_invalid")
            require_digest(artifact.get("digest"), "artifact_digest")
        usage = None if self.usage is None else dict(self.usage)
        if usage is not None:
            if set(usage) != {"model_calls", "tokens", "cost_micros"} or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in usage.values()
            ):
                raise ValueError("dspy_run_usage_invalid")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "artifact", artifact)
        object.__setattr__(self, "usage", usage)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        return value

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "OptimizationRunV1":
        _require_closed(raw, set(cls.__dataclass_fields__), "dspy_run_unknown_field")
        return cls(**dict(raw))


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
    max_trials: int = 10
    max_role_calls: int = 100

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
            (self.max_trials, 1, 1_000),
            (self.max_role_calls, 1, 10_000),
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
    lineage: Mapping[str, str] | None = None
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
        lineage = None if self.lineage is None else dict(self.lineage)
        if lineage is not None:
            if set(lineage) != {"run_id", "program_digest", "model_set_digest", "metric_digest", "evaluation_digest"}:
                raise ValueError("dspy_dataset_lineage_invalid")
            require_id(lineage["run_id"], "run_id")
            for field in set(lineage) - {"run_id"}:
                require_digest(lineage[field], field)
        object.__setattr__(self, "split_record_ids", normalized)
        object.__setattr__(self, "source_refs", tuple(self.source_refs))
        object.__setattr__(self, "lineage", lineage)

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
    scope: Mapping[str, str] | None = None
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
        scope = dict(self.scope or {})
        allowed_scope = {
            "planning_structured_tasks": {"language", "planning_mode", "model_profile", "output_schema"},
            "rag_answer": {"language", "repository_id", "retrieval_profile", "output_schema"},
            "structured_extraction": {"language", "input_schema", "output_schema"},
        }[self.program_kind]
        if set(scope) - allowed_scope or any(not require_id(value, "program_scope") for value in scope.values()):
            raise ValueError("dspy_prompt_program_scope_invalid")
        object.__setattr__(self, "module_graph", tuple(dict(item) for item in self.module_graph))
        object.__setattr__(self, "signatures", tuple(dict(item) for item in self.signatures))
        object.__setattr__(self, "demonstrations", tuple(dict(item) for item in self.demonstrations))
        object.__setattr__(self, "scope", scope)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("module_graph", "signatures", "demonstrations"):
            value[key] = [dict(item) for item in getattr(self, key)]
        return value

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PromptProgramV1":
        _require_closed(raw, set(cls.__dataclass_fields__), "dspy_program_unknown_field")
        return cls(**dict(raw))


@dataclass(frozen=True, slots=True)
class PromotionPlanV1:
    tenant_id: str
    scope_id: str
    candidate_digest: str
    baseline_digest: str
    evaluation_digest: str
    dataset_digest: str
    metric_set_digest: str
    thresholds_digest: str
    expected_registry_revision: int
    canary_percent: int
    automatic_stop_reason_codes: tuple[str, ...] | list[str]
    canary_duration_seconds: int = 86_400
    minimum_sample_size: int = 100
    schema: str = PROMOTION_PLAN_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != PROMOTION_PLAN_SCHEMA:
            raise ValueError("dspy_promotion_plan_schema_unsupported")
        require_id(self.tenant_id, "tenant_id")
        require_id(self.scope_id, "scope_id")
        for value, field in (
            (self.candidate_digest, "candidate_digest"),
            (self.baseline_digest, "baseline_digest"),
            (self.evaluation_digest, "evaluation_digest"),
            (self.dataset_digest, "dataset_digest"),
            (self.metric_set_digest, "metric_set_digest"),
            (self.thresholds_digest, "thresholds_digest"),
        ):
            require_digest(value, field)
        if (
            not isinstance(self.expected_registry_revision, int)
            or isinstance(self.expected_registry_revision, bool)
            or self.expected_registry_revision < 0
        ):
            raise ValueError("dspy_promotion_revision_invalid")
        if (
            not isinstance(self.canary_percent, int)
            or isinstance(self.canary_percent, bool)
            or not 1 <= self.canary_percent <= 100
        ):
            raise ValueError("dspy_canary_percent_invalid")
        if not 60 <= self.canary_duration_seconds <= 30 * 86_400:
            raise ValueError("dspy_canary_duration_invalid")
        if not 20 <= self.minimum_sample_size <= 1_000_000:
            raise ValueError("dspy_canary_sample_size_invalid")
        reasons = tuple(self.automatic_stop_reason_codes)
        if not reasons or len(reasons) > 32:
            raise ValueError("dspy_promotion_stop_policy_invalid")
        for reason in reasons:
            require_id(reason, "stop_reason_code")
        object.__setattr__(self, "automatic_stop_reason_codes", reasons)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["automatic_stop_reason_codes"] = list(self.automatic_stop_reason_codes)
        return value

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PromotionPlanV1":
        _require_closed(raw, set(cls.__dataclass_fields__), "dspy_promotion_plan_unknown_field")
        return cls(**dict(raw))


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def upcast_prompt_program(raw: Mapping[str, Any]) -> PromptProgramV1:
    """Validate the only supported schema without mutating the source payload."""

    return upcast_prompt_program_with_provenance(raw)[0]


def upcast_prompt_program_with_provenance(raw: Mapping[str, Any]) -> tuple[PromptProgramV1, dict[str, Any]]:
    """Apply deterministic additive defaults and retain the immutable source digest."""

    if raw.get("schema") != PROMPT_PROGRAM_SCHEMA:
        raise ValueError("dspy_prompt_program_upcast_unavailable")
    source = dict(raw)
    source_digest = canonical_digest(source)
    migrated = {**source, "scope": dict(source.get("scope") or {})}
    program = PromptProgramV1.from_mapping(migrated)
    return program, {
        "schema": "ananta.prompt-program-migration.v1",
        "source_digest": source_digest,
        "result_digest": program.digest,
        "source_schema": str(raw["schema"]),
        "result_schema": program.schema,
        "transformations": [] if "scope" in source else ["add_empty_scope"],
    }


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


def _require_closed(raw: Mapping[str, Any], allowed: set[str], reason: str) -> None:
    if set(raw) - allowed:
        raise ValueError(reason)


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
    "OPTIMIZATION_RUN_SCHEMA",
    "PROMPT_PROGRAM_SCHEMA",
    "PROMOTION_PLAN_SCHEMA",
    "DatasetManifestV1",
    "OptimizationBudgets",
    "OptimizationRunV1",
    "OptimizationRunState",
    "PromotionPlanV1",
    "OptimizationSpecV1",
    "PromptProgramV1",
    "canonical_digest",
    "canonical_json",
    "upcast_prompt_program",
    "upcast_prompt_program_with_provenance",
]
