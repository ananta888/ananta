"""Pinned model restoration and private GPU provider boundary checks."""

import hashlib
import io
import json
from unittest.mock import Mock

import pytest

from tests import meet_companion_ollama_fixture as ollama
from tests import meet_ollama_model_fixture as model
from tests.meet_companion_ollama_fixture import CompanionOllamaFixture


def synthetic_manifest(monkeypatch):
    blob = b"synthetic-test-model"
    layer = {"digest": "sha256:" + hashlib.sha256(blob).hexdigest(), "size": len(blob)}
    raw = json.dumps({"config": layer, "layers": []}).encode()
    monkeypatch.setattr(model, "MODEL_DIGEST", hashlib.sha256(raw).hexdigest())
    return raw, blob


def test_restored_fixture_requires_exact_manifest_and_every_blob(monkeypatch, tmp_path):
    raw, blob = synthetic_manifest(monkeypatch)
    open_url = Mock(side_effect=[io.BytesIO(raw), io.BytesIO(blob)])
    directory = tmp_path / "owned-model"
    model.restore_model(directory, open_url=open_url)
    assert model.validate_model(directory) == directory
    target = next((directory / "blobs").iterdir())
    target.write_bytes(b"x" * len(blob))
    with pytest.raises(ValueError, match="blob_pin_mismatch"):
        model.validate_model(directory)


@pytest.mark.parametrize("failure", ["manifest", "blob", "oversized"])
def test_invalid_download_removes_only_its_new_directory(monkeypatch, tmp_path, failure):
    raw, blob = synthetic_manifest(monkeypatch)
    if failure == "manifest":
        raw = b"{}"
    elif failure == "blob":
        blob = b"x" * len(blob)
    else:
        blob += b"x"
    survivor = tmp_path / "existing"
    survivor.write_text("keep")
    directory = tmp_path / "owned-model"
    with pytest.raises(ValueError):
        model.restore_model(directory, open_url=Mock(side_effect=[io.BytesIO(raw), io.BytesIO(blob)]))
    assert not directory.exists() and survivor.read_text() == "keep"


def test_existing_directory_is_never_overwritten(tmp_path):
    with pytest.raises(ValueError, match="not_fresh"):
        model.restore_model(tmp_path, open_url=Mock(side_effect=AssertionError("no network")))


@pytest.mark.parametrize("network", ["host", "bridge", "serving-network", "meet-test-tls-../-network"])
def test_provider_rejects_network_outside_owned_scope_before_docker(network, tmp_path):
    command = Mock()
    with pytest.raises(ValueError, match="resource_scope_invalid"):
        CompanionOllamaFixture(network, tmp_path, "invalid", command=command)
    command.assert_not_called()


def provider(monkeypatch, tmp_path, command):
    monkeypatch.setattr(ollama, "validate_model", lambda _value: tmp_path)
    monkeypatch.setattr(ollama, "driver_bindings", lambda _value: [
        "type=bind,src=/usr/lib/wsl/lib/libcuda.so.1,dst=/usr/lib/wsl/lib/libcuda.so.1,readonly"])
    return CompanionOllamaFixture("meet-test-inference-12345678-1234-1234-1234-123456789abc-network",
        tmp_path, "/usr/lib/wsl/drivers/nv_dispig.inf_amd64_123456789abcdef0",
        command=command, check_capacity=lambda: None)


def test_wsl_provider_preserves_image_model_and_execution_boundaries(monkeypatch, tmp_path):
    fixture = provider(monkeypatch, tmp_path, Mock())
    args = fixture.arguments
    assert "--read-only" in args and "--cap-drop=ALL" in args and "--user=1000:1000" in args
    assert "--env=OLLAMA_NO_CLOUD=true" in args and "--device=/dev/dxg" in args
    assert args[-6:] == [ollama.OLLAMA_IMAGE, "--signal=TERM", "--kill-after=5", "360", "/bin/ollama", "serve"]
    assert all(args[index + 1].endswith(",readonly") for index, value in enumerate(args) if value == "--mount")
    forbidden = ("--publish", "--privileged", "docker.sock", "/secrets")
    assert all(value not in " ".join(args) for value in forbidden)


def test_public_network_fails_before_container_creation(monkeypatch, tmp_path):
    command = Mock(side_effect=[ollama.OLLAMA_IMAGE, json.dumps([{
        "Internal": False, "IPAM": {"Config": [{"Subnet": "172.25.0.0/16"}]}}])])
    fixture = provider(monkeypatch, tmp_path, command)
    with pytest.raises(ValueError, match="network_invalid"):
        fixture.start()
    assert not fixture.created and all(call.args[0] != "create" for call in command.call_args_list)


def test_changed_membership_stops_only_owned_container(monkeypatch, tmp_path):
    command = Mock(side_effect=[ollama.OLLAMA_IMAGE, json.dumps([{
        "Internal": True, "IPAM": {"Config": [{"Subnet": "172.25.0.0/16"}]}}]),
        "created", "started", json.dumps({"foreign": {"IPAddress": "172.25.0.2"}}), "removed"])
    fixture = provider(monkeypatch, tmp_path, command)
    with pytest.raises(ValueError, match="network_changed"):
        fixture.start()
    assert command.call_args.args == ("rm", "--force", fixture.name)
    assert not fixture.created
