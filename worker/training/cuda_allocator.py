"""Fail-closed PyTorch CUDA allocator configuration for isolated job processes."""

from __future__ import annotations

import importlib
import math
import os
from typing import Any, Mapping

CUDA_MEMORY_FRACTION_ENV = "ANANTA_LORA_TRAINING_CUDA_MEMORY_FRACTION"


class CudaAllocatorConfigurationError(RuntimeError):
    """Transport-safe failure raised before a CUDA training backend is loaded."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def configure_cuda_allocator_from_environment(
    environment: Mapping[str, str] | None = None,
) -> float | None:
    """Apply the configured caching-allocator fraction to every visible CUDA device.

    An absent setting is a no-op for mock and CPU workers. Once configured, all
    validation and CUDA setup errors reject the child attempt rather than
    silently running without the requested allocator ceiling.
    """

    values = os.environ if environment is None else environment
    if CUDA_MEMORY_FRACTION_ENV not in values:
        return None
    fraction = _parse_fraction(values[CUDA_MEMORY_FRACTION_ENV])
    torch_module = _load_torch()
    cuda = getattr(torch_module, "cuda", None)
    if cuda is None or not callable(getattr(cuda, "is_available", None)):
        raise CudaAllocatorConfigurationError(
            "cuda_allocator_unavailable",
            "configured CUDA allocator control is unavailable",
            retryable=True,
        )
    try:
        available = bool(cuda.is_available())
        device_count = int(cuda.device_count()) if available else 0
    except Exception as exc:
        raise CudaAllocatorConfigurationError(
            "cuda_allocator_unavailable",
            "configured CUDA allocator control could not inspect visible devices",
            retryable=True,
        ) from exc
    setter = getattr(cuda, "set_per_process_memory_fraction", None)
    if not available or device_count < 1 or not callable(setter):
        raise CudaAllocatorConfigurationError(
            "cuda_allocator_unavailable",
            "configured CUDA allocator control requires at least one visible CUDA device",
            retryable=True,
        )
    try:
        for device_index in range(device_count):
            setter(fraction, device=device_index)
    except Exception as exc:
        raise CudaAllocatorConfigurationError(
            "cuda_allocator_unavailable",
            "configured CUDA allocator ceiling could not be applied",
            retryable=True,
        ) from exc
    return fraction


def _parse_fraction(raw_value: Any) -> float:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise _invalid_fraction()
    try:
        fraction = float(raw_value.strip())
    except (TypeError, ValueError) as exc:
        raise _invalid_fraction() from exc
    if not math.isfinite(fraction) or fraction <= 0.0 or fraction > 1.0:
        raise _invalid_fraction()
    return fraction


def _load_torch() -> Any:
    try:
        return importlib.import_module("torch")
    except ImportError as exc:
        raise CudaAllocatorConfigurationError(
            "cuda_allocator_unavailable",
            "configured CUDA allocator control requires the pinned PyTorch runtime",
        ) from exc


def _invalid_fraction() -> CudaAllocatorConfigurationError:
    return CudaAllocatorConfigurationError(
        "invalid_cuda_memory_fraction",
        f"{CUDA_MEMORY_FRACTION_ENV} must be a finite number greater than 0 and at most 1",
    )
