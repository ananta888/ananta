from __future__ import annotations

import pytest

from ananta_contracts.dspy_optimization import OptimizationBudgets
from tests.dspy_optimization.helpers import program
from worker.optimization.dspy.artifact_serializer import DspyJsonProgramSerializer
from worker.optimization.dspy.module_registry import DspyModuleRegistry
from worker.optimization.dspy.optimizer_registry import DspyOptimizerRegistry


def test_json_only_serializer_roundtrips_native_program_and_rejects_pickle() -> None:
    serializer = DspyJsonProgramSerializer()
    payload = serializer.dumps(program())
    assert serializer.loads(payload).digest == program().digest
    with pytest.raises(ValueError, match="payload_denied"):
        serializer.loads(b"\x80\x04unsafe")


def test_state_export_rejects_tools_paths_and_unknown_modules() -> None:
    serializer = DspyJsonProgramSerializer()
    with pytest.raises(ValueError, match="unsafe_field"):
        serializer.export(
            tenant_id="tenant-1",
            program_id="program-1",
            program_kind="planning_structured_tasks",
            dspy_state={"tools": ["shell"]},
            model_roles={"student": "binding"},
            dspy_version="3.2.1",
        )
    with pytest.raises(ValueError, match="graph_invalid"):
        DspyModuleRegistry().validate(
            program_kind="planning_structured_tasks",
            module_graph=[{"id": "one", "module": "react"}],
            signatures=[{"input_fields": ["goal", "constraints"], "output_fields": ["tasks"], "instructions": "x"}],
        )


def test_optimizer_registry_is_closed_and_estimates_calls_conservatively() -> None:
    registry = DspyOptimizerRegistry()
    assert (
        registry.estimate_calls(
            "bootstrap_few_shot", {"max_labeled_demos": 2, "max_bootstrapped_demos": 3, "max_rounds": 2}, 10
        )
        == 6
    )
    with pytest.raises(ValueError, match="unknown_field"):
        registry.validate("labeled_few_shot", {"provider": "openai"})
    with pytest.raises(PermissionError, match="trial_budget_exceeded"):
        registry.admit(
            "bootstrap_few_shot",
            {"max_labeled_demos": 2, "max_bootstrapped_demos": 3, "max_rounds": 2},
            record_count=10,
            budgets=OptimizationBudgets(10, 100, 0, 30, 1, 10, 10_000, max_trials=1),
        )
