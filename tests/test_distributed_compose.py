from pathlib import Path

import yaml


def test_distributed_compose_has_extra_workers():
    path = Path("docker-compose.distributed.yml")
    assert path.exists(), "docker-compose.distributed.yml fehlt"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    services = data.get("services", {})
    assert "ai-agent-gamma" in services
    assert "ai-agent-delta" in services


def test_main_compose_defines_optional_evolver_service():
    data = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    services = data.get("services", {})
    hub_env = services["ai-agent-hub"]["environment"]
    assert services["evolver"]["profiles"] == ["evolution"]
    assert hub_env["EVOLVER_BASE_URL"] == "${EVOLVER_BASE_URL:-http://evolver:8080}"
    assert hub_env["EVOLVER_ENABLED"] == "${EVOLVER_ENABLED:-0}"
