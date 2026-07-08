from agent.services.performance_baseline_service import PerformanceBaselineService


def test_performance_baseline_save_and_find(tmp_path):
    svc = PerformanceBaselineService(root=tmp_path)
    run = {
        "run_id": "run-1",
        "profile_id": "micro",
        "hardware_fingerprint": {"cpu": "x"},
        "software_fingerprint": {"py": "3"},
    }
    saved = svc.save_baseline(benchmark_run=run, repo_ref="HEAD", profile_id="micro")
    found = svc.find_baseline(
        repo_ref="HEAD",
        profile_id="micro",
        hardware_fingerprint={"cpu": "x"},
        software_fingerprint={"py": "3"},
    )
    assert found is not None
    assert found["baseline_id"] == saved["baseline_id"]
