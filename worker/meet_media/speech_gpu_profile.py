"""Fixed speech-provider allocation policy; not a quota for all device users."""

CUDA_ARENA_BYTES = 2 * 1024 * 1024 * 1024


def cuda_provider_options():
    return {
        "gpu_mem_limit": str(CUDA_ARENA_BYTES),
        "arena_extend_strategy": "kSameAsRequested",
        "cudnn_conv_algo_search": "HEURISTIC",
        "cudnn_conv_use_max_workspace": "0",
    }


def require_cuda_budget(session):
    """An unsupported/ignored allocation setting is not a bounded GPU profile."""
    actual = session.get_provider_options().get("CUDAExecutionProvider", {})
    if any(actual.get(key) != value for key, value in cuda_provider_options().items()):
        raise ValueError("meet_piper_cuda_budget_unavailable")
