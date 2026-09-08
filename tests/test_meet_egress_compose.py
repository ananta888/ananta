"""Real secret-free Compose merge; render only, no container or runtime mutation."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def render(tmp_path, *, guarded, policy=True):
    env = {"PATH": os.environ["PATH"], "MEET_MEDIA_STATE_DIR": str(tmp_path / "synthetic-state")}
    if policy:
        env["MEET_EGRESS_POLICY_FILE"] = str(tmp_path / "synthetic-policy.json")
    args = ["docker", "compose", "--env-file", "/dev/null", "-p", "meet-egress-render-only"]
    args += ["-f", str(ROOT / "docker-compose.meet-media.yml")]
    if guarded:
        args += ["-f", str(ROOT / "docker-compose.meet-egress.yml")]
    return subprocess.run(
        args + ["config", "--format", "json"], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=10
    )


def test_additive_profile_preserves_worker_limits_and_gates_namespace_start(tmp_path):
    base, guarded = render(tmp_path, guarded=False), render(tmp_path, guarded=True)
    assert base.returncode == guarded.returncode == 0
    base, guarded = json.loads(base.stdout)["services"], json.loads(guarded.stdout)["services"]
    before, worker, guard = base["meet-media-worker"], guarded["meet-media-worker"], guarded["meet-egress"]
    changed = {key for key in before.keys() | worker.keys() if before.get(key) != worker.get(key)}
    assert changed == {"networks", "network_mode", "depends_on"}
    assert "meet-egress" not in base and "network_mode" not in before
    assert worker["network_mode"] == "service:meet-egress" and not worker.get("networks")
    assert worker["depends_on"]["meet-egress"]["condition"] == "service_healthy"
    assert worker["cap_drop"] == ["ALL"] and not worker.get("cap_add")
    assert guard["cap_add"] == ["NET_ADMIN", "NET_BIND_SERVICE"] and guard["cap_drop"] == ["ALL"]
    assert guard["dns"] == ["127.0.0.1"] and guard["read_only"] is True
    assert guard["networks"] == {"meet-compute": {"aliases": ["meet-media-worker"]}}
    assert guard["user"] == "0:0" and "no-new-privileges:true" in guard["security_opt"]
    assert not any(guard.get(key) for key in ("ports", "privileged", "pid", "network_mode", "gpus"))
    assert guard["volumes"] == [
        {
            "type": "bind",
            "source": str(tmp_path / "synthetic-policy.json"),
            "target": "/etc/ananta/egress.json",
            "read_only": True,
            "bind": {"create_host_path": False},
        }
    ]
    assert not (tmp_path / "synthetic-policy.json").exists()


@pytest.mark.parametrize("guarded,expected", [(False, True), (True, False)])
def test_only_opt_in_profile_requires_explicit_policy(tmp_path, guarded, expected):
    result = render(tmp_path, guarded=guarded, policy=False)
    assert (result.returncode == 0) is expected
