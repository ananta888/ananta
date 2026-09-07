"""Owned internal GPU provider/worker; no serving-service state or credentials."""

import hmac
import http.client
import ipaddress
import json
import re
import secrets
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from tests.meet_gpu_source_fixture import ROOT, docker, driver_bindings
from worker.meet_media.contract import signature

OLLAMA_IMAGE = "sha256:0ab10b9b9dc5f50d30dc61aec25e3316822ca22cf0f27d4e98d74cc7dedd7c80"
MODEL_DIGEST = "65ec06548149b04c096a120e4a6da9d4017ea809c91734ea5631e89f96ddc57b"
MODEL_VOLUME = "ananta-meet-media_meet-models"
NAME = re.compile(r"meet-test-inference-[a-f0-9-]{36}-(?:worker|ollama|network)")


def inference_command(name, network, image, drivers, lifetime):
    if (
        not NAME.fullmatch(name)
        or not NAME.fullmatch(network)
        or not network.endswith("-network")
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", image)
        or type(lifetime) is not int
        or not 180 <= lifetime <= 600
    ):
        raise ValueError("test_inference_configuration_invalid")
    if name not in {network.removesuffix("-network") + suffix for suffix in ("-worker", "-ollama")}:
        raise ValueError("test_inference_configuration_invalid")
    projected = []
    for binding in drivers:
        if not isinstance(binding, str):
            raise ValueError("test_inference_driver_invalid")
        fields = binding.split(",")
        if len(fields) != 4 or fields[0] != "type=bind" or fields[3] != "readonly":
            raise ValueError("test_inference_driver_invalid")
        if not fields[1].startswith("src=") or not fields[2].startswith("dst="):
            raise ValueError("test_inference_driver_invalid")
        projected.append({"Type": "bind", "RW": False, "Source": fields[1][4:], "Destination": fields[2][4:]})
    if driver_bindings(projected) != drivers:
        raise ValueError("test_inference_driver_invalid")
    args = [
        "create",
        "--name",
        name,
        "--network",
        network,
        "--pull=never",
        "--read-only",
        "--user=1000:1000",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--memory=4g",
        "--pids-limit=256",
        "--tmpfs=/tmp:rw,size=256m,mode=1777",
        "--entrypoint=timeout",
    ]
    for device in ("nvidia0", "nvidiactl", "nvidia-uvm", "nvidia-uvm-tools"):
        args += ["--device", "/dev/" + device]
    for binding in drivers:
        args += ["--mount", binding]
    return args + ["--env=LD_LIBRARY_PATH=/host-nvidia"], [image, "--signal=TERM", "--kill-after=5", str(lifetime)]


def provider_command(name, network, drivers, lifetime):
    args, process = inference_command(name, network, OLLAMA_IMAGE, drivers, lifetime)
    return (
        args
        + [
            "--network-alias=meet-test-ollama",
        "--tmpfs=/home/ubuntu/.ollama:rw,size=4m,mode=1777",
            "--mount",
            f"type=volume,src={MODEL_VOLUME},dst=/models,volume-subpath=models,readonly",
            "--env=OLLAMA_MODELS=/models",
            "--env=OLLAMA_HOST=0.0.0.0:11434",
            "--env=OLLAMA_NO_CLOUD=true",
            "--env=OLLAMA_MAX_LOADED_MODELS=1",
            "--env=OLLAMA_NUM_PARALLEL=1",
            "--env=OLLAMA_CONTEXT_LENGTH=2048",
        ]
        + process
        + ["/bin/ollama", "serve"]
    )


def worker_command(name, network, image, drivers, lifetime, key_path, root=ROOT):
    args, process = inference_command(name, network, image, drivers, lifetime)
    for source, target in (
        (root / "worker/meet_media", "/app/worker/meet_media"),
        (root / "ananta_contracts", "/app/ananta_contracts"),
        (root / "data/meet-media/models", "/models"),
        (key_path, "/run/secrets/test-key"),
    ):
        args += ["--mount", f"type=bind,src={source},dst={target},readonly"]
    return (
        args
        + [
            "--tmpfs=/state:rw,size=16m,mode=1777",
            "--env=MEET_WORKER_KEY_FILE=/run/secrets/test-key",
            "--env=MEET_OLLAMA_URL=http://meet-test-ollama:11434",
            "--env=MEET_LLM_MODEL=qwen2.5:1.5b",
            f"--env=MEET_LLM_DIGEST={MODEL_DIGEST}",
        ]
        + process
        + ["python", "-m", "worker.meet_media.server"]
    )


