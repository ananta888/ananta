from agent.services.performance_hotspot_service import PerformanceHotspotService


def test_performance_hotspot_service_maps_profile_observation(tmp_path):
    (tmp_path / "slow_func.py").write_text("def slow_func(): pass\n", encoding="utf-8")
    report = PerformanceHotspotService().resolve_hotspots(
        profile_observation={"hotspots": [{"symbol": "slow_func", "score": 2.0}]},
        workspace_dir=tmp_path,
    )
    assert report["status"] == "completed"
    assert report["hotspots"][0]["affected_files"] == ["slow_func.py"]
