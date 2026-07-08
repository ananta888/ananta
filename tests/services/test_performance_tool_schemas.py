from agent.services.ananta_tool_registry_service import get_ananta_tool_registry_service
from worker.core.tool_registry import build_default_registry


def test_worker_tool_registry_contains_performance_tools():
    registry = build_default_registry()
    assert registry.get("performance.run_benchmark").risk_class == "high"
    assert registry.get("performance.compare").risk_class == "low"


def test_ananta_tool_registry_contains_performance_tools():
    service = get_ananta_tool_registry_service()
    assert service.is_known_tool("performance.run_benchmark")
    assert service.get_tool("performance.compare").execution_plane == "hub_control_only"
