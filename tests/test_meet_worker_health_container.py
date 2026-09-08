"""Actual isolated CPU-only Worker liveness; never touches a running Worker or GPU."""

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.mark.timeout(75)
@pytest.mark.skipif(os.environ.get("MEET_WORKER_HEALTH_GATE") != "1", reason="explicit private Worker container gate")
def test_health_probe_runs_as_nonroot_inside_private_bounded_worker_without_model_or_task(tmp_path):
    image = os.environ.get("MEET_WORKER_HEALTH_IMAGE", "")
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", image), "explicit locally installed immutable Worker image required"
    root = Path(__file__).resolve().parents[1]
    key = tmp_path / "synthetic-worker-key"
    key.write_bytes(b"synthetic-worker-health-only-key-material")
    key.chmod(0o400)
    run_id = uuid4().hex
    name = "ananta-meet-health-" + run_id[:12]

    def docker(*args, timeout=10, check=True):
        result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
        if check:
            assert result.returncode == 0, "isolated Worker container command failed; runtime details redacted"
        return result

    image_info = json.loads(docker("image", "inspect", image).stdout)[0]
    packaged = os.environ.get("MEET_WORKER_HEALTH_PACKAGED") == "1"
    if packaged:
        probe = image_info["Config"]["Healthcheck"]
        assert probe["Test"] == ["CMD", "python", "-m", "worker.meet_media.health"]
        assert probe["Timeout"] == 3_000_000_000 and probe["Retries"] == 3
    probe_command = [] if packaged else ["--health-cmd", "python -m worker.meet_media.health"]
    source_mounts = (
        []
        if packaged
        else [
            "--mount",
            f"type=bind,src={root / 'worker/meet_media'},dst=/app/worker/meet_media,readonly",
            "--mount",
            f"type=bind,src={root / 'ananta_contracts'},dst=/app/ananta_contracts,readonly",
        ]
    )

    def await_health(expected):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = json.loads(docker("inspect", name).stdout)[0]
            assert state["State"]["Running"], "private Worker failed before liveness"
            if state["State"]["Health"]["Status"] == expected:
                return state
            time.sleep(0.2)
        pytest.fail("bounded Worker health transition missing")

    try:
        docker(
            "run",
            "-d",
            "--name",
            name,
            "--label",
            "ananta.test-run=" + run_id,
            "--network",
            "none",
            "--user",
            f"{os.geteuid()}:{os.getegid()}",
            "--cpus",
            "0.5",
            "--memory",
            "256m",
            "--pids-limit",
            "32",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            f"/state:rw,size=16m,mode=700,uid={os.geteuid()},gid={os.getegid()}",
            "--tmpfs",
            "/tmp:rw,size=16m,mode=1777",
            *source_mounts,
            "--mount",
            f"type=bind,src={key},dst=/run/secrets/worker-key,readonly",
            "-e",
            "MEET_WORKER_KEY_FILE=/run/secrets/worker-key",
            "-e",
            "MEET_DIALOG_ENABLED=0",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            *probe_command,
            "--health-interval",
            "1s",
            "--health-timeout",
            "3s",
            "--health-start-period",
            "1s",
            "--health-retries",
            "2",
            image,
            "python",
            "-m",
            "worker.meet_media.server",
        )
        state = await_health("healthy")
        assert state["HostConfig"]["NetworkMode"] == "none"
        assert state["HostConfig"]["ReadonlyRootfs"] is True
        assert state["HostConfig"]["NanoCpus"] == 500_000_000
        assert state["HostConfig"]["Memory"] == 256 * 1024**2
        assert state["HostConfig"]["PidsLimit"] == 32
        assert state["HostConfig"]["CapDrop"] == ["ALL"]
        assert state["HostConfig"]["DeviceRequests"] in (None, [])
        assert all(entry["Output"] == "" for entry in state["State"]["Health"]["Log"])
        check = """
import hashlib, importlib.util, os, sqlite3, sys
from pathlib import Path
assert os.geteuid() != 0
assert importlib.util.find_spec('agent') is None
assert hashlib.sha256(Path('/app/worker/meet_media/health.py').read_bytes()).hexdigest() == sys.argv[1]
with sqlite3.connect('/state/leases.sqlite') as db:
    assert db.execute('SELECT count(*) FROM leases').fetchone() == (0,)
"""
        digest = hashlib.sha256((root / "worker/meet_media/health.py").read_bytes()).hexdigest()
        docker("exec", name, "python", "-c", check, digest)
        if packaged:
            assert len(state["Mounts"]) == 1  # Only the synthetic key, never source code.
            assert state["Mounts"][0]["Destination"] == "/run/secrets/worker-key"
            assert state["Mounts"][0]["RW"] is False
            assert set(state["HostConfig"]["Tmpfs"]) == {"/tmp", "/state"}
        # Suspend only this test-owned listener, not its health subprocesses.
        # Docker must detect missing HTTP progress even while PID 1 is alive.
        docker("kill", "--signal=STOP", name)
        try:
            stopped = await_health("unhealthy")
            assert stopped["State"]["Pid"] == state["State"]["Pid"]
            probe = docker("exec", name, "python", "-m", "worker.meet_media.health", timeout=5, check=False)
            assert probe.returncode == 1 and probe.stdout == "" and probe.stderr == ""
        finally:
            docker("kill", "--signal=CONT", name)
        recovered = await_health("healthy")
        assert recovered["RestartCount"] == 0
        docker("exec", name, "python", "-c", check, digest)
    finally:
        owner = docker("inspect", "-f", '{{index .Config.Labels "ananta.test-run"}}', name, check=False)
        if owner.returncode == 0 and owner.stdout.strip() == run_id:
            docker("rm", "-f", name)
