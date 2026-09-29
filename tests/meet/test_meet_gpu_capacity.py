"""No GPU or human required: explicit read-only capacity fixtures."""

import subprocess
from unittest.mock import Mock

import pytest

from tests.meet_dialog_gpu_fixture import DialogGpuFixture
from tests.meet_gpu_capacity import require_gpu_capacity


@pytest.mark.parametrize(
    "free,valid",
    [
        ("4096\n", True),
        ("10000\n", True),
        ("1800", False),
        ("0", False),
        ("", False),
        ("4096\n4096", False),
        ("[N/A]", False),
    ],
)
def test_capacity_check_is_single_gpu_bounded_and_never_terminates_other_processes(free, valid):
    run = Mock(return_value=Mock(stdout=free))
    if valid:
        assert require_gpu_capacity(run=run) == int(free)
    else:
        with pytest.raises(ValueError, match="gpu_capacity"):
            require_gpu_capacity(run=run)
    run.assert_called_once_with(
        ["nvidia-smi", "--id=0", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )


@pytest.mark.parametrize(
    "error", [FileNotFoundError(), subprocess.TimeoutExpired("probe", 5), subprocess.CalledProcessError(1, "probe")]
)
def test_failed_capacity_probe_is_bounded_machine_readable_failure(error):
    with pytest.raises(ValueError, match="test_inference_gpu_capacity_unavailable"):
        require_gpu_capacity(run=Mock(side_effect=error))


def test_unavailable_capacity_creates_no_docker_or_filesystem_resources():
    command = Mock()
    fixture = DialogGpuFixture(command=command, check_capacity=Mock(side_effect=ValueError("unavailable")))
    with pytest.raises(ValueError, match="unavailable"):
        fixture.start()
    command.assert_not_called()
    assert fixture.resources == [] and fixture.temporary is None
