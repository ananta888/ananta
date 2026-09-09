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
    def __init__(
        self,
        network,
        image,
        hub_url,
        *,
        lifetime=180,
        diagnostics=False,
        control_diagnostics=False,
        browser_documents=False,
        gpu=False,
        network_namespace=None,
        relay_url=None,
        command=docker,
    ):
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
        if type(diagnostics) is not bool:
            raise ValueError("test_worker_diagnostics_invalid")
        if type(control_diagnostics) is not bool:
            raise ValueError("test_worker_control_diagnostics_invalid")
        if type(browser_documents) is not bool:
            raise ValueError("test_worker_browser_documents_invalid")
        if type(gpu) is not bool:
            raise ValueError("test_worker_gpu_invalid")
        if network_namespace is not None and (
            not isinstance(network_namespace, str) or not re.fullmatch(r"[a-f0-9]{64}", network_namespace)
        ):
            raise ValueError("test_worker_namespace_invalid")
        if relay_url is not None and (
            network_namespace is None
            or not isinstance(relay_url, str)
            or not re.fullmatch(r"turn:[0-9.]+:3478\?transport=(udp|tcp)", relay_url)
        ):
            raise ValueError("test_worker_relay_invalid")
        self.network, self.image, self.hub_url, self.lifetime, self.command = network, image, hub_url, lifetime, command
        self.diagnostics = diagnostics
        self.control_diagnostics = control_diagnostics
        self.browser_documents = browser_documents
        self.gpu = gpu
        self.network_namespace, self.relay_url = network_namespace, relay_url
        self.name = "meet-test-dialog-worker-" + str(uuid4())
        self.created, self.origin = False, None

    def start(self, key, certificate, spki, *, hub_identity=None):
        if self.created or not isinstance(spki, str) or not re.fullmatch(r"[A-Za-z0-9+/]{43}=", spki):
            raise ValueError("test_worker_start_invalid")
        info = json.loads(self.command("network", "inspect", self.network))[0]
        if info.get("Internal") is not True or len(info["IPAM"]["Config"]) != 1:
            raise ValueError("test_worker_network_invalid")
        if hub_identity is None:
            if info["IPAM"]["Config"][0]["Gateway"] != urlsplit(self.hub_url).hostname:
                raise ValueError("test_worker_network_invalid")
        else:
            from tests.meet_private_hub_endpoint import require_private_hub_endpoint

            require_private_hub_endpoint(self.command, self.network, self.hub_url, hub_identity)
        if self.command("image", "inspect", self.image, "--format", "{{.Id}}") != self.image:
            raise ValueError("test_worker_image_mismatch")
        root = Path(__file__).resolve().parents[1]
        namespace = None
        if self.network_namespace is not None:
            from tests.meet_dialog_egress_guard import namespace_info

            namespace = namespace_info(self.command, self.network_namespace, self.network)
        mounts = [
            (root / "tests/meet_worker_container_sitecustomize.py", "/test/sitecustomize.py"),
            (Path(key), "/test/worker-key"),
            (Path(certificate), "/test/meet-ca.pem"),
        ]
        if self.control_diagnostics:
            mounts += [
                (root / ("tests/" + name), "/test/" + name)
                for name in ("meet_dialog_control_observer.py", "meet_dialog_rpc_observer.py")
            ]
        if self.relay_url is not None:
            mounts += [
                (root / "tests/meet_worker_relay_context.py", "/test/meet_worker_relay_context.py"),
                (root.parent / "webrtc-minimize-server/test/helpers/machine-forced-relay.js", "/test/forced-relay.js"),
            ]
        for path, _ in mounts:
            if path.is_symlink() or not path.is_file():
                raise ValueError("test_worker_mount_invalid")
        gpu_arguments = []
        if self.gpu:
            from tests.meet_dialog_gpu_resources import receive_gpu_arguments

            gpu_arguments = receive_gpu_arguments(self.command)
        self.created = True  # Covers an uncertain create result in cleanup.
        self.command(
            "create",
            "--name",
            self.name,
            "--network",
            "container:" + self.network_namespace if namespace is not None else self.network,
            "--user=1000:1000",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--security-opt=seccomp=" + str(root / "docker/meet-media/chromium-seccomp.json"),
            "--tmpfs=/tmp:size=256m,mode=1777",
            "--tmpfs=/state:size=32m,uid=1000,gid=1000,mode=0700",
            "--shm-size=256m",
            "--memory=4g" if self.gpu else "--memory=1g",
            "--pids-limit=256",
            "--cpus=2",
            "--init",
            *gpu_arguments,
            *(
                argument
                for source, target in mounts
                for argument in ("--mount", f"type=bind,src={source},dst={target},readonly")
            ),
            "--env=PYTHONPATH=/test:/app",
            "--env=MEET_DIALOG_ENABLED=1",
            "--env=MEET_DIALOG_DIAGNOSTICS_ENABLED=" + ("1" if self.diagnostics else "0"),
            "--env=MEET_TEST_CONTROL_DIAGNOSTICS=" + ("1" if self.control_diagnostics else "0"),
            "--env=MEET_TEST_PUBLIC_DOCUMENT=" + ("1" if self.browser_documents else "0"),
            "--env=MEET_TEST_FORCE_RELAY_URL=" + (self.relay_url or ""),
            "--env=MEET_WORKER_KEY_FILE=/test/worker-key",
            "--env=SSL_CERT_FILE=/test/meet-ca.pem",
            "--env=NODE_EXTRA_CA_CERTS=/test/meet-ca.pem",
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
        address = (
            namespace["NetworkSettings"]["Networks"][self.network]["IPAddress"]
            if namespace is not None
            else self.command(
                "inspect",
                self.name,
                "--format",
                "{{(index .NetworkSettings.Networks " + json.dumps(self.network) + ").IPAddress}}",
            )
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

    def browser_state(self):
        raw = self.command(
            "exec",
            self.name,
            "python",
            "-S",
            "-c",
            "from pathlib import Path; p=Path('/state/dialog-browser-ready.json'); "
            "print(p.open().read(257) if p.is_file() and not p.is_symlink() and p.stat().st_size <= 256 else '{}')",
        )
        if len(raw) > 256:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        if (
            type(value) is not dict
            or set(value) != {"revision", "mode", "loaded", "failed"}
            or type(value["revision"]) is not int
            or not 1 <= value["revision"] <= 1023
            or value["mode"] not in ("off", "browser", "status")
            or any(type(value[name]) is not bool for name in ("loaded", "failed"))
        ):
            return None
        return value
