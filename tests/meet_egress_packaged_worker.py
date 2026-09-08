"""Bounded installed media Worker consumer of a separately verified test guard."""

import json
import re
import time
from pathlib import Path

from tests.meet_egress_containers import docker


class PackagedEgressWorker:
    def __init__(self, resources, guard, image):
        assert re.fullmatch(r"sha256:[a-f0-9]{64}", image)
        assert json.loads(docker("image", "inspect", image))[0]["Id"] == image
        state = resources.inspect(guard)
        assert state["HostConfig"]["NetworkMode"] == resources.network
        assert state["State"]["Health"]["Status"] == "healthy"
        self.resources, self.guard, self.image = resources, guard, image
        self.identifier = None

    def start(self, key):
        assert self.identifier is None and key.is_file() and not key.is_symlink()
        root = Path(__file__).resolve().parents[1]
        self.identifier = docker(
            "create",
            "--label",
            "ananta.test-owner=" + self.resources.owner,
            "--network",
            "container:" + self.guard,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            "1000:1000",
            "--security-opt",
            "seccomp=" + str(root / "docker/meet-media/chromium-seccomp.json"),
            "--memory",
            "256m",
            "--pids-limit",
            "32",
            "--cpus",
            "0.5",
            "--tmpfs",
            "/state:rw,size=16m,mode=0700,uid=1000,gid=1000",
            "--tmpfs",
            "/tmp:rw,size=16m,mode=1777",
            "--mount",
            f"type=bind,src={key},dst=/run/secrets/worker-key,readonly",
            "--mount",
            f"type=bind,src={root / 'tests/meet_egress_probe.py'},dst=/probe.py,readonly",
            "--env",
            "MEET_WORKER_KEY_FILE=/run/secrets/worker-key",
            "--env",
            "MEET_DIALOG_ENABLED=0",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--health-interval",
            "1s",
            "--health-start-period",
            "1s",
            self.image,
        )
        assert re.fullmatch(r"[a-f0-9]{64}", self.identifier)
        self.resources.cleanup.callback(self.close)
        self.inspect()
        docker("start", self.identifier)
        until = time.monotonic() + 20
        while time.monotonic() < until:
            state = self.inspect()["State"]
            assert state["Running"], "packaged Worker terminated before liveness"
            if state["Health"]["Status"] == "healthy":
                assert all(entry["Output"] == "" for entry in state["Health"]["Log"])
                return
            time.sleep(0.1)
        raise AssertionError("bounded packaged Worker liveness missing")

    def inspect(self):
        row = json.loads(docker("inspect", self.identifier))[0]
        assert row["Id"] == self.identifier and row["Image"] == self.image
        assert row["Config"]["Labels"]["ananta.test-owner"] == self.resources.owner
        assert row["HostConfig"]["NetworkMode"] == "container:" + self.guard
        assert row["HostConfig"]["ReadonlyRootfs"] and row["HostConfig"]["CapDrop"] == ["ALL"]
        assert not row["HostConfig"]["CapAdd"] and not row["HostConfig"]["Privileged"]
        assert row["HostConfig"]["PidMode"] == "" and not row["HostConfig"]["DeviceRequests"]
        assert row["Config"]["User"] == "1000:1000"
        assert {m["Destination"] for m in row["Mounts"]} == {"/run/secrets/worker-key", "/probe.py"}
        assert all(not m["RW"] for m in row["Mounts"])
        return row

    def probe(self, *arguments):
        self.inspect()
        return json.loads(docker("exec", self.identifier, "python", "/probe.py", *arguments))

    def no_executed_leases(self):
        self.inspect()
        script = (
            "import importlib.util,os,sqlite3; assert os.geteuid()!=0; "
            "assert importlib.util.find_spec('agent') is None; "
            "db=sqlite3.connect('/state/leases.sqlite'); "
            "assert db.execute('SELECT count(*) FROM leases').fetchone()==(0,); db.close()"
        )
        assert docker("exec", self.identifier, "python", "-c", script) == ""

    def close(self):
        self.inspect()
        docker("rm", "-f", self.identifier)
