"""Test-owned full packaged dialog Worker, separate process/container per role."""

import ipaddress
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from tests.meet_dialog_browser_fixture import docker


class DialogWorkerContainer:
    def __init__(self, network, image, hub_url, *, lifetime=180, command=docker):
        if not isinstance(network, str) or not re.fullmatch(r"meet-test-tls-[a-f0-9-]{36}-network", network):
            raise ValueError("test_worker_network_invalid")
        if not isinstance(image, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
            raise ValueError("test_worker_image_invalid")
        hub = urlsplit(hub_url)
        if (
            hub.scheme != "http"
            or not hub.hostname
            or hub.port is None
            or hub.path != "/api/meet/v1/internal/dialog"
            or (hub.username or hub.password or hub.query or hub.fragment)
            or not ipaddress.IPv4Address(hub.hostname).is_private
        ):
            raise ValueError("test_worker_hub_invalid")
        if type(lifetime) is not int or not 180 <= lifetime <= 600:
            raise ValueError("test_worker_lifetime_invalid")
        self.network, self.image, self.hub_url, self.lifetime, self.command = network, image, hub_url, lifetime, command
        self.name = "meet-test-dialog-worker-" + str(uuid4())
        self.created, self.origin = False, None

    def start(self, key, certificate, spki):
        if self.created or not isinstance(spki, str) or not re.fullmatch(r"[A-Za-z0-9+/]{43}=", spki):
            raise ValueError("test_worker_start_invalid")
        info = json.loads(self.command("network", "inspect", self.network))[0]
        if (
            info.get("Internal") is not True
            or len(info["IPAM"]["Config"]) != 1
            or (info["IPAM"]["Config"][0]["Gateway"] != urlsplit(self.hub_url).hostname)
        ):
            raise ValueError("test_worker_network_invalid")
        if self.command("image", "inspect", self.image, "--format", "{{.Id}}") != self.image:
            raise ValueError("test_worker_image_mismatch")
        root = Path(__file__).resolve().parents[1]
        mounts = [
            (root / "tests/meet_worker_container_sitecustomize.py", "/test/sitecustomize.py"),
            (Path(key), "/test/worker-key"),
            (Path(certificate), "/test/meet-ca.pem"),
        ]
        for path, _ in mounts:
            if path.is_symlink() or not path.is_file():
                raise ValueError("test_worker_mount_invalid")
        self.created = True  # Covers an uncertain create result in cleanup.
        self.command(
            "create",
            "--name",
            self.name,
            "--network",
            self.network,
            "--user=1000:1000",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--security-opt=seccomp=" + str(root / "docker/meet-media/chromium-seccomp.json"),
            "--tmpfs=/tmp:size=256m,mode=1777",
            "--tmpfs=/state:size=32m,uid=1000,gid=1000,mode=0700",
            "--shm-size=256m",
            "--memory=1g",
            "--pids-limit=256",
            "--cpus=2",
            "--init",
            *(
                argument
                for source, target in mounts
                for argument in ("--mount", f"type=bind,src={source},dst={target},readonly")
            ),
            "--env=PYTHONPATH=/test:/app",
            "--env=MEET_DIALOG_ENABLED=1",
            "--env=MEET_WORKER_KEY_FILE=/test/worker-key",
            "--env=SSL_CERT_FILE=/test/meet-ca.pem",
            "--env=MEET_HUB_DIALOG_URL=" + self.hub_url,
            "--env=MEET_TEST_BROWSER_SPKI=" + spki,
            "--entrypoint=timeout",
            self.image,
            str(self.lifetime),
            "python",
            "-m",
            "worker.meet_media.server",
        )
        self.command("start", self.name)
        address = self.command(
            "inspect",
            self.name,
            "--format",
            "{{(index .NetworkSettings.Networks " + json.dumps(self.network) + ").IPAddress}}",
        )
        if ipaddress.IPv4Address(address) not in ipaddress.IPv4Network(info["IPAM"]["Config"][0]["Subnet"]):
            raise ValueError("test_worker_address_invalid")
        self.origin = f"http://{address}:8094"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = json.loads(self.command("inspect", self.name, "--format", "{{json .State}}"))
            if state.get("Running") is not True:
                raise ValueError("test_worker_stopped_before_health")
            if state.get("Health", {}).get("Status") == "healthy":
                return
            time.sleep(0.1)
        raise ValueError("test_worker_start_timeout")

    def close(self):
        if self.created:
            self.created = False
            self.command("rm", "--force", self.name)

    def chat_state(self):
        raw = self.command(
            "exec",
            self.name,
            "python",
            "-S",
            "-c",
            "from pathlib import Path; p=Path('/state/dialog-chat-ready.json'); "
            "print(p.open().read(257) if p.is_file() and not p.is_symlink() and p.stat().st_size <= 256 else '{}')",
        )
        if len(raw) > 256:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None  # A concurrent test-marker write is not a readiness signal.
        if (
            not isinstance(value, dict)
            or set(value) != {"open", "control_revision", "receive_revision"}
            or type(value["open"]) is not bool
            or any(
                type(value[k]) is not int or not 0 <= value[k] < 2**53 for k in ("control_revision", "receive_revision")
            )
        ):
            return None
        return value
