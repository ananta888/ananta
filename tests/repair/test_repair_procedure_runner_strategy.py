from __future__ import annotations

from types import SimpleNamespace

from worker.core.repair_procedure_runner_strategy import RepairProcedureRunnerStrategy


def test_repair_strategy_returns_executable_when_missing_paths_present() -> None:
    strategy = RepairProcedureRunnerStrategy()
    context = SimpleNamespace(
        goal_id="g1",
        task_id="t1",
        task={"verification_critique": {"missing_paths": ["backend", "frontend"]}},
    )
    result = strategy.run(context)
    assert result.is_executable is True
    assert result.proposal.tool_calls[0]["name"] == "file_write"


def test_repair_strategy_declines_without_critique() -> None:
    strategy = RepairProcedureRunnerStrategy()
    context = SimpleNamespace(goal_id="g1", task_id="t1", task={})
    result = strategy.run(context)
    assert result.status == "declined"

