from pathlib import Path

import yaml


def test_distributed_compose_has_extra_workers():
    path = Path("docker-compose.distributed.yml")
    assert path.exists(), "docker-compose.distributed.yml fehlt"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    services = data.get("services", {})
    assert "ai-agent-gamma" in services
    assert "ai-agent-delta" in services
