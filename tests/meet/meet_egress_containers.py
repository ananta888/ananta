"""Exact label/ID-owned disposable resources; never host networking or firewall."""

import ipaddress
import json
import re
import subprocess
import time
import uuid
from contextlib import ExitStack
from pathlib import Path


def docker(*arguments):
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=25, check=False)
    assert result.returncode == 0, result.stderr[-1500:]
    return (result.stdout + result.stderr if arguments[0] == "logs" else result.stdout).strip()


class EgressContainers:
    def __init__(self, image):
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", image), "immutable test image required"
        assert json.loads(docker("image", "inspect", image))[0]["Id"] == image
        self.image, self.owner = image, "meet-egress-test-" + str(uuid.uuid4())
        self.cleanup = ExitStack()
        self.network = None

    def __enter__(self):
        self.network = docker(
            "network", "create", "--internal", "--label", "ananta.test-owner=" + self.owner, self.owner
        )
        self.cleanup.callback(self.remove_network)
        try:
            row = json.loads(docker("network", "inspect", self.network))[0]
            assert row["Id"] == self.network and row["Internal"] is True and row["Driver"] == "bridge"
        except Exception:
            self.cleanup.close()
            raise
        return self

    def __exit__(self, *args):
        self.cleanup.close()

    def remove_network(self):
        row = json.loads(docker("network", "inspect", self.network))[0]
        assert row["Id"] == self.network and row["Labels"]["ananta.test-owner"] == self.owner
        assert not row["Containers"], "do not remove a network with unowned remaining consumers"
        docker("network", "rm", self.network)

    def inspect(self, identifier):
        row = json.loads(docker("inspect", identifier))[0]
        assert row["Id"] == identifier and row["Image"] == self.image
        assert row["Config"]["Labels"]["ananta.test-owner"] == self.owner
        assert not row["HostConfig"]["Privileged"] and row["HostConfig"]["PidMode"] == ""
        assert row["HostConfig"]["NetworkMode"] != "host"
        assert all(m["Destination"] != "/var/run/docker.sock" for m in row["Mounts"])
        return row

    def remove(self, identifier):
        self.inspect(identifier)
        docker("rm", "-f", identifier)

    def create(self, role, *, namespace=None, configuration=None):
        assert role in {"endpoint", "worker", "guard"}
        if namespace:
            assert role == "worker"
            parent = self.inspect(namespace)
            assert parent["HostConfig"]["NetworkMode"] == self.network
            assert parent["State"]["Health"]["Status"] == "healthy"
        args = [
            "create",
            "--label",
            "ananta.test-owner=" + self.owner,
            "--network",
            "container:" + namespace if namespace else self.network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            "64m",
            "--pids-limit",
            "16",
            "--cpus",
            "0.5",
        ]
        if role == "guard":
            args += [
                "--cap-add",
                "NET_ADMIN",
                "--cap-add",
                "NET_BIND_SERVICE",
                "--dns",
                "127.0.0.1",
                "--tmpfs",
                "/run/meet-egress:rw,size=64k,mode=0700",
                "--tmpfs",
                "/run/xtables:rw,size=64k,mode=0700",
                "--mount",
                f"type=bind,src={configuration},dst=/etc/ananta/egress.json,readonly",
            ]
        else:
            probe = Path(__file__).with_name("meet_egress_probe.py").resolve()
            args += [
                "--user",
                "1000:1000",
                "--env",
                "PYTHONPATH=/app",
                "--no-healthcheck",
                "--mount",
                f"type=bind,src={probe},dst=/probe.py,readonly",
            ]
        args += [self.image]
        if role != "guard":
            args += ["python", "/probe.py", role]
        identifier = docker(*args)
        assert re.fullmatch(r"[0-9a-f]{64}", identifier)
        self.cleanup.callback(self.remove, identifier)
        self.inspect(identifier)
        docker("start", identifier)
        if role != "guard":
            self.probe_ready(identifier, 18080 if role == "endpoint" else 8094)
        return identifier

    def address(self, identifier):
        row = self.inspect(identifier)
        assert row["State"]["Running"], "test endpoint stopped before address admission"
        networks = row["NetworkSettings"]["Networks"]
        assert len(networks) == 1
        value = next(iter(networks.values()))["IPAddress"]
        assert ipaddress.IPv4Address(value).is_private
        return value

    def probe_ready(self, identifier, port):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            assert self.inspect(identifier)["State"]["Running"], "test probe stopped before HTTP readiness"
            try:
                self.probe(identifier, "http", "127.0.0.1", str(port), "/counts")
                return
            except AssertionError:
                time.sleep(0.1)
        raise AssertionError("bounded test endpoint readiness missing")

    def probe(self, identifier, *arguments):
        self.inspect(identifier)
        return json.loads(docker("exec", identifier, "python", "/probe.py", *arguments))

    def healthy(self, identifier):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = self.inspect(identifier)["State"]
            if not state["Running"]:
                diagnostic = docker("logs", identifier)
                allowed = {
                    "meet_egress_guard_failed:" + phase
                    for phase in ("configuration", "filter", "dns_bind", "readiness", "dns_serve")
                }
                raise AssertionError(
                    {
                        "guard_failure": diagnostic if diagnostic in allowed else "redacted",
                        "exit_code": state["ExitCode"],
                        "oom": state["OOMKilled"],
                    }
                )
            if state.get("Health", {}).get("Status") == "healthy":
                return
            time.sleep(0.1)
        raise AssertionError("bounded guard readiness missing")
