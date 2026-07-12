from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker" / "compose-next" / "compose.voice-restricted.yml"
VOICE_DOCKERFILE = ROOT / "docker" / "compose-next" / "Dockerfile.voice-runtime"
RESTRICTED_DOCKERFILE = ROOT / "docker" / "compose-next" / "Dockerfile.restricted-inference"
GENERATIVE_JUDGE_DOCKERFILE = ROOT / "docker" / "compose-next" / "Dockerfile.generative-judge-worker"
GENERATIVE_JUDGE_REQUIREMENTS = ROOT / "docker" / "compose-next" / "requirements.generative-judge-cpu.txt"
HARDWARE_PROFILES_FILE = ROOT / "config" / "release-gates" / "voice-restricted-hardware-profiles.v1.json"

VOICE_SERVICES = {
    "voice-runtime-minimal",
    "voice-runtime-cpu",
    "voice-runtime-nvidia",
}
RESTRICTED_SERVICES = {
    "restricted-inference-minimal",
    "restricted-inference-cpu",
    "restricted-inference-nvidia",
}
RUNTIME_SERVICES = VOICE_SERVICES | RESTRICTED_SERVICES
GENERATIVE_JUDGE_SERVICE = "generative-judge-worker"


@pytest.fixture(scope="module")
def compose_document() -> dict:
    document = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def test_no_network_boundary_keeps_capability_networks_internal_and_disjoint(compose_document: dict) -> None:
    networks = compose_document["networks"]
    assert networks["voice-runtime-control"]["internal"] is True
    assert networks["restricted-inference-control"]["internal"] is True
    assert networks["generative-judge-control"]["internal"] is True

    services = compose_document["services"]
    for name in VOICE_SERVICES:
        assert set(services[name]["networks"]) == {"voice-runtime-control"}
    for name in RESTRICTED_SERVICES:
        assert set(services[name]["networks"]) == {"restricted-inference-control"}
    assert set(services[GENERATIVE_JUDGE_SERVICE]["networks"]) == {"generative-judge-control"}

    hub_networks = set(services["ai-agent-hub"]["networks"])
    assert hub_networks == {
        "default",
        "generative-judge-control",
        "voice-runtime-control",
        "restricted-inference-control",
    }


@pytest.mark.parametrize("service_name", sorted(RUNTIME_SERVICES))
def test_runtime_services_apply_least_privilege(compose_document: dict, service_name: str) -> None:
    service = compose_document["services"][service_name]

    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["pids_limit"] == 256
    assert service["deploy"]["resources"]["limits"]["pids"] == 256
    assert service["deploy"]["resources"]["limits"]["cpus"]
    assert service["deploy"]["resources"]["limits"]["memory"]
    assert "ports" not in service
    assert "extra_hosts" not in service
    assert service["tmpfs"]
    assert all("noexec" in mount and "nosuid" in mount and "nodev" in mount for mount in service["tmpfs"])

    model_mounts = [mount for mount in service["volumes"] if str(mount.get("target", "")).startswith("/models/")]
    assert len(model_mounts) == 1
    assert model_mounts[0]["read_only"] is True
    assert model_mounts[0]["bind"]["create_host_path"] is False


@pytest.mark.parametrize("service_name", sorted(RUNTIME_SERVICES))
def test_runtime_services_fail_closed_without_remote_downloads(compose_document: dict, service_name: str) -> None:
    environment = compose_document["services"][service_name]["environment"]
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["TRANSFORMERS_OFFLINE"] == "1"

    if service_name.startswith("voice-"):
        assert environment["VOICE_ALLOW_MODEL_DOWNLOAD"] == "false"
        assert environment["VOICE_PRODUCTION_PROFILE"] == "true"
        assert str(environment["VOICE_RESTRICTED_CHOICE_HOOK_ENABLED"]).endswith(":-true}")
        assert environment["VOICE_REQUIRE_INTERNAL_AUTH"] == "true"
        selected_backends = ",".join(
            str(environment.get(key, ""))
            for key in (
                "VOICE_ASR_BACKEND",
                "VOICE_PRIMARY_BACKEND",
                "VOICE_RERUN_BACKEND",
                "VOICE_SECONDARY_BACKENDS",
            )
        )
        assert "mock" not in selected_backends
    else:
        assert environment["ANANTA_RESTRICTED_INFERENCE_TOKEN"].startswith("${RESTRICTED_INFERENCE_INTERNAL_TOKEN:?")
        assert "at least 24 characters" in environment["ANANTA_RESTRICTED_INFERENCE_TOKEN"]
        assert "HUB_URL" not in environment
        assert environment["RESTRICTED_INFERENCE_ALLOW_MODEL_DOWNLOAD"] == "false"
        assert environment["RESTRICTED_INFERENCE_REQUIRE_INTERNAL_AUTH"] == "true"


