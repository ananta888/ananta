from agent.services.performance_context_orchestrator import PerformanceContextOrchestrator


def test_performance_context_orchestrator_bounds_files(tmp_path):
    (tmp_path / "x.py").write_text("print('x')\n" * 100, encoding="utf-8")
    package = PerformanceContextOrchestrator().build_context_package(
        hypothesis={"hypothesis_id": "h", "affected_files": ["x.py"]},
        workspace_dir=tmp_path,
        max_total_bytes=20,
    )
    assert package["files"]
    assert package["byte_count"] <= 20
