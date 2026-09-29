"""Fail closed when the runtime does not honor the fixed speech arena policy."""

from unittest.mock import Mock

import pytest

from worker.meet_media.speech_gpu_profile import cuda_provider_options, require_cuda_budget


def test_speech_arena_and_workspace_limits_are_explicit_and_not_shared_mutable_state():
    expected = {
        "gpu_mem_limit": "2147483648",
        "arena_extend_strategy": "kSameAsRequested",
        "cudnn_conv_algo_search": "HEURISTIC",
        "cudnn_conv_use_max_workspace": "0",
    }
    assert cuda_provider_options() == expected
    altered = cuda_provider_options()
    altered["gpu_mem_limit"] = "0"
    assert cuda_provider_options() == expected
    session = Mock()
    session.get_provider_options.return_value = {"CUDAExecutionProvider": expected}
    require_cuda_budget(session)


@pytest.mark.parametrize("key", list(cuda_provider_options()))
@pytest.mark.parametrize("change", ["missing", "changed"])
def test_missing_or_ignored_setting_is_not_a_supported_budget(key, change):
    options = cuda_provider_options()
    if change == "missing":
        options.pop(key)
    else:
        options[key] = "unsupported"
    session = Mock()
    session.get_provider_options.return_value = {"CUDAExecutionProvider": options}
    with pytest.raises(ValueError, match="^meet_piper_cuda_budget_unavailable$"):
        require_cuda_budget(session)


def test_cpu_provider_is_not_a_budgeted_cuda_session():
    session = Mock()
    session.get_provider_options.return_value = {"CPUExecutionProvider": {}}
    with pytest.raises(ValueError, match="^meet_piper_cuda_budget_unavailable$"):
        require_cuda_budget(session)
