from agent.services.benchmark_runner_service import BenchmarkRunnerService


def test_benchmark_runner_uses_native_runtime_for_dummy_command(tmp_path):
    (tmp_path / "bench.py").write_text("print('ok')\n", encoding="utf-8")
    result = BenchmarkRunnerService().run_benchmark(
        command="python bench.py",
        workspace_dir=tmp_path,
        task_id="bench-test",
        profile_id="micro_benchmark",
    )
    assert result["schema"] == "benchmark_run_artifact.v1"
    assert result["profile_id"] == "micro_benchmark"
    assert result["env_sanitized"].get("PATH") == "<set>"
    assert "API" not in "".join(result["env_sanitized"].keys())


def test_benchmark_runner_policy_blocks_unknown_command(tmp_path):
    result = BenchmarkRunnerService().run_benchmark(
        command="curl http://example.invalid",
        workspace_dir=tmp_path,
        task_id="bench-test",
    )
    assert result["status"] in {"failed", "degraded"}
    assert result["reason_code"] != "success"