def test_generative_judge_worker_is_optional_hardened_and_has_only_a_local_engine(
    compose_document: dict,
) -> None:
    service = compose_document["services"][GENERATIVE_JUDGE_SERVICE]
    environment = service["environment"]

    assert service["profiles"] == ["voice-generative-judge"]
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["pids_limit"] == 256
    assert "ports" not in service
    assert "extra_hosts" not in service
    assert environment["GENERATIVE_JUDGE_ENGINE"] == "${GENERATIVE_JUDGE_ENGINE:-transformers}"
    assert environment["GENERATIVE_JUDGE_MODEL_PATH"] == "/models/generative-judge/model"
    assert environment["GENERATIVE_JUDGE_ALLOWED_HUB_ORIGINS"] == "http://ai-agent-hub:5000"
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["TRANSFORMERS_OFFLINE"] == "1"
    model_mount = service["volumes"][0]
    assert model_mount["target"] == "/models/generative-judge"
    assert model_mount["read_only"] is True
    assert model_mount["bind"]["create_host_path"] is False


def test_hub_is_the_only_runtime_client_and_owns_service_credentials(compose_document: dict) -> None:
    hub = compose_document["services"]["ai-agent-hub"]
    environment = hub["environment"]

    assert environment["VOICE_RUNTIME_URL"] == "http://voice-runtime:8090"
    assert environment["ANANTA_RESTRICTED_INFERENCE_URL"] == (
        "http://restricted-inference-worker:8091/internal/v1/restricted-inference"
    )
    assert environment["ANANTA_RESTRICTED_INFERENCE_ALLOWED_ENDPOINTS"] == (
        environment["ANANTA_RESTRICTED_INFERENCE_URL"]
    )
    assert environment["RESTRICTED_INFERENCE_WORKER_URL"] == "http://restricted-inference-worker:8091"
    assert environment["VOICE_DIRECT_CLIENT_ACCESS"] == "false"
    assert environment["VOICE_DELETION_LEDGER_PATH"] == "/app/data/voice-deletion-ledger.v1.jsonl"
    assert str(environment["VOICE_DELETION_LEDGER_SEGMENT_RECORDS"]).endswith(":-10000}")
    assert str(environment["VOICE_DELETION_LEDGER_MAX_RECORDS"]).endswith(":-1000000}")
    assert environment["VOICE_INTERNAL_SERVICE_TOKEN"].startswith("${VOICE_INTERNAL_SERVICE_TOKEN:?")
    assert environment["RESTRICTED_INFERENCE_INTERNAL_TOKEN"].startswith("${RESTRICTED_INFERENCE_INTERNAL_TOKEN:?")
    assert environment["ANANTA_RESTRICTED_INFERENCE_TOKEN"] == environment["RESTRICTED_INFERENCE_INTERNAL_TOKEN"]
    assert environment["ANANTA_RESTRICTED_INFERENCE_MANIFEST_SCORE_CHOICES"] == (
        "${ANANTA_RESTRICTED_INFERENCE_MANIFEST_SCORE_CHOICES:-}"
    )
    assert environment["VOICE_GENERATIVE_JUDGE_WORKER_URL"] == (
        "http://generative-judge-worker:8092/internal/v1/generative-judge"
    )
    assert environment["VOICE_GENERATIVE_JUDGE_WORKER_ALLOWED_ENDPOINTS"] == (
        environment["VOICE_GENERATIVE_JUDGE_WORKER_URL"]
    )
    assert environment["VOICE_GENERATIVE_JUDGE_HUB_ORIGIN"] == "http://ai-agent-hub:5000"
    assert set(hub["depends_on"]) == RUNTIME_SERVICES | {GENERATIVE_JUDGE_SERVICE}
    assert all(dependency["required"] is False for dependency in hub["depends_on"].values())


