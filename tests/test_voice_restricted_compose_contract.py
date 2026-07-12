from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "docker" / "compose-next" / "compose.voice-restricted.yml"


def _compose() -> dict:
    payload = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_runtime_model_roots_match_the_fail_closed_application_contract() -> None:
    environment = _compose()["x-restricted-inference-common"]["environment"]
    assert environment["ANANTA_RESTRICTED_INFERENCE_MANIFEST_ROOT"] == "/models/restricted/manifests"
    assert environment["ANANTA_RESTRICTED_INFERENCE_SNAPSHOT_ROOT"] == "/models/restricted/artifacts"
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["TRANSFORMERS_OFFLINE"] == "1"
    assert environment["RESTRICTED_INFERENCE_ALLOW_MODEL_DOWNLOAD"] == "false"


def test_hub_has_neither_model_mounts_nor_gpu_devices() -> None:
    hub = _compose()["services"]["ai-agent-hub"]
    assert not any(
        str(mount.get("target", "")).startswith("/models/")
        for mount in hub.get("volumes", ())
        if isinstance(mount, dict)
    )
    reservations = hub.get("deploy", {}).get("resources", {}).get("reservations", {})
    assert not reservations.get("devices")


def test_personalization_fernet_key_is_required_and_hub_only() -> None:
    compose = _compose()
    key_name = "VOICE_PERSONALIZATION_ENCRYPTION_KEY"
    services = compose["services"]
    hub_value = services["ai-agent-hub"]["environment"][key_name]
    assert hub_value.startswith("${VOICE_PERSONALIZATION_ENCRYPTION_KEY:?")
    assert "Fernet key" in hub_value
    for service_name, service in services.items():
        if service_name != "ai-agent-hub":
            assert key_name not in (service.get("environment") or {}), service_name
    assert key_name not in compose["x-voice-runtime-common"]["environment"]
    assert key_name not in compose["x-restricted-inference-common"]["environment"]

    base = yaml.safe_load(
        (ROOT / "docker" / "compose-next" / "compose.base.yml").read_text(encoding="utf-8")
    )
    for service_name in ("ai-agent-worker-base", "angular-frontend-base"):
        assert key_name not in (base["services"][service_name].get("environment") or {})
    angular_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "frontend-angular" / "src").rglob("*.ts")
        if not path.name.endswith(".spec.ts")
    )
    assert key_name not in angular_sources


def test_capability_network_aliases_are_disjoint_and_not_host_published() -> None:
    compose = _compose()
    services = compose["services"]
    voice_aliases: set[str] = set()
    restricted_aliases: set[str] = set()
    for name, service in services.items():
        if name.startswith("voice-runtime-"):
            assert "ports" not in service
            voice_aliases.update(service["networks"]["voice-runtime-control"].get("aliases", ()))
        if name.startswith("restricted-inference-"):
            assert "ports" not in service
            restricted_aliases.update(service["networks"]["restricted-inference-control"].get("aliases", ()))
    assert voice_aliases == {"voice-runtime"}
    assert restricted_aliases == {"restricted-inference-worker"}
    assert voice_aliases.isdisjoint(restricted_aliases)


def test_only_runtime_containers_receive_model_execution_profile_and_devices() -> None:
    services = _compose()["services"]
    nvidia = {name for name in services if name.endswith("-nvidia")}
    assert nvidia == {"voice-runtime-nvidia", "restricted-inference-nvidia"}
    for name, service in services.items():
        device_reservations = (
            service.get("deploy", {}).get("resources", {}).get("reservations", {}).get("devices", ())
        )
        if name in nvidia:
            assert device_reservations
        else:
            assert not device_reservations


def test_runtime_images_exclude_hub_and_peer_execution_packages() -> None:
    voice_dockerfile = (ROOT / "docker" / "compose-next" / "Dockerfile.voice-runtime").read_text(
        encoding="utf-8"
    )
    restricted_dockerfile = (
        ROOT / "docker" / "compose-next" / "Dockerfile.restricted-inference"
    ).read_text(encoding="utf-8")
    assert "COPY --chown=10001:10001 agent" not in voice_dockerfile
    assert "COPY --chown=10001:10001 worker" not in voice_dockerfile
    assert "COPY --chown=10002:10002 voice_runtime" not in restricted_dockerfile
    assert "agent.ai_agent" not in restricted_dockerfile


def test_restricted_cpu_image_pins_cpu_only_pytorch() -> None:
    requirements = (
        ROOT / "docker" / "compose-next" / "requirements.restricted-inference-cpu.txt"
    ).read_text(encoding="utf-8")

    assert "--extra-index-url https://download.pytorch.org/whl/cpu" in requirements
    assert "torch==2.6.0+cpu" in requirements
    assert "nvidia-" not in requirements


def test_nvidia_images_have_separate_pinned_cuda_user_space_dependencies() -> None:
    voice_dockerfile = (ROOT / "docker" / "compose-next" / "Dockerfile.voice-runtime").read_text(
        encoding="utf-8"
    )
    restricted_dockerfile = (
        ROOT / "docker" / "compose-next" / "Dockerfile.restricted-inference"
    ).read_text(encoding="utf-8")
    voice_requirements = (
        ROOT / "docker" / "compose-next" / "requirements.voice-nvidia.txt"
    ).read_text(encoding="utf-8")
    restricted_requirements = (
        ROOT / "docker" / "compose-next" / "requirements.restricted-inference-nvidia.txt"
    ).read_text(encoding="utf-8")

    assert "requirements.voice-nvidia.txt" in voice_dockerfile
    assert "nvidia-cublas-cu12==12.4.5.8" in voice_requirements
    assert "nvidia-cudnn-cu12==9.1.0.70" in voice_requirements
    assert "NVIDIA_REQUIRE_CUDA=\"cuda>=12.4\"" in voice_dockerfile
    assert "FROM base AS nvidia" in restricted_dockerfile
    assert "FROM cpu AS nvidia" not in restricted_dockerfile
    assert "requirements.restricted-inference-nvidia.txt" in restricted_dockerfile
    assert "torch==2.6.0+cu124" in restricted_requirements
    assert "/whl/cu124" in restricted_requirements
    assert "+cpu" not in restricted_requirements
