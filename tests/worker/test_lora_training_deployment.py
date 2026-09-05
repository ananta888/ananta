from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker" / "compose-next" / "compose.lora-training.yml"
DOCKERFILE = ROOT / "docker" / "compose-next" / "Dockerfile.lora-training-worker"
STACK_RUNNER = ROOT / "scripts" / "run-lora-training-stack.sh"


def test_compose_is_internal_non_root_read_only_and_profile_scoped() -> None:
    payload = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = payload["services"]
    for name in ("lora-training-worker-mock", "lora-training-worker-cpu", "lora-training-worker-nvidia"):
        service = services[name]
        assert service["user"] == "10005:10005"
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in service["security_opt"]
        assert "ports" not in service
        assert service["networks"] == {"lora-training-control": {"aliases": ["lora-training-worker"]}}
        model_mount = next(item for item in service["volumes"] if item["target"] == "/models/base")
        workspace_mount = next(item for item in service["volumes"] if item["target"] == "/project-workspaces")
        assert model_mount["read_only"] is True
        assert workspace_mount["read_only"] is True
    assert payload["networks"]["lora-training-control"]["internal"] is True
    assert services["lora-training-worker-nvidia"]["deploy"]["resources"]["reservations"]["devices"][0][
        "capabilities"
    ] == ["gpu"]
    assert services["lora-training-worker-nvidia"]["environment"][
        "ANANTA_LORA_TRAINING_CUDA_MEMORY_FRACTION"
    ] == "${ANANTA_LORA_TRAINING_CUDA_MEMORY_FRACTION:-0.90}"
    assert any(
        value.startswith("/tmp:") and ",exec," in value
        for value in services["lora-training-worker-nvidia"]["tmpfs"]
    )
    assert "ANANTA_LORA_TRAINING_CUDA_MEMORY_FRACTION" not in services["lora-training-worker-cpu"]["environment"]
    assert "ANANTA_LORA_TRAINING_CUDA_MEMORY_FRACTION" not in services["lora-training-worker-mock"]["environment"]
    assert "deploy" not in services["ai-agent-hub"] or "devices" not in str(services["ai-agent-hub"].get("deploy"))


def test_worker_image_excludes_hub_and_separates_cpu_from_nvidia() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "COPY --chown=10005:10005 agent/" not in dockerfile
    assert "COPY --chown=10005:10005 ananta_contracts /app/ananta_contracts" in dockerfile
    assert "USER 10005:10005" in dockerfile
    assert "FROM base AS cpu" in dockerfile
    assert "FROM base AS nvidia" in dockerfile
    assert "FROM cpu AS nvidia" not in dockerfile
    assert "apt-get install --yes --no-install-recommends gcc libc6-dev" in dockerfile
    assert "FROM python:3.11.15-slim-bookworm@sha256:" in dockerfile
    assert "https://codeload.github.com/ggml-org/llama.cpp/tar.gz/refs/tags/v0.4.0" in dockerfile
    assert "--checksum=sha256:9c2948aa9c79c92dd0e4c98e11ff5cf76dfdcaebdeb18e3e93409e9a98aefdab" in dockerfile
    assert "ANANTA_UNSLOTH_LLAMA_CPP_SOURCE=/opt/llama.cpp" in dockerfile
    assert "UNSLOTH_LLAMA_CPP_PATH=/tmp/ananta-unsloth/llama.cpp" in dockerfile
    assert "COPY --from=llama-cpp-builder --chown=10005:10005 /opt/llama.cpp /opt/llama.cpp" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/lora-training-worker-entrypoint"]' in dockerfile
    assert "runtime_configured" in dockerfile and "auth_configured" in dockerfile
    assert "Authorization" in dockerfile and "ANANTA_LORA_TRAINING_TOKEN" in dockerfile


def test_training_requirement_sets_are_exactly_pinned() -> None:
    for filename in ("requirements.lora-training-cpu.txt", "requirements.lora-training-nvidia.txt"):
        lines = (COMPOSE.parent / filename).read_text(encoding="utf-8").splitlines()
        dependencies = [line for line in lines if line and not line.startswith("#") and not line.startswith("--")]
        assert dependencies
        assert all(re.fullmatch(r"[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_.-]+\])?==[^=\s]+", line) for line in dependencies)


