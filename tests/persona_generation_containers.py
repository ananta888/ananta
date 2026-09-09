"""Private immutable-image generation/inspection containers, exact owned cleanup."""

import json
import os
import socket
import subprocess
import time
import uuid

from tests.meet_egress_containers import EgressContainers, docker

PROFILES = {
    "generation": (8098, "PERSONA_GENERATION", "persona_generation_server", "256m", "32m"),
    "image": (8095, "PERSONA_IMAGE", "persona_server", "256m", "32m"),
    "video": (8096, "PERSONA_VIDEO", "persona_video_server", "384m", "64m"),
}


class PersonaGenerationContainers:
    def __init__(self, image):
        self.resources = EgressContainers(image)

    def __enter__(self):
        self.resources.__enter__()
        return self

    def __exit__(self, *args):
        return self.resources.__exit__(*args)

    @property
    def gateway(self):
        row = json.loads(docker("network", "inspect", self.resources.network))[0]
        assert row["Internal"] and row["Id"] == self.resources.network
        return row["IPAM"]["Config"][0]["Gateway"]

    def _remove_name(self, name):
        result = subprocess.run(["docker", "inspect", name], capture_output=True, text=True, timeout=15)
        if result.returncode:
            assert "No such" in result.stderr, "owned test container cleanup lookup failed"
            return
        row = json.loads(result.stdout)[0]
        assert row["Name"] == "/" + name
        self.resources.remove(row["Id"])  # Rechecks exact ID, image and ownership label.

    def start(self, kind, directory, callback, key_bytes):
        port, prefix, module, memory, temporary = PROFILES[kind]
        directory.mkdir(mode=0o700)
        key, state = directory / "worker-key", directory / "state"
        key.write_bytes(key_bytes)
        key.chmod(0o600)
        state.mkdir(mode=0o700)
        name = "persona-generation-test-" + uuid.uuid4().hex
        # Register ownership-checked lookup first, including uncertain create outcomes.
        self.resources.cleanup.callback(self._remove_name, name)
        identifier = docker(
            "create",
            "--pull",
            "never",
            "--name",
            name,
            "--label",
            "ananta.test-owner=" + self.resources.owner,
            "--network",
            self.resources.network,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--read-only",
            "--init",
            "--memory",
            memory,
            "--cpus",
            "1",
            "--pids-limit",
            "32",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            f"/tmp:size={temporary},mode=1777",
            "--mount",
            f"type=bind,src={key},dst=/run/secrets/worker-key,readonly",
            "--mount",
            f"type=bind,src={state},dst=/state",
            "--env",
            f"{prefix}_WORKER_KEY_FILE=/run/secrets/worker-key",
            "--env",
            f"{prefix}_HUB_LEASE_URL={callback}",
            self.resources.image,
            "python",
            "-m",
            f"worker.meet_media.{module}",
        )
        row = self.resources.inspect(identifier)
        config = row["HostConfig"]
        assert row["Config"]["User"] == f"{os.getuid()}:{os.getgid()}" and os.getuid() != 0
        assert config["ReadonlyRootfs"] and config["CapDrop"] == ["ALL"]
        assert config["NanoCpus"] == 1_000_000_000 and config["PidsLimit"] == 32
        assert config["Memory"] == int(memory[:-1]) * 1024 * 1024
        assert not config["PortBindings"] and not config["DeviceRequests"] and not config["Devices"]
        assert {mount["Destination"] for mount in row["Mounts"] if mount["Type"] == "bind"} == {
            "/run/secrets/worker-key",
            "/state",
        }
        docker("start", identifier)
        address = self.resources.address(identifier)
        deadline = time.monotonic() + 5
        while True:
            assert self.resources.inspect(identifier)["State"]["Running"], "private Worker exited before readiness"
            try:
                with socket.create_connection((address, port), timeout=0.2):
                    break
            except OSError:
                assert time.monotonic() < deadline, "private Worker readiness expired"
                time.sleep(0.05)
        return identifier, address, port