class DialogGpuFixture:
    def __init__(self, lifetime=240, *, command=docker):
        if type(lifetime) is not int or not 180 <= lifetime <= 600:
            raise ValueError("test_inference_lifetime_invalid")
        base = "meet-test-inference-" + str(uuid4())
        self.network, self.provider, self.worker = (base + suffix for suffix in ("-network", "-ollama", "-worker"))
        self.command, self.lifetime = command, lifetime
        self.resources = []
        self.temporary = None
        self.endpoint = None
        self.key = secrets.token_hex(32).encode("ascii")

    def _address(self, name, port):
        value = json.loads(self.command("inspect", name, "--format", "{{json .NetworkSettings.Networks}}"))
        if set(value) != {self.network}:
            raise ValueError("test_inference_network_changed")
        if not value[self.network]["IPAddress"]:
            raise ValueError("test_inference_container_not_running")
        address = ipaddress.IPv4Address(value[self.network]["IPAddress"])
        if address not in self.subnet or not address.is_private or address.is_loopback:
            raise ValueError("test_inference_address_invalid")
        return str(address), port

    def _ready(self, address, *, provider):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            connection = http.client.HTTPConnection(*address, timeout=1)
            try:
                if provider:
                    connection.request("GET", "/api/tags")
                else:
                    connection.request(
                        "POST",
                        "/v1/turns",
                        b"{}",
                        {
                            "Content-Type": "application/json",
                            "X-Ananta-Task-Signature": signature(self.key, b"{}"),
                        },
                    )
                response = connection.getresponse()
                body = response.read(65537)
                if len(body) > 65536:
                    raise ValueError("test_inference_health_oversize")
                value = json.loads(body)
                if provider and response.status == 200:
                    if not any(
                        model.get("name") == "qwen2.5:1.5b" and model.get("digest") == MODEL_DIGEST
                        for model in value.get("models", [])
                    ):
                        raise ValueError("test_inference_pinned_model_missing")
                    return
                if not provider and response.status == 409:
                    if value != {"error": {"code": "meet_turn_contract_invalid"}} or not hmac.compare_digest(
                        signature(self.key, b"result-v1\0" + body), response.getheader("X-Ananta-Result-Signature", "")
                    ):
                        raise ValueError("test_inference_worker_probe_invalid")
                    return
            except (OSError, http.client.HTTPException):
                pass
            finally:
                connection.close()
            time.sleep(0.1)
        raise ValueError("test_inference_start_timeout")

    def start(self):
        if self.resources or self.temporary is not None:
            raise ValueError("test_inference_already_started")
        try:
            service = "ananta-meet-media-meet-media-worker-1"
            image = self.command("inspect", service, "--format", "{{.Image}}")
            drivers = driver_bindings(json.loads(self.command("inspect", service, "--format", "{{json .Mounts}}")))
            if self.command("image", "inspect", OLLAMA_IMAGE, "--format", "{{.Id}}") != OLLAMA_IMAGE:
                raise ValueError("test_inference_provider_image_missing")
            if self.command("volume", "inspect", MODEL_VOLUME, "--format", "{{.Name}}") != MODEL_VOLUME:
                raise ValueError("test_inference_model_volume_missing")
            self.temporary = tempfile.TemporaryDirectory(prefix="meet-test-inference-key-")
            key_path = Path(self.temporary.name) / "key"
            key_path.write_bytes(self.key)
            key_path.chmod(0o600)
            self.resources.append(("network", self.network))
            self.command("network", "create", "--internal", self.network)
            info = json.loads(self.command("network", "inspect", self.network))[0]
            if info.get("Internal") is not True or len(info["IPAM"]["Config"]) != 1:
                raise ValueError("test_inference_network_invalid")
            self.subnet = ipaddress.IPv4Network(info["IPAM"]["Config"][0]["Subnet"])
            for name, args in (
                (self.provider, provider_command(self.provider, self.network, drivers, self.lifetime)),
                (self.worker, worker_command(self.worker, self.network, image, drivers, self.lifetime, key_path)),
            ):
                self.resources.append(("container", name))
                self.command(*args)
                self.command("start", name)
                address = self._address(name, 11434 if name == self.provider else 8094)
                self._ready(address, provider=name == self.provider)
            self.endpoint = f"http://{address[0]}:{address[1]}/v1/turns"
            return self
        except Exception:
            self.close()
            raise

    def close(self):
        failed = []
        for kind, name in reversed(self.resources):
            try:
                if not NAME.fullmatch(name):
                    raise ValueError("test_inference_cleanup_scope_invalid")
                self.command(*(("network", "rm", name) if kind == "network" else ("rm", "--force", name)))
            except Exception:
                failed.append((kind, name))
        self.resources = list(reversed(failed))
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None
        if failed:
            raise ValueError("test_inference_cleanup_failed")

    def __enter__(self):
        return self.start()

    def __exit__(self, *_args):
        self.close()