def test_supported_stack_entrypoint_binds_hub_and_worker_profiles() -> None:
    expected = {
        "lora-training-mock": ("dry_run", "mock", "mock", "mock", "none"),
        "lora-training-cpu": ("live", "peft_trl", "peft_trl", "cpu", "none"),
        "lora-training-nvidia": (
            "live",
            "peft_trl",
            "peft_trl,unsloth,unsloth_vision,unsloth_audio,unsloth_embedding",
            "nvidia",
            "rtx3080-safe",
        ),
    }
    assert STACK_RUNNER.stat().st_mode & 0o111
    for profile, values in expected.items():
        result = subprocess.run(
            [str(STACK_RUNNER), profile, "--print-profile"],
            check=True,
            capture_output=True,
            text=True,
        )
        rendered = dict(line.split("=", maxsplit=1) for line in result.stdout.splitlines())
        assert (
            rendered["mode"],
            rendered["default_backend"],
            rendered["worker_backends"],
            rendered["resource_profile"],
            rendered["gpu_profile"],
        ) == values


def test_supported_stack_entrypoint_renders_hardened_real_compose_profiles(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is unavailable")
    compose_version = subprocess.run(
        ["docker", "compose", "version"],
        check=False,
        capture_output=True,
        text=True,
    )
    if compose_version.returncode != 0:
        pytest.skip("docker compose is unavailable")

    expected = {
        "lora-training-mock": ("lora-training-worker-mock", "dry_run", "mock", "mock", "none"),
        "lora-training-cpu": ("lora-training-worker-cpu", "live", "peft_trl", "cpu", "none"),
        "lora-training-nvidia": (
            "lora-training-worker-nvidia",
            "live",
            "peft_trl,unsloth,unsloth_vision,unsloth_audio,unsloth_embedding",
            "nvidia",
            "rtx3080-safe",
        ),
    }
    worker_names = {values[0] for values in expected.values()}
    environment = dict(os.environ)
    environment.update(
        {
            "POSTGRES_PASSWORD": "test-postgres-password",
            "INITIAL_ADMIN_PASSWORD": "test123",
            "SECRET_KEY": "test-secret-key-with-at-least-thirty-two-chars",
            "AGENT_TOKEN_HUB": "hub-token",
            "AGENT_TOKEN_ALPHA": "alpha-token",
            "AGENT_TOKEN_BETA": "beta-token",
            "AGENT_TOKEN_GAMMA": "gamma-token",
            "AGENT_TOKEN_DELTA": "delta-token",
            "GRAFANA_PASSWORD": "test-grafana-password",
            "ANANTA_LORA_TRAINING_INTERNAL_TOKEN": "test-only-token-0123456789abcdef",
            "ANANTA_LORA_TRAINING_MODEL_CATALOG_JSON": json.dumps(
                {
                    "local/test": {
                        "relative_path": ".",
                        "snapshot_hash": "a" * 64,
                    }
                }
            ),
            "ANANTA_LORA_TRAINING_MODEL_DIR": str(ROOT),
            "ANANTA_LORA_TRAINING_ENV_FILE": str(tmp_path / "does-not-exist.env"),
        }
    )
    for profile, (worker_name, mode, backends, resource_profile, gpu_profile) in expected.items():
        rendered = subprocess.run(
            [str(STACK_RUNNER), profile, "config", "--format", "json"],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        payload = json.loads(rendered.stdout)
        services = payload["services"]
        assert worker_name in services
        assert not ((worker_names - {worker_name}) & set(services))
        hub = services["ai-agent-hub"]
        worker = services[worker_name]
        assert hub["environment"]["ANANTA_LORA_TRAINING_MODE"] == mode
        assert hub["environment"]["ANANTA_LORA_TRAINING_WORKER_BACKENDS"] == backends
        assert hub["environment"]["ANANTA_LORA_TRAINING_RESOURCE_PROFILE"] == resource_profile
        assert hub["environment"]["ANANTA_LORA_TRAINING_GPU_PROFILE"] == gpu_profile
        assert worker["read_only"] is True
        assert worker["user"] == "10005:10005"
        assert worker["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in worker["security_opt"]
        assert worker["environment"]["HF_HUB_OFFLINE"] == "1"
        assert worker["environment"]["TRANSFORMERS_OFFLINE"] == "1"
        if resource_profile == "nvidia":
            assert worker["environment"][
                "ANANTA_LORA_TRAINING_GPU_PROFILE"
            ] == "rtx3080-safe"
        assert "Authorization" in " ".join(worker["healthcheck"]["test"])
        assert payload["networks"]["lora-training-control"]["internal"] is True
        assert not any(volume.get("target") == "/models/base" for volume in hub.get("volumes", []))


def test_built_minimal_worker_is_non_root_read_only_offline_and_auth_ready(tmp_path: Path) -> None:
    image = str(os.getenv("ANANTA_LORA_TRAINING_TEST_IMAGE") or "").strip()
    if not image:
        pytest.skip("set ANANTA_LORA_TRAINING_TEST_IMAGE to run the built-container smoke")
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is unavailable")

    model_root = tmp_path / "models"
    workspace_root = tmp_path / "workspaces"
    model_root.mkdir()
    workspace_root.mkdir()
    name = f"ananta-lora-verification-{uuid.uuid4().hex[:12]}"
    token = "test-only-container-token-0123456789"
    command = [
        "docker", "run", "--detach", "--name", name,
        "--read-only", "--user", "10005:10005",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "128", "--network", "none",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,uid=10005,gid=10005",
        "--tmpfs", "/run:rw,noexec,nosuid,nodev,uid=10005,gid=10005",
        "--tmpfs", "/var/lib/ananta-lora:rw,nosuid,nodev,uid=10005,gid=10005",
        "--volume", f"{model_root}:/models/base:ro",
        "--volume", f"{workspace_root}:/project-workspaces:ro",
        "--env", f"ANANTA_LORA_TRAINING_TOKEN={token}",
        "--env", "ANANTA_LORA_TRAINING_BACKENDS=mock",
        "--env", "ANANTA_LORA_TRAINING_RESOURCE_PROFILE=mock",
        "--env", "ANANTA_LORA_TRAINING_STATE_ROOT=/var/lib/ananta-lora",
        "--env", "ANANTA_LORA_TRAINING_WORKSPACE_ROOT=/project-workspaces",
        "--env", "ANANTA_LORA_TRAINING_DATASET_ROOT=/project-workspaces",
        "--env", "ANANTA_LORA_TRAINING_MODEL_ROOT=/models/base",
        image,
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        health = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            probe = subprocess.run(
                [
                    "docker", "exec", name, "python", "-c",
                    "import json,os,urllib.request; "
                    "r=urllib.request.Request('http://127.0.0.1:8095/health',"
                    "headers={'Authorization':'Bearer '+os.environ['ANANTA_LORA_TRAINING_TOKEN']}); "
                    "print(json.dumps(json.load(urllib.request.urlopen(r,timeout=2))))",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if probe.returncode == 0:
                health = json.loads(probe.stdout)
                break
            time.sleep(0.25)
        assert health is not None
        assert health["status"] == "ready", health
        assert health["auth_configured"] is True
        assert health["runtime_configured"] is True

        identity = subprocess.run(
            ["docker", "exec", name, "id", "-u"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert identity.stdout.strip() == "10005"
        import_audit = subprocess.run(
            [
                "docker", "exec", name, "python", "-c",
                "import importlib.util,json; "
                "print(json.dumps({name: importlib.util.find_spec(name) is not None "
                "for name in ('agent','torch','transformers','worker')}))",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        modules = json.loads(import_audit.stdout)
        assert modules == {"agent": False, "torch": False, "transformers": False, "worker": True}

        inspected = json.loads(
            subprocess.run(
                ["docker", "inspect", name],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )[0]
        host = inspected["HostConfig"]
        assert host["ReadonlyRootfs"] is True
        assert host["CapDrop"] == ["ALL"]
        assert "no-new-privileges" in host["SecurityOpt"]
        assert host["PidsLimit"] == 128
        assert host["NetworkMode"] == "none"
        assert all(mount["RW"] is False for mount in inspected["Mounts"] if mount["Type"] == "bind")
        environment = inspected["Config"]["Env"]
        assert "HF_HUB_OFFLINE=1" in environment
        assert "TRANSFORMERS_OFFLINE=1" in environment
    finally:
        subprocess.run(
            ["docker", "rm", "--force", name],
            check=False,
            capture_output=True,
            text=True,
        )