@pytest.mark.parametrize("service_name", sorted(VOICE_SERVICES))
def test_voice_profiles_publish_explicit_pre_execution_resource_budgets(
    compose_document: dict,
    service_name: str,
) -> None:
    environment = compose_document["services"][service_name]["environment"]
    for key in (
        "VOICE_RESOURCE_MAX_RAM_MB",
        "VOICE_RESOURCE_MAX_VRAM_MB",
        "VOICE_RESOURCE_MAX_CONCURRENT_BACKENDS",
        "VOICE_RESOURCE_MAX_QUEUE_DEPTH",
    ):
        assert str(environment[key]).strip()
    if service_name != "voice-runtime-nvidia":
        assert str(environment["VOICE_RESOURCE_MAX_VRAM_MB"]) == "0"


@pytest.mark.parametrize("service_name", sorted(RESTRICTED_SERVICES))
def test_restricted_profiles_receive_explicit_admission_budgets(
    compose_document: dict,
    service_name: str,
) -> None:
    environment = compose_document["services"][service_name]["environment"]
    for key in (
        "ANANTA_RESTRICTED_INFERENCE_MAX_RAM_BYTES",
        "ANANTA_RESTRICTED_INFERENCE_MAX_VRAM_BYTES",
        "ANANTA_RESTRICTED_INFERENCE_MAX_LOADED_MODELS",
        "ANANTA_RESTRICTED_INFERENCE_MAX_IN_FLIGHT",
        "ANANTA_RESTRICTED_INFERENCE_MAX_QUEUE",
    ):
        assert str(environment[key]).startswith("${ANANTA_RESTRICTED_INFERENCE_")


def test_versioned_hardware_profiles_match_compose_voice_backend_policy(compose_document: dict) -> None:
    hardware_profiles = json.loads(HARDWARE_PROFILES_FILE.read_text(encoding="utf-8"))["profiles"]
    for profile in hardware_profiles:
        suffix = "cpu" if profile["compose_profile"].endswith("cpu") else "nvidia"
        configured = compose_document["services"][f"voice-runtime-{suffix}"]["environment"]
        assert str(configured["VOICE_POLICY_ALLOWED_BACKENDS"]).split(",") == profile["voice_backends"]


@pytest.mark.parametrize("service_name", sorted(RESTRICTED_SERVICES))
def test_restricted_execution_profiles_require_ready_not_degraded_health(
    compose_document: dict, service_name: str
) -> None:
    health_probe = " ".join(compose_document["services"][service_name]["healthcheck"]["test"])
    assert "payload.get('status') == 'ready'" in health_probe


@pytest.mark.parametrize("service_name", sorted(VOICE_SERVICES))
def test_voice_execution_profiles_require_ready_not_degraded_health(
    compose_document: dict, service_name: str
) -> None:
    health_probe = " ".join(compose_document["services"][service_name]["healthcheck"]["test"])
    assert "payload.get('status') == 'ready'" in health_probe


