"""Own exactly one private persistent Hub container; never a serving instance."""

import http.client
import ipaddress
import json
import re
import secrets
import time
from pathlib import Path
from uuid import uuid4

from tests.meet_dialog_browser_fixture import docker
from tests.meet_restart_hub_runtime import validate_config


class RestartHubContainer:
    def __init__(self, wire, image, directory, *, command=docker):
        if not isinstance(image, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
            raise ValueError("test_hub_image_invalid")
        self.network, self.gateway = wire.ready["test_network"], wire.gateway
        if (
            not re.fullmatch(r"meet-test-tls-[a-f0-9-]{36}-network", self.network)
            or not ipaddress.IPv4Address(self.gateway).is_private
        ):
            raise ValueError("test_hub_network_invalid")
        self.image, self.wire, self.command = image, wire, command
        self.name = "meet-test-restart-hub-" + str(uuid4())
        self.created = False
        self.identifier = None
        self.directory = Path(directory) / "restart-hub"
        self.directory.mkdir(mode=0o700)
        self.state = self.directory / "state"
        self.state.mkdir(mode=0o700)
        self.control_key = secrets.token_bytes(32)
        self.config_file = self.directory / "config.json"
        self.address = self.origin = None
        self.port = 8099

    def prepare(self):
        if self.created:
            raise ValueError("test_hub_already_created")
        network = json.loads(self.command("network", "inspect", self.network))[0]
        if (
            network.get("Internal") is not True
            or len(network["IPAM"]["Config"]) != 1
            or network["IPAM"]["Config"][0]["Gateway"] != self.gateway
        ):
            raise ValueError("test_hub_network_invalid")
        if self.command("image", "inspect", self.image, "--format", "{{.Id}}") != self.image:
            raise ValueError("test_hub_image_mismatch")
        subnet = ipaddress.IPv4Network(network["IPAM"]["Config"][0]["Subnet"])
        address = subnet[-2]
        if (
            not address.is_private
            or str(address) == self.gateway
            or any(
                row.get("IPv4Address", "").split("/")[0] == str(address)
                for row in network.get("Containers", {}).values()
            )
        ):
            raise ValueError("test_hub_address_invalid")
        self.address = str(address)
        config_file, control_file = self.config_file, self.directory / "control-key"
        # The stopped container has no valid execution configuration yet.
        config_file.write_text("{}", encoding="utf-8")
        config_file.chmod(0o600)
        control_file.write_bytes(self.control_key)
        control_file.chmod(0o600)
        mounts = [
            (config_file, "/test/hub-config.json"),
            (control_file, "/test/control-key"),
            (self.wire.private, "/test/hub.pem"),
            (self.wire.worker_key, "/test/worker-key"),
            (Path(self.wire.ready["certificate"]), "/test/meet-ca.pem"),
        ]
        if any(path.is_symlink() or not path.is_file() for path, _ in mounts):
            raise ValueError("test_hub_mount_invalid")
        self.created = True
        self.command(
            "create",
            "--name",
            self.name,
            "--network",
            self.network,
            "--ip=" + self.address,
            "--user=1000:1000",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--tmpfs=/tmp:size=256m,mode=1777",
            "--memory=2g",
            "--pids-limit=256",
            "--cpus=2",
            "--init",
            "--mount",
            f"type=bind,src={self.state},dst=/state",
            *(
                argument
                for source, target in mounts
                for argument in ("--mount", f"type=bind,src={source},dst={target},readonly")
            ),
            "--env=SSL_CERT_FILE=/test/meet-ca.pem",
            "--env=AGENT_TOKEN=synthetic-restart-hub-token-not-production",
            "--env=SECRET_KEY=synthetic-restart-hub-session-secret-not-production",
            "--env=INITIAL_ADMIN_USER=synthetic-admin",
            "--env=INITIAL_ADMIN_PASSWORD=synthetic-test-password",
            self.image,
        )
        self.identifier = self.command("inspect", self.name, "--format", "{{.Id}}")
        state = self.state_snapshot()
        if state.get("Running") is not False:
            raise ValueError("test_hub_prepare_transition_invalid")
        info = json.loads(self.command("inspect", self.identifier))[0]
        configured = info["NetworkSettings"]["Networks"][self.network].get("IPAMConfig", {})
        if configured.get("IPv4Address") != self.address:
            raise ValueError("test_hub_address_invalid")
        self.origin = f"http://{self.address}:{self.port}"

    def start(self, worker_origin):
        if not self.created or self.state_snapshot().get("Running") is not False:
            raise ValueError("test_hub_start_transition_invalid")
        config = validate_config(
            {
                "meeting_origin": self.wire.ready["origin"],
                "room_id": self.wire.ready["room_id"],
                "worker_origin": worker_origin,
            }
        )
        self.config_file.write_text(json.dumps(config), encoding="utf-8")
        self.command("start", self.identifier)
        info = json.loads(self.command("inspect", self.identifier))[0]
        if info["NetworkSettings"]["Networks"][self.network].get("IPAddress") != self.address:
            raise ValueError("test_hub_address_mismatch")
        self.wait_ready()

    def state_snapshot(self):
        rows = json.loads(self.command("inspect", self.name))
        if (
            len(rows) != 1
            or rows[0].get("Id") != self.identifier
            or rows[0].get("Image") != self.image
            or rows[0].get("Name") != "/" + self.name
            or set(rows[0]["NetworkSettings"]["Networks"]) != {self.network}
        ):
            raise ValueError("test_hub_owned_container_mismatch")
        return rows[0]["State"]

    def request(self, method, path):
        if not re.fullmatch(r"/__test/(health|start|(?:status|enable)/[a-f0-9-]{36})", path):
            raise ValueError("test_hub_operation_invalid")
        connection = http.client.HTTPConnection(self.address, self.port, timeout=2)
        try:
            connection.request(method, path, headers={"X-Test-Hub-Control": self.control_key.hex()})
            response = connection.getresponse()
            body = response.read(4097)
            if response.status != 200 or len(body) > 4096:
                raise ValueError(f"test_hub_control_status_{response.status}")
            return json.loads(body)
        finally:
            connection.close()

    def wait_ready(self):
        until = time.monotonic() + 60
        last_failure = "unobserved"
        while time.monotonic() < until:
            state = self.state_snapshot()
            if state.get("Running") is not True:
                kind = "unknown"
                for line in self.command("logs", "--tail=8", self.name).splitlines():
                    if len(line) > 512:
                        continue
                    try:
                        value = json.loads(line)
                    except ValueError:
                        continue
                    if (
                        isinstance(value, dict)
                        and set(value) in ({"error", "type"}, {"error", "type", "frames"})
                        and value["error"] == "test_hub_runtime_failed"
                        and isinstance(value["type"], str)
                        and re.fullmatch(r"[A-Za-z]{1,64}", value["type"])
                    ):
                        kind = value["type"]
                        frames = value.get("frames", [])
                        if isinstance(frames, list) and len(frames) <= 4:
                            for frame in frames:
                                if (
                                    isinstance(frame, dict)
                                    and set(frame) == {"file", "line"}
                                    and isinstance(frame["file"], str)
                                    and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}\.py", frame["file"])
                                    and type(frame["line"]) is int
                                    and 1 <= frame["line"] < 100000
                                ):
                                    kind += ":" + frame["file"] + ":" + str(frame["line"])
                raise ValueError("test_hub_stopped_before_readiness:" + kind)
            try:
                if self.request("GET", "/__test/health") == {"ready": True}:
                    return
            except (OSError, ValueError, http.client.HTTPException) as error:
                last_failure = type(error).__name__
                if isinstance(error, ValueError) and re.fullmatch(r"test_hub_control_status_[0-9]{3}", str(error)):
                    last_failure = str(error)
            time.sleep(0.1)
        raise ValueError("test_hub_readiness_timeout:" + last_failure)

    def kill(self):
        state = self.state_snapshot()
        if state.get("Running") is not True or not re.fullmatch(r"[a-f0-9]{64}", self.identifier or ""):
            raise ValueError("test_hub_crash_target_invalid")
        self.command("kill", "--signal=KILL", self.identifier)
        after = self.state_snapshot()
        assert after["Running"] is False and after["ExitCode"] == 137
        return state["Pid"]

    def restart(self, previous_pid):
        if self.state_snapshot().get("Running") is not False:
            raise ValueError("test_hub_restart_transition_invalid")
        self.command("start", self.identifier)
        self.wait_ready()
        assert self.state_snapshot()["Pid"] != previous_pid

    def close(self):
        if self.created:
            self.created = False
            self.command("rm", "--force", self.name)
