"""An owned GPU Ollama provider on the existing private acceptance network."""

import http.client
import ipaddress
import json
import re
import time
from uuid import uuid4

from tests.meet_gpu_capacity import require_gpu_capacity
from tests.meet_gpu_source_fixture import docker
from tests.meet_gpu_wsl_fixture import driver_bindings, validate_driver_store
from tests.meet_ollama_model_fixture import validate_model
from tests.meet_ollama_model_spec import MODEL_DIGEST, OLLAMA_IMAGE


def provider_command(name, network, models, store):
    if not re.fullmatch(r"meet-test-ollama-[a-f0-9-]{36}", name) or not re.fullmatch(
        r"meet-test-(?:tls|inference)-[a-f0-9-]{36}-network", network
    ):
        raise ValueError("test_ollama_resource_scope_invalid")
    models = validate_model(models)
    library_path = "/usr/lib/wsl/lib:" + str(validate_driver_store(store))
    bindings = driver_bindings(store)
    args = ["create", "--name", name, "--network", network, "--pull=never", "--read-only",
            "--user=1000:1000", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--memory=4g", "--pids-limit=256", "--tmpfs=/tmp:rw,size=256m,mode=1777",
            "--tmpfs=/home/ubuntu/.ollama:rw,size=4m,mode=1777", "--device=/dev/dxg",
            "--mount", f"type=bind,src={models},dst=/models,readonly",
            "--env=LD_LIBRARY_PATH=" + library_path, "--env=OLLAMA_MODELS=/models",
            "--env=OLLAMA_HOST=0.0.0.0:11434", "--env=OLLAMA_NO_CLOUD=true",
            "--env=OLLAMA_MAX_LOADED_MODELS=1", "--env=OLLAMA_NUM_PARALLEL=1",
            "--env=OLLAMA_CONTEXT_LENGTH=2048", "--entrypoint=timeout"]
    for binding in bindings:
        args.extend(["--mount", binding])
    return args + [OLLAMA_IMAGE, "--signal=TERM", "--kill-after=5", "360", "/bin/ollama", "serve"]


class CompanionOllamaFixture:
    def __init__(self, network, models, store, *, command=docker, check_capacity=require_gpu_capacity):
        self.name = "meet-test-ollama-" + str(uuid4())
        self.network, self.command = network, command
        self.arguments = provider_command(self.name, network, models, store)
        self.check_capacity = check_capacity
        self.created, self.address = False, None

    def request(self, path, payload=None, *, timeout=45):
        connection = http.client.HTTPConnection(*self.address, timeout=timeout)
        try:
            body = None if payload is None else json.dumps(payload).encode()
            connection.request("GET" if body is None else "POST", path, body,
                               {"Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read(65_537)
            if response.status != 200 or len(raw) > 65_536:
                raise ValueError("test_ollama_response_invalid")
            return json.loads(raw)
        finally:
            connection.close()

    def start(self):
        if self.created:
            raise ValueError("test_ollama_already_started")
        self.check_capacity()
        if self.command("image", "inspect", OLLAMA_IMAGE, "--format", "{{.Id}}") != OLLAMA_IMAGE:
            raise ValueError("test_ollama_image_pin_mismatch")
        info = json.loads(self.command("network", "inspect", self.network))[0]
        if info.get("Internal") is not True or len(info["IPAM"]["Config"]) != 1:
            raise ValueError("test_ollama_network_invalid")
        subnet = ipaddress.IPv4Network(info["IPAM"]["Config"][0]["Subnet"])
        try:
            self.created = True
            self.command(*self.arguments)
            self.command("start", self.name)
            networks = json.loads(self.command("inspect", self.name, "--format",
                                                "{{json .NetworkSettings.Networks}}"))
            if set(networks) != {self.network}:
                raise ValueError("test_ollama_network_changed")
            address = ipaddress.IPv4Address(networks[self.network]["IPAddress"])
            if address not in subnet or not address.is_private or address.is_loopback:
                raise ValueError("test_ollama_address_invalid")
            self.address = str(address), 11434
            deadline = time.monotonic() + 25
            while True:
                try:
                    models = self.request("/api/tags", timeout=1).get("models", [])
                    if not any(model.get("name") == "qwen2.5:1.5b" and model.get("digest") == MODEL_DIGEST
                               for model in models):
                        raise ValueError("test_ollama_model_pin_mismatch")
                    return self
                except (OSError, http.client.HTTPException):
                    if time.monotonic() >= deadline:
                        raise ValueError("test_ollama_start_timeout") from None
                    time.sleep(.1)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.created:
            self.command("rm", "--force", self.name)
            self.created = False

    def __enter__(self):
        return self.start()

    def __exit__(self, *_args):
        self.close()
