from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_fullstack_overlay_declares_single_image_specialized_services() -> None:
    data = _load_yaml("docker-compose.single-image-fullstack.yml")
    services = data.get("services", {})

    assert "evolver" in services
    assert "ai-agent-deerflow" in services
    assert "ai-agent-ml-intern" in services

    for name in ("evolver", "ai-agent-deerflow", "ai-agent-ml-intern"):
        svc = services[name]
        assert svc["image"] == "${ANANTA_QUICKSTART_IMAGE:-ananta-quickstart-no-ollama:local}"
        assert svc["build"]["dockerfile"] == "Dockerfile.quickstart-no-ollama"


def test_fullstack_overlay_routes_core_services_to_role_mode() -> None:
    data = _load_yaml("docker-compose.single-image-fullstack.yml")
    services = data.get("services", {})

    hub_env = services["ai-agent-hub"]["environment"]
    assert hub_env["ANANTA_QUICKSTART_MODE"] == "role"
    assert hub_env["ANANTA_QUICKSTART_ROLE"] == "hub"
    assert hub_env["EVOLVER_BASE_URL"] == "${EVOLVER_BASE_URL:-http://evolver:8080}"

    frontend_env = services["angular-frontend"]["environment"]
    assert frontend_env["ANANTA_QUICKSTART_ROLE"] == "frontend"


def test_quickstart_overlay_keeps_agent_only_compat_mode() -> None:
    data = _load_yaml("docker-compose.quickstart-no-ollama.yml")
    services = data.get("services", {})

    assert services["ai-agent-hub"]["environment"]["ANANTA_QUICKSTART_MODE"] == "${ANANTA_QUICKSTART_MODE_HUB:-agent-only}"
    assert services["ai-agent-alpha"]["environment"]["ANANTA_QUICKSTART_MODE"] == "${ANANTA_QUICKSTART_MODE_ALPHA:-agent-only}"
    assert services["ai-agent-beta"]["environment"]["ANANTA_QUICKSTART_MODE"] == "${ANANTA_QUICKSTART_MODE_BETA:-agent-only}"
