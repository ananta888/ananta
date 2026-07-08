from agent.services.performance_experiment_orchestrator import PerformanceExperimentOrchestrator


def test_performance_experiment_orchestrator_plan_only(tmp_path):
    result = PerformanceExperimentOrchestrator().run_experiment({
        "workspace_dir": str(tmp_path),
        "benchmark_command": "python bench.py",
        "plan_only": True,
    })
    assert result["status"] == "plan_only"
    assert result["plan"]["schema"] == "experiment_plan_artifact.v1"
