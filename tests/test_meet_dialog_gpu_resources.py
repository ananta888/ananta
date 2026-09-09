"""No serving credentials, source mounts or provider access in GPU receive tests."""

import json
from unittest.mock import Mock

import pytest

from tests.meet_dialog_gpu_resources import receive_gpu_arguments
from tests.meet_dialog_worker_container import DialogWorkerContainer
from tests.test_meet_dialog_worker_container_fixture import HUB, IMAGE, NETWORK
from tests.test_meet_gpu_source_fixture import mounts


def test_gpu_resources_project_only_fixed_devices_models_and_readonly_drivers(tmp_path):
    command, capacity = Mock(return_value=json.dumps(mounts())), Mock()
    # Resource projection is a unit test, not model/hardware acceptance. The real
    # fixture retains its default repository model directory and capacity check.
    arguments = receive_gpu_arguments(command, check_capacity=capacity, model_directory=tmp_path)
    capacity.assert_called_once_with()
    command.assert_called_once_with("inspect", "ananta-meet-media-meet-media-worker-1", "--format", "{{json .Mounts}}")
    bindings = [arguments[i + 1] for i, value in enumerate(arguments) if value == "--mount"]
    assert all(value.endswith(",readonly") for value in bindings)
    assert not any("dst=/app" in value or "secret" in value or "sock" in value for value in bindings)
    assert sum("dst=/models," in value for value in bindings) == 1
    assert f"type=bind,src={tmp_path},dst=/models,readonly" in bindings
    assert [arguments[i + 1] for i, value in enumerate(arguments) if value == "--device"] == [
        "/dev/nvidia0",
        "/dev/nvidiactl",
        "/dev/nvidia-uvm",
        "/dev/nvidia-uvm-tools",
    ]


@pytest.mark.parametrize("kind", ["missing", "file", "symlink"])
def test_missing_or_indirect_model_directory_fails_before_docker_inspection(tmp_path, kind):
    models = tmp_path / "models"
    if kind == "file":
        models.write_text("synthetic-not-a-model")
    elif kind == "symlink":
        models.symlink_to(tmp_path, target_is_directory=True)
    command = Mock()
    with pytest.raises(ValueError, match="test_receive_models_unavailable"):
        receive_gpu_arguments(command, check_capacity=Mock(), model_directory=models)
    command.assert_not_called()


def test_no_capacity_never_inspects_serving_service():
    command = Mock()
    with pytest.raises(ValueError, match="capacity"):
        receive_gpu_arguments(command, check_capacity=Mock(side_effect=ValueError("capacity")))
    command.assert_not_called()


@pytest.mark.parametrize("value", [1, "true", None, {}])
def test_gpu_requires_explicit_boolean_before_any_docker_calls(value):
    command = Mock()
    with pytest.raises(ValueError, match="gpu_invalid"):
        DialogWorkerContainer(NETWORK, IMAGE, HUB, gpu=value, command=command)
    command.assert_not_called()
