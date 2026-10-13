"""GPU test resource admission refuses indirect assets before hardware access."""

from unittest.mock import Mock

import pytest

from tests.meet_companion_gpu_resources import gpu_tts_arguments

STORE = "/usr/lib/wsl/drivers/nv_dispig.inf_amd64_" + "a" * 16


@pytest.mark.parametrize("kind", ["missing", "file", "symlink"])
def test_unavailable_or_indirect_models_never_check_gpu_capacity(tmp_path, kind):
    models = tmp_path / "models"
    if kind == "file":
        models.write_text("synthetic")
    elif kind == "symlink":
        models.symlink_to(tmp_path, target_is_directory=True)
    capacity = Mock()
    with pytest.raises(ValueError, match="test_companion_gpu_models_unavailable"):
        gpu_tts_arguments(models, STORE, check_capacity=capacity)
    capacity.assert_not_called()


def test_unknown_driver_store_never_checks_capacity(tmp_path):
    capacity = Mock()
    with pytest.raises(ValueError, match="test_gpu_wsl_driver_store_invalid"):
        gpu_tts_arguments(tmp_path, "/private", check_capacity=capacity)
    capacity.assert_not_called()
