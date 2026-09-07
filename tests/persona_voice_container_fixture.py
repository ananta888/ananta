"""Disposable private descriptor worker; exact owned resources only, no serving mutation."""

import json
import os
import socket
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager


def docker(*args, timeout=15):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Disposable voice Docker operation failed: {result.stderr[-1000:]}")
    return result.stdout.strip()


@contextmanager
def private_voice_network():
    name = "persona-voice-test-" + uuid.uuid4().hex
    docker("network", "create", "--internal", name)
    try:
        info = json.loads(docker("network", "inspect", name))[0]
        assert info["Internal"] is True
        yield name, info["IPAM"]["Config"][0]["Gateway"]
    finally:
        docker("network", "rm", name)


@contextmanager
def descriptor_worker(tmp_path, network, callback, key_bytes):
    name = "persona-voice-test-" + uuid.uuid4().hex
    # Resolve the explicitly built local test tag once, then create by immutable ID.
    image = docker("image", "inspect", "ananta-persona-voice:local", "--format", "{{.Id}}")
    key = tmp_path / "voice-test-key"
    key.write_bytes(key_bytes)
    key.chmod(0o600)
    state = tmp_path / "voice-worker-state"
    state.mkdir(mode=0o700)
    created = False
    try:
        docker(
            "create",
            "--pull",
            "never",
            "--name",
            name,
            "--network",
            network,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--read-only",
            "--init",
            "--memory",
            "128m",
            "--cpus",
            "0.5",
            "--pids-limit",
            "32",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp:size=16m,mode=1777",
            "--mount",
            f"type=bind,source={key},target=/run/secrets/persona-voice-key,readonly",
            "--mount",
            f"type=bind,source={state},target=/state",
            "-e",
            "PERSONA_VOICE_WORKER_KEY_FILE=/run/secrets/persona-voice-key",
            "-e",
            f"PERSONA_VOICE_HUB_LEASE_URL={callback}",
            image,
        )
        created = True
        docker("start", name, timeout=45)
        info = json.loads(docker("inspect", name))[0]
        assert not info["HostConfig"]["PortBindings"] and not info["HostConfig"]["DeviceRequests"]
        assert info["HostConfig"]["ReadonlyRootfs"] and info["HostConfig"]["CapDrop"] == ["ALL"]
        address = info["NetworkSettings"]["Networks"][network]["IPAddress"]
        deadline = time.monotonic() + 5
        while True:
            try:
                with socket.create_connection((address, 8097), timeout=0.25):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Disposable voice worker readiness exceeded five seconds") from None
                threading.Event().wait(0.05)
        yield address, image
    finally:
        if created:
            docker("rm", "--force", name, timeout=30)
