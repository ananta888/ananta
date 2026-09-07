"""Deterministic inference fixture boundaries; no real Docker/GPU required."""

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.meet_dialog_gpu_fixture import (
    MODEL_DIGEST,
    MODEL_VOLUME,
    OLLAMA_IMAGE,
    DialogGpuFixture,
    provider_command,
    worker_command,
)
from tests.meet_gpu_source_fixture import driver_bindings
from tests.test_meet_gpu_source_fixture import IMAGE, mounts

BASE = "meet-test-inference-12345678-1234-1234-1234-123456789abc"


def test_provider_only_mounts_readonly_model_subdirectory_and_never_serving_keys():
    args = provider_command(BASE + "-ollama", BASE + "-network", driver_bindings(mounts()), 240)
    assert f"type=volume,src={MODEL_VOLUME},dst=/models,volume-subpath=models,readonly" in args
    assert "--pull=never" in args and "--read-only" in args and "--user=1000:1000" in args
    assert "--env=OLLAMA_NO_CLOUD=true" in args and "--tmpfs=/home/ubuntu/.ollama:rw,size=4m,mode=1777" in args
    assert args[-6:] == [OLLAMA_IMAGE, "--signal=TERM", "--kill-after=5", "240", "/bin/ollama", "serve"]
    assert all(secret not in " ".join(args) for secret in ("docker.sock", "--privileged", "id_ed25519", "--publish"))


def test_worker_has_only_current_source_models_and_ephemeral_key_mounts(tmp_path):
    args = worker_command(
        BASE + "-worker", BASE + "-network", IMAGE, driver_bindings(mounts()), 240, tmp_path / "key", tmp_path
    )
    assert "--env=MEET_OLLAMA_URL=http://meet-test-ollama:11434" in args
    assert "--tmpfs=/state:rw,size=16m,mode=1777" in args
    for index, value in enumerate(args):
        if value == "--mount":
            assert args[index + 1].endswith(",readonly")
    assert args[-7:] == [IMAGE, "--signal=TERM", "--kill-after=5", "240", "python", "-m", "worker.meet_media.server"]
    assert not any(value.startswith("--env=HOME=") or "docker.sock" in value or "--publish" in value for value in args)


@pytest.mark.parametrize("change", ["name", "network", "image", "duration", "driver", "writable", "other_network"])
def test_invalid_configuration_fails_before_docker(change, tmp_path):
    fields = [BASE + "-worker", BASE + "-network", IMAGE, driver_bindings(mounts()), 240, tmp_path / "key"]
    if change == "name":
        fields[0] = "/"
    elif change == "network":
        fields[1] = "host"
    elif change == "image":
        fields[2] = "latest"
    elif change == "duration":
        fields[4] = True
    elif change == "driver":
        fields[3] = ["type=bind,src=/secret,dst=/host-nvidia/libcuda.so.1,readonly"]
    elif change == "writable":
        fields[3] = [item.replace("readonly", "rw") for item in fields[3]]
    else:
        fields[1] = fields[1].replace("12345678", "abcdefab")
    with pytest.raises(ValueError):
        worker_command(*fields)


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "network_create",
        "network_public",
        "provider_create",
        "provider_start",
        "worker_create",
        "worker_start",
        "health",
        "wrong_membership",
    ],
)
def test_every_owned_resource_is_cleaned_after_partial_or_uncertain_setup(failure):
    instance = DialogGpuFixture(check_capacity=lambda: None)
    created = []

    def command(*args):
        if args[0] == "inspect" and args[1] == "ananta-meet-media-meet-media-worker-1":
            return IMAGE if args[-1] == "{{.Image}}" else json.dumps(mounts())
        if args[:2] == ("image", "inspect"):
            return OLLAMA_IMAGE
        if args[:2] == ("volume", "inspect"):
            return MODEL_VOLUME
        if args[:2] == ("network", "create"):
            created.append(("network", instance.network))
            if failure == "network_create":
                raise subprocess.TimeoutExpired("create", 1)
        if args[:2] == ("network", "inspect"):
            return json.dumps(
                [{"Internal": failure != "network_public", "IPAM": {"Config": [{"Subnet": "172.30.0.0/24"}]}}]
            )
        if args[0] == "create":
            created.append(("container", args[2]))
            if failure == ("provider_create" if args[2] == instance.provider else "worker_create"):
                raise subprocess.TimeoutExpired("create", 1)
        if args[0] == "start" and failure == ("provider_start" if args[1] == instance.provider else "worker_start"):
            raise subprocess.TimeoutExpired("start", 1)
        if args[0] == "inspect":
            return json.dumps(
                {"foreign" if failure == "wrong_membership" else instance.network: {"IPAddress": "172.30.0.2"}}
            )
        return ""

    instance.command = Mock(side_effect=command)
    instance._ready = Mock(side_effect=ValueError("health") if failure == "health" else None)
    if failure:
        with pytest.raises((ValueError, subprocess.TimeoutExpired)):
            instance.start()
    else:
        instance.start()
        key_path = Path(instance.temporary.name) / "key"
        assert key_path.stat().st_mode & 0o077 == 0 and key_path.read_bytes() == instance.key
        instance.close()
        assert not key_path.exists()
    calls = [call.args for call in instance.command.call_args_list]
    cleanup = [call for call in calls if call[0] == "rm" or call[:2] == ("network", "rm")]
    expected = [
        ("network", "rm", name) if kind == "network" else ("rm", "--force", name) for kind, name in reversed(created)
    ]
    assert cleanup == expected and not instance.resources and instance.temporary is None


@pytest.mark.parametrize("change", [None, "cpu", "digest", "status", "oversize", "incomplete"])
def test_preload_has_no_prompt_and_requires_pinned_gpu_residency(change, monkeypatch):
    instance = DialogGpuFixture()
    instance.provider_address = ("172.30.0.2", 11434)
    instance.endpoint = "http://172.30.0.3:8094/v1/turns"
    first = Mock(status=503 if change == "status" else 200)
    first.read.return_value = (
        b"a" * 65537 if change == "oversize" else json.dumps({"done": change != "incomplete"}).encode()
    )
    second = Mock(status=200)
    second.read.return_value = json.dumps(
        {
            "models": [
                {
                    "name": "qwen2.5:1.5b",
                    "digest": "wrong" if change == "digest" else MODEL_DIGEST,
                    "size_vram": 0 if change == "cpu" else 100,
                }
            ]
        }
    ).encode()
    connection = Mock()
    connection.getresponse.side_effect = [first, second]
    monkeypatch.setattr("tests.meet_dialog_gpu_fixture.http.client.HTTPConnection", Mock(return_value=connection))
    if change:
        with pytest.raises(ValueError):
            instance.preload()
    else:
        assert instance.preload() >= 0
    payload = json.loads(connection.request.call_args_list[0].args[2])
    assert payload["prompt"] == "" and payload["model"] == "qwen2.5:1.5b" and payload["options"]["num_gpu"] == 99
    assert "task_id" not in payload and "messages" not in payload and "tools" not in payload
    connection.close.assert_called_once()
