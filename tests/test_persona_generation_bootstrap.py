"""Explicit generation enablement and immutable execution configuration only."""

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from flask import Flask

from agent.bootstrap.persona_generation import configure_persona_generation
from tests import test_persona_generation_tasks as task_cases
from tests.test_persona_video_http import KEY

runtime = task_cases.runtime
generation_task = task_cases.generation_task
pytestmark = pytest.mark.timeout(40)


def environment(tmp_path):
    key = tmp_path / "generation-key"
    key.write_bytes(KEY)
    key.chmod(0o600)
    return {
        "ANANTA_PERSONA_GENERATION_ENABLED": "1",
        "ANANTA_PERSONA_GENERATION_KEY_FILE": str(key),
        "ANANTA_PERSONA_GENERATION_WORKER_URL": "http://generator.test:8098/v1/persona-generations",
        "ANANTA_PERSONA_GENERATION_REPOSITORY_REVISION": "1" * 40,
        "ANANTA_PERSONA_GENERATION_EXECUTION_PROFILE_DIGEST": "a" * 64,
        "ANANTA_PERSONA_GENERATION_ENVIRONMENT_DIGEST": "b" * 64,
    }


@pytest.mark.parametrize("role,enabled", [("worker", "1"), ("hub", "0"), ("hub", "true"), ("hub", "")])
def test_generator_bootstrap_is_exactly_opt_in(role, enabled, monkeypatch):
    app = Flask(__name__)
    app.config["ROLE"] = role
    monkeypatch.setenv("ANANTA_PERSONA_GENERATION_ENABLED", enabled)
    configure_persona_generation(app)
    assert not app.extensions


def test_bootstrap_wires_separate_generation_and_existing_asset_ports(generation_task, tmp_path, monkeypatch):
    c = generation_task
    app = Flask(__name__)
    app.config["ROLE"] = "hub"
    access = app.extensions["project_access_authority"] = Mock()
    image = app.extensions["persona_assets"] = Mock()
    policy = app.extensions["persona_image_policy"] = Mock()
    for name, value in environment(tmp_path).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        "agent.services.hub_evidence_registry_service.get_hub_evidence_registry_service", lambda: c.base.registry
    )
    configure_persona_generation(app)
    creator = app.extensions["persona_generated_assets"]
    assert creator.targets["image"][:2] == (image, policy)
    assert creator.targets["video"][:2] == (None, None)
    assert creator.generator.leases is app.extensions["persona_generation_leases"]
    assert creator.generator.policy.authority.access is access
    assert creator.generator.registry is c.base.registry
    assert app.extensions["persona_generation_worker_key"] == KEY
    assert "persona_generated_sources" not in app.extensions
    assert not task_cases.runs(c) and not policy.mock_calls and not image.mock_calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("ANANTA_PERSONA_GENERATION_WORKER_URL", "http://generator.test:8098/v1/persona-images"),
        ("ANANTA_PERSONA_GENERATION_REPOSITORY_REVISION", "main"),
        ("ANANTA_PERSONA_GENERATION_EXECUTION_PROFILE_DIGEST", "unknown"),
        ("ANANTA_PERSONA_GENERATION_ENVIRONMENT_DIGEST", ""),
    ],
)
def test_invalid_configuration_never_installs_generator(generation_task, tmp_path, monkeypatch, field, value):
    c = generation_task
    app = Flask(__name__)
    app.config["ROLE"] = "hub"
    app.extensions["project_access_authority"] = c.access
    for key, setting in (environment(tmp_path) | {field: value}).items():
        monkeypatch.setenv(key, setting)
    monkeypatch.setattr(
        "agent.services.hub_evidence_registry_service.get_hub_evidence_registry_service", lambda: c.base.registry
    )
    with pytest.raises(ValueError):
        configure_persona_generation(app)
    assert not any(key.startswith("persona_generation") or key == "persona_generated_assets" for key in app.extensions)
    assert not task_cases.runs(c)


def test_shipped_cpu_generator_is_separate_small_readonly_and_not_a_publisher():
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "docker-compose.persona-generation.yml").read_text())
    worker = config["services"]["persona-generation-worker"]
    assert set(config["services"]) == {"persona-generation-worker"}
    assert worker["read_only"] is True and worker["init"] is True and worker["cap_drop"] == ["ALL"]
    assert worker["mem_limit"] == "256m" and worker["cpus"] == 1 and worker["pids_limit"] == 32
    assert not {"ports", "devices", "gpus", "privileged"} & set(worker)
    assert len(worker["volumes"]) == 2 and worker["volumes"][0].endswith(":ro")
    assert worker["networks"] == ["persona-generation"] and config["networks"]["persona-generation"]["internal"]
    assert set(worker["environment"]) == {"PERSONA_GENERATION_WORKER_KEY_FILE", "PERSONA_GENERATION_HUB_LEASE_URL"}
    overlay = yaml.safe_load((root / "docker-compose.persona-generation-hub.yml").read_text())
    hub = overlay["services"]["ai-agent-hub"]
    assert hub["environment"]["ANANTA_PERSONA_GENERATION_ENABLED"] == "${ANANTA_PERSONA_GENERATION_ENABLED:-0}"
    assert not {"ports", "devices", "gpus", "privileged"} & set(hub)
    assert not any("RETENTION" in name or "IMAGES_ENABLED" in name for name in hub["environment"])
    dockerfile = (root / "docker/persona-generation/Dockerfile").read_text()
    assert "@sha256:" in dockerfile and "Pillow==11.3.0" in dockerfile
    assert "COPY agent" not in dockerfile and "persona_generation_server" in dockerfile
