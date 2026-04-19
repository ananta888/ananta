from pathlib import Path


def test_core_read_model_baselines_exist_for_frontend_contracts():
    baseline_dir = Path("tests/baselines")
    required = [
        baseline_dir / "assistant_read_model.json",
        baseline_dir / "dashboard_read_model.json",
        baseline_dir / "schemas" / "governance_policy.json",
    ]

    missing = [str(path) for path in required if not path.exists()]
    assert not missing, f"Missing read-model baselines: {missing}"
