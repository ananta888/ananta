"""Template and actual local Compose rendering, never serving keys or containers."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.meet-media.yml"


def test_template_selects_one_explicit_device_and_readonly_worker_without_more_mounts():
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    for name in ("meet-ollama", "meet-media-worker"):
        assert services[name]["gpus"] == [{"driver": "nvidia", "device_ids": ["${MEET_MEDIA_GPU_DEVICE_ID:-0}"]}]
    worker = services["meet-media-worker"]
    assert worker["read_only"] is True and worker["init"] is True
    assert worker["tmpfs"] == ["/tmp:size=256m,mode=1777"] and worker["shm_size"] == "256m"
    assert len(worker["volumes"]) == 3
    assert worker["volumes"][0].endswith("/models:/models:ro")
    assert worker["volumes"][1].endswith("/worker-key:/run/secrets/meet-worker-key:ro")
    assert worker["volumes"][2].endswith("/worker-state:/state")
    assert not worker.get("ports") and not worker.get("privileged")
    assert worker["cap_drop"] == ["ALL"] and "no-new-privileges:true" in worker["security_opt"]


@pytest.mark.parametrize("selected", [None, "1", "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"])
def test_local_compose_renderer_preserves_device_selection_and_mount_permissions(selected, tmp_path):
    env = {"PATH": os.environ["PATH"], "MEET_MEDIA_STATE_DIR": str(tmp_path / "synthetic-state")}
    if selected is not None:
        env["MEET_MEDIA_GPU_DEVICE_ID"] = selected
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-p",
            "ananta-test-device-render",
            "-f",
            str(COMPOSE),
            "config",
            "--format",
            "json",
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, "local Compose rendering failed; environment details redacted"
    services = json.loads(result.stdout)["services"]
    for name in ("meet-ollama", "meet-media-worker"):
        assert len(services[name]["gpus"]) == 1
        gpu = services[name]["gpus"][0]
        assert gpu["driver"] == "nvidia" and gpu["device_ids"] == [selected or "0"]
    worker = services["meet-media-worker"]
    assert worker["read_only"] is True and worker["cpus"] == 4.0
    assert int(worker["mem_limit"]) == 4 * 1024**3 and worker["pids_limit"] == 256
    mounts = {v["target"]: v for v in worker["volumes"]}
    assert set(mounts) == {"/models", "/run/secrets/meet-worker-key", "/state"}
    assert mounts["/models"]["read_only"] is True and mounts["/run/secrets/meet-worker-key"]["read_only"] is True
    assert mounts["/state"].get("read_only", False) is False
    assert all(v["source"].startswith(str(tmp_path / "synthetic-state") + "/") for v in mounts.values())
    assert worker["environment"]["MEET_HUB_DIALOG_URL"] == ""  # Never inherit the operator environment.
    assert not (tmp_path / "synthetic-state").exists()  # Rendering provisions nothing.
