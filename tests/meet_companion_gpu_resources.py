"""Closed WSL resources for an owned actual assigned-Companion TTS gate."""

from tests.meet_gpu_capacity import require_gpu_capacity
from tests.meet_gpu_wsl_fixture import driver_bindings, validate_driver_store


def gpu_tts_arguments(models, store, *, check_capacity=require_gpu_capacity):
    if not models.is_dir() or models.is_symlink():
        raise ValueError("test_companion_gpu_models_unavailable")
    driver_store = validate_driver_store(store)
    drivers = driver_bindings(store)
    check_capacity()
    resources = ["--env=TEST_REAL_ASSIGNED_TTS=1", "--device=/dev/dxg",
                 "--env=LD_LIBRARY_PATH=/usr/lib/wsl/lib:" + str(driver_store),
                 "--mount", f"type=bind,src={models},dst=/models,readonly"]
    for binding in drivers:
        resources.extend(["--mount", binding])
    return resources
