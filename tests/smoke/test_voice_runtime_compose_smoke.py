from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import requests

_RUN_FLAG = "RUN_VOICE_DOCKER_SMOKE"


def _require_voice_compose_smoke() -> None:
    if str(os.getenv(_RUN_FLAG) or "").strip() != "1":
        pytest.skip(f"set {_RUN_FLAG}=1 to run voice docker smoke test")
    if shutil.which("docker") is None:
        pytest.skip("docker binary not available")


def _compose_cmd(repo_root: Path) -> list[str]:
    return [
        "docker",
        "compose",
        "-f",
        "docker-compose.base.yml",
        "-f",
        "docker-compose.yml",
        "-f",
        "docker-compose.voice-runtime.yml",
    ]


def test_voice_runtime_compose_smoke_health_and_models() -> None:
    _require_voice_compose_smoke()
    repo_root = Path(__file__).resolve().parents[2]
    compose = _compose_cmd(repo_root)

    subprocess.run([*compose, "up", "-d", "--build", "voice-runtime"], cwd=repo_root, check=True, timeout=900)
    try:
        deadline = time.time() + 90
        health_payload = None
        while time.time() < deadline:
            try:
                response = requests.get("http://localhost:8090/health", timeout=3)
                if response.status_code == 200:
                    health_payload = response.json()
                    break
            except Exception:
                pass
            time.sleep(2)
        assert health_payload is not None
        assert health_payload.get("ok") is True

        models = requests.get("http://localhost:8090/v1/models", timeout=5)
        assert models.status_code == 200
        models_payload = models.json()
        assert isinstance(models_payload.get("models"), list)
        assert len(models_payload.get("models") or []) >= 1
    finally:
        subprocess.run([*compose, "down", "-v", "--remove-orphans"], cwd=repo_root, check=False, timeout=240)
