from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

from agent.config import Settings
from agent.services.model_profile_loader import ModelProfileLoader

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "docker/compose-next/compose.dev.ollama.yml"
CPU_OVERLAY_PATH = (
    ROOT / "docker/compose-next/compose.dev.ollama-cpu.yml"
)
PROFILE_PATH = (
    ROOT
    / "config/models/local-ollama-phi-gemma-rtx3080.model_profiles.yaml"
)


def _compose() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8")),
    )


class _ComposeLoader(yaml.SafeLoader):
    """Parse Compose reset tags as their effective replacement value."""


def _construct_reset_sequence(
    loader: yaml.SafeLoader,
    node: yaml.SequenceNode,
) -> list[object]:
    return cast(list[object], loader.construct_sequence(node))


_ComposeLoader.add_constructor("!reset", _construct_reset_sequence)


def _cpu_overlay() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        yaml.load(
            CPU_OVERLAY_PATH.read_text(encoding="utf-8"),
            Loader=_ComposeLoader,
        ),
    )


def test_ollama_uses_backupable_wsl_bind_mount_and_single_model_gpu_limits():
    compose = _compose()
    ollama = compose["services"]["ollama"]
    bootstrap = compose["services"]["model-bootstrap"]

    expected_model_mount = {
        "type": "bind",
        "source": "${OLLAMA_DATA_DIR:-../../../ananta-data/ollama}",
        "target": "/root/.ollama",
    }
    assert expected_model_mount in ollama["volumes"]
    assert not any(
        (
            isinstance(value, dict)
            and value.get("target") == "/root/.ollama"
        )
        or (
            isinstance(value, str)
            and value.endswith(":/root/.ollama")
        )
        for value in bootstrap["volumes"]
    )
    assert "ollama-data" not in compose.get("volumes", {})

    assert ollama["gpus"] == "all"
    assert ollama["environment"]["NVIDIA_VISIBLE_DEVICES"] == "all"
    assert (
        ollama["environment"]["OLLAMA_NUM_PARALLEL"]
        == "${OLLAMA_NUM_PARALLEL:-1}"
    )
    assert (
        ollama["environment"]["OLLAMA_MAX_LOADED_MODELS"]
        == "${OLLAMA_MAX_LOADED_MODELS:-1}"
    )


def test_cpu_overlay_removes_only_the_nvidia_device_request():
    overlay = _cpu_overlay()

    assert set(overlay) == {"services"}
    assert set(overlay["services"]) == {"ollama"}
    ollama = overlay["services"]["ollama"]
    assert ollama["gpus"] == []
    assert ollama["environment"] == {"NVIDIA_VISIBLE_DEVICES": "void"}


def test_python_and_angular_services_mount_only_live_sources():
    services = _compose()["services"]

    for service_name in (
        "ai-agent-hub",
        "ai-agent-alpha",
        "ai-agent-beta",
    ):
        volumes = services[service_name]["volumes"]
        assert "../../agent:/app/agent:ro" in volumes
        assert "../../worker:/app/worker:ro" in volumes
        assert "../../ananta_codecompass:/app/ananta_codecompass:ro" in volumes
        assert "../../config:/app/config:ro" in volumes
        assert not any(
            isinstance(value, str)
            and value.split(":", 1)[0] == "../.."
            for value in volumes
        )

    angular_volumes = services["angular-frontend"]["volumes"]
    assert "../../:/app:rw" not in angular_volumes

    assert services["ai-agent-hub"]["environment"]["FLASK_DEBUG"] == 1
    assert services["ai-agent-alpha"]["environment"]["FLASK_DEBUG"] == 1
    assert services["ai-agent-beta"]["environment"]["FLASK_DEBUG"] == 1


def test_workers_do_not_share_the_hub_authoritative_database():
    services = _compose()["services"]

    hub_database = services["ai-agent-hub"]["environment"]["DATABASE_URL"]
    alpha_database = services["ai-agent-alpha"]["environment"]["DATABASE_URL"]
    beta_database = services["ai-agent-beta"]["environment"]["DATABASE_URL"]

    assert str(hub_database).startswith("postgresql://")
    assert str(alpha_database) == "sqlite:////app/data/ananta.db"
    assert str(beta_database) == "sqlite:////app/data/ananta.db"
    assert alpha_database != hub_database
    assert beta_database != hub_database


def test_strict_hub_auth_uses_an_explicit_local_cors_allowlist():
    hub_environment = _compose()["services"]["ai-agent-hub"]["environment"]
    configured = hub_environment["CORS_ORIGINS"]

    assert configured == (
        "${CORS_ORIGINS:-"
        "http://localhost:4200,http://127.0.0.1:4200}"
    )
    settings = Settings(
        _env_file=None,
        workflow_require_registered_worker_auth=True,
        cors_origins="http://localhost:4200,http://127.0.0.1:4200",
    )
    assert "*" not in settings.cors_origins


def test_local_profiles_reserve_explicit_safe_input_budgets():
    loaded = ModelProfileLoader().load_file(PROFILE_PATH)

    assert loaded.ok, loaded.errors
    profiles = {profile.profile_id: profile for profile in loaded.profiles}

    phi = profiles["local_ollama_phi4_mini"]
    assert phi.context_tokens == 32_768
    assert phi.max_context_for_profile == 32_768
    assert phi.max_output_tokens == 2_048
    assert phi.max_input_tokens() == 30_720

    gemma = profiles["local_ollama_gemma4_e4b_reasoning"]
    assert gemma.context_tokens == 8_192
    assert gemma.max_context_for_profile == 8_192
    assert gemma.max_output_tokens == 3_072
    assert gemma.max_input_tokens() == 5_111
    assert gemma.system_prompt_prefix == "<|think|>"
    assert gemma.temperature == 1.0
