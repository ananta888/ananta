from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "docker/compose-next/compose.voice-restricted.yml"
RESTRICTED_SERVICES = (
    "restricted-inference-minimal",
    "restricted-inference-cpu",
    "restricted-inference-nvidia",
)


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_restricted_inference_profiles_are_non_root_read_only_and_capability_dropped() -> None:
    services = _compose()["services"]

    for service_name in RESTRICTED_SERVICES:
        service = services[service_name]
        assert service["user"] == "10002:10002"
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in service["security_opt"]
        assert service["pids_limit"] == 256
        assert "ports" not in service


def test_restricted_inference_profiles_have_no_egress_network() -> None:
    compose = _compose()
    services = compose["services"]

    assert compose["networks"]["restricted-inference-control"]["internal"] is True
    for service_name in RESTRICTED_SERVICES:
        assert set(services[service_name]["networks"]) == {"restricted-inference-control"}


def test_restricted_model_mount_is_read_only_and_runtime_is_offline() -> None:
    services = _compose()["services"]

    for service_name in RESTRICTED_SERVICES:
        service = services[service_name]
        model_mount = next(
            volume
            for volume in service["volumes"]
            if volume.get("target") == "/models/restricted"
        )
        environment = service["environment"]
        assert model_mount["read_only"] is True
        assert model_mount["bind"]["create_host_path"] is False
        assert environment["HF_HUB_OFFLINE"] == "1"
        assert environment["TRANSFORMERS_OFFLINE"] == "1"
        assert environment["RESTRICTED_INFERENCE_ALLOW_MODEL_DOWNLOAD"] == "false"
        assert environment["RESTRICTED_INFERENCE_REQUIRE_INTERNAL_AUTH"] == "true"
        assert all(
            "noexec" in mount and "nosuid" in mount and "nodev" in mount
            for mount in service["tmpfs"]
        )


def test_restricted_profiles_define_cpu_memory_and_pid_limits() -> None:
    services = _compose()["services"]

    for service_name in RESTRICTED_SERVICES:
        service = services[service_name]
        limits = service["deploy"]["resources"]["limits"]
        assert service["cpus"]
        assert service["mem_limit"]
        assert limits["cpus"]
        assert limits["memory"]
        assert limits["pids"] == 256