def test_dockerfiles_use_pinned_non_root_minimal_images() -> None:
    voice = VOICE_DOCKERFILE.read_text(encoding="utf-8")
    restricted = RESTRICTED_DOCKERFILE.read_text(encoding="utf-8")
    generative = GENERATIVE_JUDGE_DOCKERFILE.read_text(encoding="utf-8")

    for dockerfile in (voice, restricted, generative):
        assert "python:3.11.15-slim-bookworm@sha256:" in dockerfile
        assert "HEALTHCHECK" in dockerfile
        assert "requirements.lock" not in dockerfile
        assert "USER root\nEXPOSE" not in dockerfile

    assert "USER 10001:10001" in voice
    assert 'CMD ["python", "-m", "voice_runtime.app"]' in voice
    assert "payload.get('status') == 'ready'" in voice
    assert voice.index("FROM base AS cpu") < voice.index("requirements.voice-cpu.txt")
    assert "requirements.voice-nvidia.txt" in voice
    assert 'NVIDIA_REQUIRE_CUDA="cuda>=12.4"' in voice
    assert "USER 10002:10002" in restricted
    assert 'CMD ["python", "-m", "worker.runtime.restricted_inference_app"]' in restricted
    assert restricted.index("FROM base AS cpu") < restricted.index("requirements.restricted-inference-cpu.txt")
    assert restricted.count("FROM base AS nvidia") == 1
    assert "FROM cpu AS nvidia" not in restricted
    assert "requirements.restricted-inference-nvidia.txt" in restricted
    assert "agent.ai_agent" not in restricted
    for required_module in (
        "model_inference_adapter_registry.py",
        "restricted_inference_cache.py",
        "restricted_inference_config_service.py",
    ):
        assert required_module in restricted
    assert "USER 10003:10003" in generative
    assert 'CMD ["python", "-m", "worker.runtime.generative_judge_app"]' in generative
    assert "payload.get('status') == 'ready'" in generative
    assert "requirements.generative-judge-cpu.txt" in generative
    assert "agent.ai_agent" not in generative
    assert "restricted_inference_app" not in generative

    judge_requirements = [
        line.strip()
        for line in GENERATIVE_JUDGE_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert all(line.startswith("--") or "==" in line for line in judge_requirements)
    assert "torch==2.6.0+cpu" in judge_requirements
    assert not any("nvidia" in line.lower() or "cuda" in line.lower() for line in judge_requirements)


def test_hub_build_context_excludes_secrets_and_local_runtime_state() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    for required_pattern in (
        "**/.env",
        "**/.env.*",
        "**/node_modules/",
        "**/.venv/",
        ".claude/",
        ".opencode/",
        "/project-workspaces/",
        "/models/",
        "/artifacts/",
    ):
        assert required_pattern in dockerignore


@pytest.mark.parametrize(
    ("profile", "expected_services"),
    [
        ("voice-production-minimal", {"ai-agent-hub", "voice-runtime-minimal", "restricted-inference-minimal"}),
        ("voice-production-cpu", {"ai-agent-hub", "voice-runtime-cpu", "restricted-inference-cpu"}),
        ("voice-production-nvidia", {"ai-agent-hub", "voice-runtime-nvidia", "restricted-inference-nvidia"}),
    ],
)
def test_compose_profiles_render_without_contacting_a_daemon(profile: str, expected_services: set[str]) -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is not installed")

    environment = os.environ.copy()
    environment.update(
        {
            "INITIAL_ADMIN_PASSWORD": "compose-config-test-password",
            "VOICE_INTERNAL_SERVICE_TOKEN": "compose-test-voice-token-at-least-24-chars",
            "VOICE_PERSONALIZATION_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
            "RESTRICTED_INFERENCE_INTERNAL_TOKEN": "compose-test-restricted-token-at-least-24-chars",
        }
    )
    command = [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "--profile",
        profile,
        "config",
        "--services",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert set(completed.stdout.splitlines()) == expected_services


def test_optional_generative_judge_profile_renders_only_hub_and_dedicated_worker() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is not installed")
    environment = os.environ.copy()
    environment.update(
        {
            "INITIAL_ADMIN_PASSWORD": "compose-config-test-password",
            "VOICE_INTERNAL_SERVICE_TOKEN": "compose-test-voice-token-at-least-24-chars",
            "VOICE_PERSONALIZATION_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
            "RESTRICTED_INFERENCE_INTERNAL_TOKEN": "compose-test-restricted-token-at-least-24-chars",
            "VOICE_GENERATIVE_JUDGE_WORKER_TOKEN": "compose-test-judge-token-at-least-24-chars",
        }
    )
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--profile",
            "voice-generative-judge",
            "config",
            "--services",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert set(completed.stdout.splitlines()) == {"ai-agent-hub", GENERATIVE_JUDGE_SERVICE}
