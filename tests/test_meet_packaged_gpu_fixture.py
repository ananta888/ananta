"""Explicit packaged-image selection is independent of serving-service deployment."""

import json
import subprocess
from unittest.mock import Mock

import pytest

from tests.meet_dialog_gpu_fixture import MODEL_VOLUME, OLLAMA_IMAGE, DialogGpuFixture, worker_command
from tests.meet_gpu_source_fixture import driver_bindings
from tests.test_meet_dialog_gpu_fixture import BASE
from tests.test_meet_gpu_source_fixture import IMAGE, mounts


@pytest.mark.parametrize("image", ["", "latest", "worker:latest", "sha256:a", True, 1, "https://private/image"])
def test_invalid_packaged_identity_fails_before_gpu_checks_or_docker(image):
    command, capacity = Mock(), Mock()
    with pytest.raises(ValueError, match="test_inference_packaged_image_invalid"):
        DialogGpuFixture(packaged_image=image, command=command, check_capacity=capacity)
    command.assert_not_called()
    capacity.assert_not_called()


@pytest.mark.parametrize("missing", [False, True])
def test_missing_or_mismatched_packaged_image_never_falls_back_or_creates_resources(missing):
    command = Mock(return_value="sha256:" + "f" * 64)
    if missing:
        command.side_effect = subprocess.CalledProcessError(1, "docker")
    fixture = DialogGpuFixture(packaged_image=IMAGE, command=command, check_capacity=lambda: None)
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        fixture.start()
    command.assert_called_once_with("image", "inspect", IMAGE, "--format", "{{.Id}}")
    assert fixture.resources == [] and fixture.temporary is None and fixture.image is None


def test_packaged_worker_mounts_models_key_and_drivers_but_no_source(tmp_path):
    args = worker_command(
        BASE + "-worker",
        BASE + "-network",
        IMAGE,
        driver_bindings(mounts()),
        240,
        tmp_path / "key",
        tmp_path,
        packaged=True,
    )
    bindings = [args[i + 1] for i, value in enumerate(args) if value == "--mount"]
    assert all(value.endswith(",readonly") for value in bindings)
    assert not any("dst=/app/" in value for value in bindings)
    assert f"type=bind,src={tmp_path / 'data/meet-media/models'},dst=/models,readonly" in bindings
    assert f"type=bind,src={tmp_path / 'key'},dst=/run/secrets/test-key,readonly" in bindings
    assert len(bindings) == len(driver_bindings(mounts())) + 2
    assert "--pull=never" in args and "--read-only" in args and "--user=1000:1000" in args


def test_packaged_start_uses_exact_image_and_only_reads_serving_driver_mounts():
    fixture = DialogGpuFixture(packaged_image=IMAGE, check_capacity=lambda: None)

    def command(*args):
        if args[:2] == ("image", "inspect"):
            return args[2]
        if args[:2] == ("volume", "inspect"):
            return MODEL_VOLUME
        if args[:2] == ("network", "inspect"):
            return json.dumps([{"Internal": True, "IPAM": {"Config": [{"Subnet": "172.30.0.0/24"}]}}])
        if args[:2] == ("inspect", "ananta-meet-media-meet-media-worker-1"):
            assert args[-1] == "{{json .Mounts}}"
            return json.dumps(mounts())
        if args[0] == "inspect":
            return json.dumps({fixture.network: {"IPAddress": "172.30.0.2"}})
        return ""

    fixture.command = Mock(side_effect=command)
    fixture._ready = Mock()
    try:
        assert fixture.start() is fixture and fixture.image == IMAGE
        creates = [call.args for call in fixture.command.call_args_list if call.args[0] == "create"]
        assert len(creates) == 2 and OLLAMA_IMAGE in creates[0] and IMAGE in creates[1]
        assert not any("dst=/app/" in arg for arg in creates[1])
    finally:
        fixture.close()
    calls = [call.args for call in fixture.command.call_args_list]
    assert calls[-3:] == [
        ("rm", "--force", fixture.worker),
        ("rm", "--force", fixture.provider),
        ("network", "rm", fixture.network),
    ]
    assert fixture.resources == [] and fixture.temporary is None
