"""Closed GPU/model resources for an owned packaged receive Worker only."""

import json
from pathlib import Path

from tests.meet_gpu_capacity import require_gpu_capacity
from tests.meet_gpu_source_fixture import driver_bindings


def receive_gpu_arguments(command, *, check_capacity=require_gpu_capacity):
    check_capacity()
    models = Path(__file__).resolve().parents[1] / "data/meet-media/models"
    if not models.is_dir() or models.is_symlink():
        raise ValueError("test_receive_models_unavailable")
    drivers = driver_bindings(
        json.loads(command("inspect", "ananta-meet-media-meet-media-worker-1", "--format", "{{json .Mounts}}"))
    )
    arguments = ["--env=LD_LIBRARY_PATH=/host-nvidia", "--mount", f"type=bind,src={models},dst=/models,readonly"]
    for device in ("nvidia0", "nvidiactl", "nvidia-uvm", "nvidia-uvm-tools"):
        arguments += ["--device", "/dev/" + device]
    for binding in drivers:
        arguments += ["--mount", binding]
    return arguments
