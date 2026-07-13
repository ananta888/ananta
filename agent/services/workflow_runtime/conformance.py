"""Runtime-neutral workflow conformance and differential evaluation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent.services.workflow_runtime.reference_workflows import ReferenceWorkflow

CONFORMANCE_REPORT_SCHEMA = "ananta.workflow_conformance_report.v1"
CONFORMANCE_STATUSES = frozenset({"passed", "failed", "incompatible", "expected_failure"})


@dataclass(frozen=True)
class RuntimeObservation:
    runtime_id: str
    terminal_status: str
    capabilities: frozenset[str] = frozenset()
    event_types: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    gate_ids: tuple[str, ...] = ()
    side_effect_operations: tuple[str, ...] = ()
    policy_decisions: tuple[str, ...] = ()
    budget_usage: dict[str, int | float] = field(default_factory=dict)
    unsupported_reason: str = ""


class ConformanceRuntimePort(Protocol):
    """Small adapter seam; framework-specific types never cross this boundary."""

    @property
    def runtime_id(self) -> str: ...

    @property
    def capabilities(self) -> frozenset[str]: ...

    def observe(self, scenario: ReferenceWorkflow) -> RuntimeObservation: ...


@dataclass(frozen=True)
class ConformanceIssue:
    code: str
    expected: tuple[str, ...] = ()
    actual: tuple[str, ...] = ()
    event_sequence: tuple[str, ...] = ()
    minimal_reproduction: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "expected": list(self.expected),
            "actual": list(self.actual),
            "event_sequence": list(self.event_sequence),
            "minimal_reproduction": dict(self.minimal_reproduction),
        }


@dataclass(frozen=True)
class ConformanceResult:
    runtime_id: str
    scenario_id: str
    status: str
    issues: tuple[ConformanceIssue, ...] = ()
    observed_terminal_status: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CONFORMANCE_REPORT_SCHEMA,
            "runtime_id": self.runtime_id,
            "scenario_id": self.scenario_id,
            "status": self.status,
            "observed_terminal_status": self.observed_terminal_status,
            "issues": [issue.to_dict() for issue in self.issues],
        }


class WorkflowConformanceEvaluator:
    """Evaluates declared invariants without comparing nondeterministic text."""

    def evaluate(
        self,
        scenario: ReferenceWorkflow,
        observation: RuntimeObservation,
        *,
        expected_failure: bool = False,
    ) -> ConformanceResult:
        support = (
            "target"
            if observation.runtime_id == DeterministicReferenceRuntime.runtime_id
            else scenario.support_for(observation.runtime_id)
        )
        required_capabilities = set(scenario.plan.capabilities)
        missing_capabilities = required_capabilities - set(observation.capabilities)
        if support == "incompatible" or observation.unsupported_reason or missing_capabilities:
            reasons = tuple(
                sorted(
                    ({observation.unsupported_reason} if observation.unsupported_reason else set())
                    | {f"missing_capability:{value}" for value in missing_capabilities}
                )
            )
            return ConformanceResult(
                runtime_id=observation.runtime_id,
                scenario_id=scenario.scenario_id,
                status="incompatible",
                issues=(
                    self._issue(
                        "runtime_incompatible",
                        scenario=scenario,
                        observation=observation,
                        expected=reasons,
                    ),
                ),
                observed_terminal_status=observation.terminal_status,
            )

        invariants = scenario.invariants
        issues: list[ConformanceIssue] = []
        if observation.terminal_status not in invariants.terminal_statuses:
            issues.append(
                self._issue(
                    "terminal_status_mismatch",
                    scenario=scenario,
                    observation=observation,
                    expected=invariants.terminal_statuses,
                    actual=(observation.terminal_status,),
                )
            )
        self._require_subset(
            issues,
            "required_events_missing",
            invariants.required_event_types,
            observation.event_types,
            scenario=scenario,
            observation=observation,
        )
        self._require_subset(
            issues,
            "required_artifacts_missing",
            invariants.required_artifacts,
            observation.artifact_ids,
            scenario=scenario,
            observation=observation,
        )
        self._require_subset(
            issues,
            "required_gates_missing",
            invariants.required_gates,
            observation.gate_ids,
            scenario=scenario,
            observation=observation,
        )
        self._require_exact_set(
            issues,
            "side_effect_operations_mismatch",
            invariants.side_effect_operations,
            observation.side_effect_operations,
            scenario=scenario,
            observation=observation,
        )
        self._require_subset(
            issues,
            "required_policy_decisions_missing",
            invariants.required_policy_decisions,
            observation.policy_decisions,
            scenario=scenario,
            observation=observation,
        )
        if any(value < 0 for value in observation.budget_usage.values()):
            issues.append(
                self._issue(
                    "budget_usage_invalid",
                    scenario=scenario,
                    observation=observation,
                )
            )
        for budget_name, limit in invariants.budget_limits.items():
            actual = observation.budget_usage.get(budget_name)
            if actual is None:
                issues.append(
                    self._issue(
                        "budget_dimension_missing",
                        scenario=scenario,
                        observation=observation,
                        expected=(budget_name,),
                    )
                )
            elif actual > limit:
                issues.append(
                    self._issue(
                        "budget_limit_exceeded",
                        scenario=scenario,
                        observation=observation,
                        expected=(f"{budget_name}<={limit}",),
                        actual=(f"{budget_name}={actual}",),
                    )
                )

        status = "failed" if issues else "passed"
        if issues and expected_failure:
            status = "expected_failure"
        return ConformanceResult(
            runtime_id=observation.runtime_id,
            scenario_id=scenario.scenario_id,
            status=status,
            issues=tuple(issues),
            observed_terminal_status=observation.terminal_status,
        )

    @classmethod
    def _require_subset(
        cls,
        issues: list[ConformanceIssue],
        code: str,
        expected: tuple[str, ...],
        actual: tuple[str, ...],
        *,
        scenario: ReferenceWorkflow,
        observation: RuntimeObservation,
    ) -> None:
        missing = set(expected) - set(actual)
        if missing:
            issues.append(
                cls._issue(
                    code,
                    scenario=scenario,
                    observation=observation,
                    expected=tuple(sorted(missing)),
                    actual=actual,
                )
            )

    @classmethod
    def _require_exact_set(
        cls,
        issues: list[ConformanceIssue],
        code: str,
        expected: tuple[str, ...],
        actual: tuple[str, ...],
        *,
        scenario: ReferenceWorkflow,
        observation: RuntimeObservation,
    ) -> None:
        if set(expected) != set(actual):
            issues.append(
                cls._issue(
                    code,
                    scenario=scenario,
                    observation=observation,
                    expected=tuple(sorted(expected)),
                    actual=tuple(sorted(actual)),
                )
            )

    @staticmethod
    def _issue(
        code: str,
        *,
        scenario: ReferenceWorkflow,
        observation: RuntimeObservation,
        expected: tuple[str, ...] = (),
        actual: tuple[str, ...] = (),
    ) -> ConformanceIssue:
        return ConformanceIssue(
            code=code,
            expected=expected,
            actual=actual,
            event_sequence=observation.event_types,
            minimal_reproduction={
                "runtime_id": observation.runtime_id,
                "scenario_id": scenario.scenario_id,
                "invariant": code,
            },
        )


class RuntimeDifferentialEvaluator:
    """Compares only deterministic projections shared by two observations."""

    def compare(
        self,
        left: RuntimeObservation,
        right: RuntimeObservation,
        *,
        required_capabilities: frozenset[str] = frozenset(),
    ) -> tuple[ConformanceIssue, ...]:
        shared_capabilities = set(left.capabilities) & set(right.capabilities)
        missing = set(required_capabilities) - shared_capabilities
        if missing:
            return (
                ConformanceIssue(
                    "runtime_pair_incompatible",
                    expected=tuple(sorted(required_capabilities)),
                    actual=tuple(sorted(shared_capabilities)),
                ),
            )
        issues: list[ConformanceIssue] = []
        for code, left_values, right_values in (
            ("terminal_status_drift", (left.terminal_status,), (right.terminal_status,)),
            ("event_type_drift", tuple(sorted(set(left.event_types))), tuple(sorted(set(right.event_types)))),
            ("artifact_drift", tuple(sorted(set(left.artifact_ids))), tuple(sorted(set(right.artifact_ids)))),
            ("gate_drift", tuple(sorted(set(left.gate_ids))), tuple(sorted(set(right.gate_ids)))),
            (
                "side_effect_drift",
                tuple(sorted(set(left.side_effect_operations))),
                tuple(sorted(set(right.side_effect_operations))),
            ),
            (
                "policy_decision_drift",
                tuple(sorted(set(left.policy_decisions))),
                tuple(sorted(set(right.policy_decisions))),
            ),
            (
                "budget_usage_drift",
                tuple(f"{key}={left.budget_usage[key]}" for key in sorted(left.budget_usage)),
                tuple(f"{key}={right.budget_usage[key]}" for key in sorted(right.budget_usage)),
            ),
        ):
            if left_values != right_values:
                issues.append(
                    ConformanceIssue(
                        code,
                        expected=left_values,
                        actual=right_values,
                        event_sequence=right.event_types,
                        minimal_reproduction={
                            "left_runtime_id": left.runtime_id,
                            "right_runtime_id": right.runtime_id,
                            "invariant": code,
                        },
                    )
                )
        return tuple(issues)


class ReferenceProviderPort(Protocol):
    def invoke(self, *, scenario_id: str) -> dict[str, Any]: ...


class ReferenceToolPort(Protocol):
    def invoke(self, *, scenario_id: str, operation: str) -> dict[str, Any]: ...


class DeterministicFakeProvider:
    """Network-free fake used by the checked-in conformance command."""

    def invoke(self, *, scenario_id: str) -> dict[str, Any]:
        return {"schema": "ananta.reference_provider_output.v1", "scenario_id": scenario_id}


class DeterministicFakeTools:
    """Side-effect-free fake; the operation is reported, never executed."""

    def invoke(self, *, scenario_id: str, operation: str) -> dict[str, Any]:
        return {
            "schema": "ananta.reference_tool_output.v1",
            "scenario_id": scenario_id,
            "operation": operation,
            "status": "simulated",
        }


class DeterministicReferenceRuntime:
    """A test oracle; it is intentionally unavailable as a production runtime."""

    runtime_id = "reference"

    def __init__(
        self,
        *,
        provider: ReferenceProviderPort | None = None,
        tools: ReferenceToolPort | None = None,
    ) -> None:
        self._provider = provider or DeterministicFakeProvider()
        self._tools = tools or DeterministicFakeTools()

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset(
            {
                "approval",
                "bounded_parallel",
                "checkpoint",
                "deterministic_merge",
                "durability",
                "resume",
                "retrieval",
                "structured_output",
                "tool_calling",
            }
        )

    def observe(self, scenario: ReferenceWorkflow) -> RuntimeObservation:
        provider_output = self._provider.invoke(scenario_id=scenario.scenario_id)
        if provider_output.get("scenario_id") != scenario.scenario_id:
            raise ValueError("reference_provider_binding_mismatch")
        for operation in scenario.invariants.side_effect_operations:
            tool_output = self._tools.invoke(
                scenario_id=scenario.scenario_id,
                operation=operation,
            )
            if tool_output.get("operation") != operation:
                raise ValueError("reference_tool_binding_mismatch")
        return RuntimeObservation(
            runtime_id=self.runtime_id,
            terminal_status=scenario.invariants.terminal_statuses[0],
            capabilities=self.capabilities,
            event_types=scenario.invariants.required_event_types,
            artifact_ids=scenario.invariants.required_artifacts,
            gate_ids=scenario.invariants.required_gates,
            side_effect_operations=scenario.invariants.side_effect_operations,
            policy_decisions=scenario.invariants.required_policy_decisions,
            budget_usage={"attempts": 1, "tokens": 0, "cost_micros": 0},
        )


@dataclass(frozen=True)
class ConformanceHarnessRecord:
    runtime_id: str
    scenario_id: str
    repetitions: int
    status: str
    observation_digest: str
    result: ConformanceResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "scenario_id": self.scenario_id,
            "repetitions": self.repetitions,
            "status": self.status,
            "observation_digest": self.observation_digest,
            "result": self.result.to_dict(),
        }


class WorkflowConformanceHarness:
    """Repeatable differential runner for framework-neutral runtime adapters."""

    def __init__(self, *, repetitions: int = 10) -> None:
        if repetitions < 10 or repetitions > 100:
            raise ValueError("conformance_repetitions_out_of_bounds")
        self._repetitions = repetitions
        self._evaluator = WorkflowConformanceEvaluator()

    def run(
        self,
        scenarios: tuple[ReferenceWorkflow, ...],
        runtimes: tuple[ConformanceRuntimePort, ...],
    ) -> tuple[ConformanceHarnessRecord, ...]:
        records: list[ConformanceHarnessRecord] = []
        for runtime in runtimes:
            for scenario in scenarios:
                observations = tuple(runtime.observe(scenario) for _ in range(self._repetitions))
                projections = tuple(_observation_projection(item) for item in observations)
                digests = tuple(_projection_digest(item) for item in projections)
                result = self._evaluator.evaluate(scenario, observations[0])
                if len(set(digests)) != 1:
                    issue = ConformanceIssue(
                        code="nondeterministic_runtime_projection",
                        actual=digests,
                        event_sequence=observations[0].event_types,
                        minimal_reproduction={
                            "runtime_id": runtime.runtime_id,
                            "scenario_id": scenario.scenario_id,
                            "repetitions": self._repetitions,
                        },
                    )
                    result = ConformanceResult(
                        runtime_id=runtime.runtime_id,
                        scenario_id=scenario.scenario_id,
                        status="failed",
                        issues=(issue,),
                        observed_terminal_status=observations[0].terminal_status,
                    )
                records.append(
                    ConformanceHarnessRecord(
                        runtime_id=runtime.runtime_id,
                        scenario_id=scenario.scenario_id,
                        repetitions=self._repetitions,
                        status=result.status,
                        observation_digest=digests[0],
                        result=result,
                    )
                )
        return tuple(records)


def _observation_projection(observation: RuntimeObservation) -> dict[str, Any]:
    """Exclude generated text while retaining every deterministic invariant."""

    return {
        "runtime_id": observation.runtime_id,
        "terminal_status": observation.terminal_status,
        "capabilities": sorted(observation.capabilities),
        "event_types": list(observation.event_types),
        "artifact_ids": sorted(set(observation.artifact_ids)),
        "gate_ids": sorted(set(observation.gate_ids)),
        "side_effect_operations": sorted(set(observation.side_effect_operations)),
        "policy_decisions": sorted(set(observation.policy_decisions)),
        "budget_usage": {
            key: observation.budget_usage[key] for key in sorted(observation.budget_usage)
        },
        "unsupported_reason": observation.unsupported_reason,
    }


def _projection_digest(projection: dict[str, Any]) -> str:
    rendered = json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()
