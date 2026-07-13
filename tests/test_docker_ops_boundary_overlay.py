from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "docker" / "compose-next" / "compose.ops-control.yml"


def test_docker_ops_overlay_grants_boundary_to_hub_only():
    document = yaml.safe_load(OVERLAY.read_text(encoding="utf-8"))
    services = dict(document.get("services") or {})

    assert set(services) == {"ai-agent-hub"}
    hub = services["ai-agent-hub"]
    environment = dict(hub.get("environment") or {})
    volumes = list(hub.get("volumes") or [])

    assert environment["ANANTA_DOCKER_OPS_BOUNDARY"] == "${ANANTA_DOCKER_OPS_BOUNDARY:-hub_cli}"
    assert environment["ANANTA_DOCKER_OPS_ENV_FILE"] == "/run/ananta/compose.env"
    assert environment["DOCKER_HOST"] == "unix:///var/run/docker.sock"
    assert "/var/run/docker.sock:/var/run/docker.sock" in volumes
    assert any(str(item).endswith(":/run/ananta/compose.env:ro") for item in volumes)


def test_standard_compose_definitions_do_not_mount_docker_socket():
    compose_dir = ROOT / "docker" / "compose-next"
    standard_files = [path for path in compose_dir.glob("compose*.yml") if path.name != OVERLAY.name]

    assert standard_files
    assert all("/var/run/docker.sock" not in path.read_text(encoding="utf-8") for path in standard_files)
