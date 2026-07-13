"""Versioned, runtime-neutral workflow evaluation.

Deterministic contract and security checks are the only source of truth.  An
optional judge is injected through a narrow port and can only make an otherwise
passing evaluation stricter; it can never turn a deterministic failure into a
release decision.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Protocol

from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.conformance import (
    ConformanceIssue,
    ConformanceResult,
    RuntimeDifferentialEvaluator,
    RuntimeObservation,
    WorkflowConformanceEvaluator,
)
from agent.services.workflow_runtime.reference_workflows import ReferenceWorkflow

WORKFLOW_EVALUATION_SUITE_SCHEMA = "ananta.workflow_evaluation_suite.v1"
WORKFLOW_EVALUATION_REPORT_SCHEMA = "ananta.workflow_evaluation_report.v1"
DEFAULT_EVALUATION_SUITE_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "workflow_runtime" / "evaluation_suite.v1.json"
)
_ROOT = Path(__file__).resolve().parents[2]
_JUDGE_STATUSES = frozenset({"passed", "failed", "disagreed", "unavailable"})


@dataclass(frozen=True)
class EvaluationModelRef:
    model_ref: str
    model_version: str
    descriptor_path: str
    descriptor_sha256: str
    network_required: bool


@dataclass(frozen=True)
class WorkflowEvaluationSuite:
    suite_id: str
    suite_version: str
    dataset_id: str
    dataset_version: str
    catalog_path: str
    catalog_sha256: str
    rubric_id: str
    rubric_version: str
    deterministic_rules: tuple[str, ...]
    models: tuple[EvaluationModelRef, ...]
    artifact_version: str

    @property
    def allowed_model_refs(self) -> frozenset[str]:
        return frozenset(model.model_ref for model in self.models)

    @property
    def suite_hash(self) -> str:
        return sha256_json(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "suite_version": self.suite_version,
            "dataset": {
                "dataset_id": self.dataset_id,
                "dataset_version": self.dataset_version,
                "catalog_path": self.catalog_path,
                "catalog_sha256": self.catalog_sha256,
            },
            "rubric": {
                "rubric_id": self.rubric_id,
                "rubric_version": self.rubric_version,
                "deterministic_rules": list(self.deterministic_rules),
            },
            "models": [model.__dict__ for model in self.models],
            "artifact_version": self.artifact_version,
        }


def load_workflow_evaluation_suite(
    path: str | Path = DEFAULT_EVALUATION_SUITE_PATH,
) -> WorkflowEvaluationSuite:
    suite_path = Path(path).resolve(strict=True)
    raw = json.loads(suite_path.read_text(encoding="utf-8"))
    if raw.get("schema") != WORKFLOW_EVALUATION_SUITE_SCHEMA:
        raise ValueError("workflow_evaluation_suite_schema_unsupported")
    if raw.get("suite_version") != "1.0.0":
        raise ValueError("workflow_evaluation_suite_version_unsupported")

    dataset = _required_mapping(raw, "dataset")
    rubric = _required_mapping(raw, "rubric")
    models_raw = _required_mapping(raw, "models")
    artifact = _required_mapping(raw, "artifact")
    model_items = models_raw.get("allowed_model_refs")
    if not isinstance(model_items, list) or not model_items:
        raise ValueError("workflow_evaluation_model_registry_empty")

    catalog_path = _checked_repository_file(str(dataset.get("catalog_path") or ""))
    catalog_digest = _sha256_file(catalog_path)
    if catalog_digest != str(dataset.get("catalog_sha256") or ""):
        raise ValueError("workflow_evaluation_dataset_hash_mismatch")

    models: list[EvaluationModelRef] = []
    seen: set[str] = set()
    for item in model_items:
        if not isinstance(item, dict):
            raise ValueError("workflow_evaluation_model_ref_invalid")
        model = EvaluationModelRef(
            model_ref=str(item.get("model_ref") or ""),
            model_version=str(item.get("model_version") or ""),
            descriptor_path=str(item.get("descriptor_path") or ""),
            descriptor_sha256=str(item.get("descriptor_sha256") or ""),
            network_required=bool(item.get("network_required", True)),
        )
        if not model.model_ref or not model.model_version or model.model_ref in seen:
            raise ValueError("workflow_evaluation_model_ref_invalid")
        seen.add(model.model_ref)
        if _sha256_file(_checked_repository_file(model.descriptor_path)) != model.descriptor_sha256:
            raise ValueError("workflow_evaluation_model_descriptor_hash_mismatch")
        models.append(model)

    rules = rubric.get("deterministic_rules")
    if not isinstance(rules, list) or not rules or not all(isinstance(item, str) and item for item in rules):
        raise ValueError("workflow_evaluation_deterministic_rules_invalid")
    judge_policy = _required_mapping(rubric, "judge")
    if (
        judge_policy.get("request_is_explicit") is not True
        or judge_policy.get("non_pass_blocks_release_when_requested") is not True
        or judge_policy.get("may_override_deterministic_gate") is not False
    ):
        raise ValueError("workflow_evaluation_judge_policy_unsafe")
    if artifact.get("schema") != WORKFLOW_EVALUATION_REPORT_SCHEMA:
        raise ValueError("workflow_evaluation_artifact_schema_unsupported")

    return WorkflowEvaluationSuite(
        suite_id=str(raw.get("suite_id") or ""),
        suite_version=str(raw.get("suite_version") or ""),
        dataset_id=str(dataset.get("dataset_id") or ""),
        dataset_version=str(dataset.get("dataset_version") or ""),
        catalog_path=str(dataset.get("catalog_path") or ""),
        catalog_sha256=catalog_digest,
        rubric_id=str(rubric.get("rubric_id") or ""),
        rubric_version=str(rubric.get("rubric_version") or ""),
        deterministic_rules=tuple(rules),
        models=tuple(models),
        artifact_version=str(artifact.get("artifact_version") or ""),
    )


@dataclass(frozen=True)
class WorkflowJudgeRequest:
    scenario_id: str
    dataset_id: str
    dataset_version: str
    rubric_id: str
    rubric_version: str
    deterministic_status: str
    deterministic_report_hash: str
    runtime_ids: tuple[str, ...]
    capability_intersection: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowJudgeAssessment:
    status: str
    model_ref: str = ""
    model_version: str = ""
    score: float | None = None
    reason_codes: tuple[str, ...] = ()

    def assert_valid(self) -> None:
        if self.status not in _JUDGE_STATUSES:
            raise ValueError("workflow_evaluation_judge_status_invalid")
        if self.status != "unavailable" and (not self.model_ref or not self.model_version):
            raise ValueError("workflow_evaluation_judge_model_binding_required")
        if self.score is not None and (isinstance(self.score, bool) or not 0 <= self.score <= 1):
            raise ValueError("workflow_evaluation_judge_score_invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "model_ref": self.model_ref,
            "model_version": self.model_version,
            "score": self.score,
            "reason_codes": list(self.reason_codes),
        }


class WorkflowJudgePort(Protocol):
    """Optional semantic evaluator; implementations never receive secrets or raw prompts."""

    def evaluate(self, request: WorkflowJudgeRequest) -> WorkflowJudgeAssessment: ...


@dataclass(frozen=True)
class WorkflowEvaluationReport:
    scenario_id: str
    status: str
    release_eligible: bool
    deterministic_status: str
    conformance_results: tuple[ConformanceResult, ...]
    differential_issues: tuple[ConformanceIssue, ...]
    capability_intersection: tuple[str, ...]
    judge_requested: bool
    judge_assessment: WorkflowJudgeAssessment
    suite: WorkflowEvaluationSuite

    @property
    def report_hash(self) -> str:
        return sha256_json(self.to_dict(include_hash=False))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": WORKFLOW_EVALUATION_REPORT_SCHEMA,
            "artifact_version": self.suite.artifact_version,
            "scenario_id": self.scenario_id,
            "status": self.status,
            "release_eligible": self.release_eligible,
            "deterministic_gate": {
                "status": self.deterministic_status,
                "results": [result.to_dict() for result in self.conformance_results],
                "differential_issues": [issue.to_dict() for issue in self.differential_issues],
                "capability_intersection": list(self.capability_intersection),
            },
            "judge": {
                "requested": self.judge_requested,
                **self.judge_assessment.to_dict(),
                "may_override_deterministic_gate": False,
            },
            "versions": {
                "suite_id": self.suite.suite_id,
                "suite_version": self.suite.suite_version,
                "suite_hash": self.suite.suite_hash,
                "dataset_id": self.suite.dataset_id,
                "dataset_version": self.suite.dataset_version,
                "dataset_sha256": self.suite.catalog_sha256,
                "rubric_id": self.suite.rubric_id,
                "rubric_version": self.suite.rubric_version,
            },
        }
        if include_hash:
            payload["report_hash"] = sha256_json(payload)
        return payload


class WorkflowEvaluationService:
    """Combine deterministic conformance with an explicitly requested judge."""

    def __init__(
        self,
        *,
        suite: WorkflowEvaluationSuite | None = None,
        conformance: WorkflowConformanceEvaluator | None = None,
        differential: RuntimeDifferentialEvaluator | None = None,
    ) -> None:
        self._suite = suite or load_workflow_evaluation_suite()
        self._conformance = conformance or WorkflowConformanceEvaluator()
        self._differential = differential or RuntimeDifferentialEvaluator()

    def evaluate(
        self,
        scenario: ReferenceWorkflow,
        observations: tuple[RuntimeObservation, ...],
        *,
        request_judge: bool = False,
        judge: WorkflowJudgePort | None = None,
    ) -> WorkflowEvaluationReport:
        if not observations:
            raise ValueError("workflow_evaluation_observation_required")
        ordered = tuple(sorted(observations, key=lambda value: value.runtime_id))
        if len({item.runtime_id for item in ordered}) != len(ordered):
            raise ValueError("workflow_evaluation_runtime_duplicate")

        results = tuple(self._conformance.evaluate(scenario, item) for item in ordered)
        differential_issues: list[ConformanceIssue] = []
        for left, right in combinations(ordered, 2):
            differential_issues.extend(
                self._differential.compare(
                    left,
                    right,
                    required_capabilities=frozenset(scenario.plan.capabilities),
                )
            )

        statuses = {result.status for result in results}
        if "failed" in statuses:
            deterministic_status = "failed"
        elif "incompatible" in statuses:
            deterministic_status = "incompatible"
        elif differential_issues:
            deterministic_status = "failed"
        else:
            deterministic_status = "passed"
        common_capabilities = set(ordered[0].capabilities)
        for observation in ordered[1:]:
            common_capabilities.intersection_update(observation.capabilities)
        capability_intersection = tuple(sorted(common_capabilities))

        judge_assessment = WorkflowJudgeAssessment(status="unavailable", reason_codes=("judge_not_requested",))
        if request_judge:
            judge_assessment = self._judge(
                judge,
                scenario=scenario,
                deterministic_status=deterministic_status,
                results=results,
                differential_issues=tuple(differential_issues),
                runtime_ids=tuple(item.runtime_id for item in ordered),
                capability_intersection=capability_intersection,
            )

        release_eligible = deterministic_status == "passed" and (
            not request_judge or judge_assessment.status == "passed"
        )
        if deterministic_status != "passed":
            status = deterministic_status
        elif request_judge and judge_assessment.status != "passed":
            status = "degraded"
        else:
            status = "passed"
        return WorkflowEvaluationReport(
            scenario_id=scenario.scenario_id,
            status=status,
            release_eligible=release_eligible,
            deterministic_status=deterministic_status,
            conformance_results=results,
            differential_issues=tuple(differential_issues),
            capability_intersection=capability_intersection,
            judge_requested=request_judge,
            judge_assessment=judge_assessment,
            suite=self._suite,
        )

    def _judge(
        self,
        judge: WorkflowJudgePort | None,
        *,
        scenario: ReferenceWorkflow,
        deterministic_status: str,
        results: tuple[ConformanceResult, ...],
        differential_issues: tuple[ConformanceIssue, ...],
        runtime_ids: tuple[str, ...],
        capability_intersection: tuple[str, ...],
    ) -> WorkflowJudgeAssessment:
        if judge is None:
            return WorkflowJudgeAssessment(status="unavailable", reason_codes=("judge_unavailable",))
        deterministic_payload = {
            "scenario_id": scenario.scenario_id,
            "status": deterministic_status,
            "results": [result.to_dict() for result in results],
            "differential_issues": [issue.to_dict() for issue in differential_issues],
        }
        request = WorkflowJudgeRequest(
            scenario_id=scenario.scenario_id,
            dataset_id=self._suite.dataset_id,
            dataset_version=self._suite.dataset_version,
            rubric_id=self._suite.rubric_id,
            rubric_version=self._suite.rubric_version,
            deterministic_status=deterministic_status,
            deterministic_report_hash=sha256_json(deterministic_payload),
            runtime_ids=runtime_ids,
            capability_intersection=capability_intersection,
        )
        try:
            assessment = judge.evaluate(request)
            assessment.assert_valid()
        except Exception:
            return WorkflowJudgeAssessment(status="unavailable", reason_codes=("judge_execution_failed",))
        if assessment.status == "unavailable":
            return assessment
        if assessment.model_ref not in self._suite.allowed_model_refs:
            return WorkflowJudgeAssessment(status="unavailable", reason_codes=("judge_model_ref_not_allowed",))
        configured = next(model for model in self._suite.models if model.model_ref == assessment.model_ref)
        if assessment.model_version != configured.model_version:
            return WorkflowJudgeAssessment(status="unavailable", reason_codes=("judge_model_version_mismatch",))
        return assessment


def _required_mapping(value: dict[str, Any], key: str) -> dict[str, Any]:
    item = value.get(key)
    if not isinstance(item, dict):
        raise ValueError(f"workflow_evaluation_{key}_invalid")
    return item


def _checked_repository_file(value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("workflow_evaluation_path_invalid")
    resolved = (_ROOT / relative).resolve(strict=True)
    if not resolved.is_file() or not resolved.is_relative_to(_ROOT):
        raise ValueError("workflow_evaluation_path_invalid")
    return resolved


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = [
    "EvaluationModelRef",
    "WorkflowEvaluationReport",
    "WorkflowEvaluationService",
    "WorkflowEvaluationSuite",
    "WorkflowJudgeAssessment",
    "WorkflowJudgePort",
    "WorkflowJudgeRequest",
    "load_workflow_evaluation_suite",
]
