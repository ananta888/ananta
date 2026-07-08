"""Hub-side orchestration for performance experiments."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from agent.performance.artifacts import build_experiment_plan_artifact
from agent.performance.profilers.python_time import PythonTimeProfilerParser
from agent.services.benchmark_runner_service import BenchmarkRunnerService, get_benchmark_runner_service
from agent.services.optimization_hypothesis_service import (
    OptimizationHypothesisService,
    get_optimization_hypothesis_service,
)
from agent.services.patch_sandbox_service import PatchSandboxService, get_patch_sandbox_service
from agent.services.performance_comparator_service import (
    PerformanceComparatorService,
    get_performance_comparator_service,
)
from agent.services.performance_context_orchestrator import (
    PerformanceContextOrchestrator,
    get_performance_context_orchestrator,
)
from agent.services.performance_hotspot_service import PerformanceHotspotService, get_performance_hotspot_service
from agent.services.regression_gate_service import RegressionGateService, get_regression_gate_service


class PerformanceExperimentOrchestrator:
    def __init__(
        self,
        *,
        runner: BenchmarkRunnerService | None = None,
        hotspot_service: PerformanceHotspotService | None = None,
        hypothesis_service: OptimizationHypothesisService | None = None,
        context_orchestrator: PerformanceContextOrchestrator | None = None,
        sandbox_service: PatchSandboxService | None = None,
        regression_gate: RegressionGateService | None = None,
        comparator: PerformanceComparatorService | None = None,
    ) -> None:
        self._runner = runner or get_benchmark_runner_service()
        self._hotspots = hotspot_service or get_performance_hotspot_service()
        self._hypotheses = hypothesis_service or get_optimization_hypothesis_service()
        self._context = context_orchestrator or get_performance_context_orchestrator()
        self._sandbox = sandbox_service or get_patch_sandbox_service()
        self._regression = regression_gate or get_regression_gate_service()
        self._comparator = comparator or get_performance_comparator_service()

    def run_experiment(self, spec: dict[str, Any]) -> dict[str, Any]:
        workspace = Path(spec.get("workspace_dir") or ".").resolve()
        command = str(spec.get("benchmark_command") or "")
        patch_text = str(spec.get("patch") or "")
        plan_only = bool(spec.get("plan_only", False))
        trace: list[dict[str, Any]] = []
        plan = build_experiment_plan_artifact(
            plan_id=f"plan-{uuid.uuid4().hex[:12]}",
            steps=[
                {"mode": "profile"},
                {"mode": "apply_sandbox_patch"},
                {"mode": "run_benchmark"},
                {"mode": "run_regression"},
                {"mode": "compare"},
                {"mode": "report"},
            ],
            required_tools=["performance.run_benchmark", "performance.compare", "performance.report"],
            benchmark_matrix=[{"command": command}],
            regression_matrix=[{"commands": list(spec.get("regression_commands") or [])}],
        )
        trace.append({"step": "plan", "artifact": plan})
        if plan_only:
            return {
                "schema": "performance_experiment_report.v1",
                "status": "plan_only",
                "trace_bundle": trace,
                "plan": plan,
            }

        baseline = self._runner.run_benchmark(
            command=command,
            workspace_dir=workspace,
            task_id=str(spec.get("task_id") or "performance-experiment"),
            profile_id=str(spec.get("profile_id") or "micro_benchmark"),
        )
        trace.append({"step": "baseline", "artifact": baseline})
        observation = PythonTimeProfilerParser().parse(
            str(baseline.get("stdout_ref") or "wall_time: 0.0 seconds")
        ).to_dict()
        hotspot_report = self._hotspots.resolve_hotspots(profile_observation=observation, workspace_dir=workspace)
        trace.append({"step": "hotspots", "artifact": hotspot_report})
        hypotheses = self._hypotheses.generate(hotspot_report=hotspot_report)
        trace.append({"step": "hypotheses", "artifact": hypotheses})
        context = self._context.build_context_package(hypothesis=hypotheses[0], workspace_dir=workspace)
        trace.append({"step": "context", "artifact": context})

        if not patch_text:
            return {
                "schema": "performance_experiment_report.v1",
                "status": "inconclusive",
                "reason_code": "no_patch_candidate",
                "trace_bundle": trace,
                "baseline": baseline,
                "hypotheses": hypotheses,
            }
        sandbox = self._sandbox.create_sandbox(workspace_dir=workspace, patch_text=patch_text)
        trace.append({"step": "sandbox", "artifact": sandbox})
        if sandbox.get("status") != "completed":
            return {
                "schema": "performance_experiment_report.v1",
                "status": "failed",
                "reason_code": sandbox.get("reason_code"),
                "trace_bundle": trace,
            }

        candidate = self._runner.run_benchmark(
            command=command,
            workspace_dir=sandbox["sandbox_dir"],
            task_id=str(spec.get("task_id") or "performance-experiment"),
            profile_id=str(spec.get("profile_id") or "micro_benchmark"),
        )
        trace.append({"step": "candidate", "artifact": candidate})
        regression = self._regression.evaluate(
            workspace_dir=sandbox["sandbox_dir"],
            test_commands=list(spec.get("regression_commands") or []),
            expected_output=spec.get("expected_output"),
            actual_output=spec.get("actual_output"),
        )
        trace.append({"step": "regression", "artifact": regression})
        comparison = self._comparator.compare(
            baseline_run=baseline,
            candidate_run=candidate,
            metric=str(spec.get("metric") or "wall_time"),
            regression_result=regression,
        )
        trace.append({"step": "compare", "artifact": comparison})
        return {
            "schema": "performance_experiment_report.v1",
            "status": comparison.get("pass_fail"),
            "reason_code": comparison.get("reason_code"),
            "baseline": baseline,
            "candidate": candidate,
            "regression": regression,
            "comparison": comparison,
            "trace_bundle": trace,
            "human_review_required": True,
        }


def get_performance_experiment_orchestrator() -> PerformanceExperimentOrchestrator:
    return PerformanceExperimentOrchestrator()
