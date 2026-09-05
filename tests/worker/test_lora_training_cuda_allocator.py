from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from worker.training import cuda_allocator, job_process
from worker.training.cuda_allocator import (
    CUDA_MEMORY_FRACTION_ENV,
    CudaAllocatorConfigurationError,
    configure_cuda_allocator_from_environment,
)
from worker.training.subprocess_executor import _child_environment


class _FakeCuda:
    def __init__(self, *, available: bool = True, devices: int = 1, failure: Exception | None = None) -> None:
        self.available = available
        self.devices = devices
        self.failure = failure
        self.applied: list[tuple[float, int]] = []

    def is_available(self) -> bool:
        return self.available

    def device_count(self) -> int:
        return self.devices

    def set_per_process_memory_fraction(self, fraction: float, *, device: int) -> None:
        if self.failure is not None:
            raise self.failure
        self.applied.append((fraction, device))


def _install_fake_torch(monkeypatch: pytest.MonkeyPatch, cuda: Any) -> None:
    monkeypatch.setattr(cuda_allocator.importlib, "import_module", lambda name: SimpleNamespace(cuda=cuda))


def test_unconfigured_allocator_is_a_noop_without_loading_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cuda_allocator.importlib,
        "import_module",
        lambda name: pytest.fail("torch must not be loaded for mock/CPU workers"),
    )

    assert configure_cuda_allocator_from_environment({}) is None


def test_valid_fraction_is_applied_to_each_visible_cuda_device(monkeypatch: pytest.MonkeyPatch) -> None:
    cuda = _FakeCuda(devices=2)
    _install_fake_torch(monkeypatch, cuda)

    applied = configure_cuda_allocator_from_environment({CUDA_MEMORY_FRACTION_ENV: " 0.90 "})

    assert applied == 0.9
    assert cuda.applied == [(0.9, 0), (0.9, 1)]


@pytest.mark.parametrize("raw", ["", " ", "not-a-number", "nan", "inf", "-inf", "0", "-0.1", "1.01"])
def test_invalid_fraction_fails_before_torch_is_loaded(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setattr(
        cuda_allocator.importlib,
        "import_module",
        lambda name: pytest.fail("invalid configuration must fail before loading torch"),
    )

    with pytest.raises(CudaAllocatorConfigurationError) as error:
        configure_cuda_allocator_from_environment({CUDA_MEMORY_FRACTION_ENV: raw})

    assert error.value.code == "invalid_cuda_memory_fraction"
    assert error.value.retryable is False


@pytest.mark.parametrize(
    ("cuda", "retryable"),
    [
        (_FakeCuda(available=False), True),
        (_FakeCuda(devices=0), True),
        (_FakeCuda(failure=RuntimeError("allocator rejected limit")), True),
    ],
)
def test_configured_allocator_fails_closed_when_cuda_cannot_apply_it(
    monkeypatch: pytest.MonkeyPatch,
    cuda: _FakeCuda,
    retryable: bool,
) -> None:
    _install_fake_torch(monkeypatch, cuda)

    with pytest.raises(CudaAllocatorConfigurationError) as error:
        configure_cuda_allocator_from_environment({CUDA_MEMORY_FRACTION_ENV: "0.5"})

    assert error.value.code == "cuda_allocator_unavailable"
    assert error.value.retryable is retryable


def test_child_environment_passes_allocator_limit_but_not_worker_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CUDA_MEMORY_FRACTION_ENV, "0.75")
    monkeypatch.setenv("ANANTA_LORA_TRAINING_TOKEN", "must-not-enter-child")

    environment = _child_environment()

    assert environment[CUDA_MEMORY_FRACTION_ENV] == "0.75"
    assert environment["HF_HOME"] == "/tmp/huggingface"
    assert environment["HOME"] == "/tmp"
    assert environment["XDG_CACHE_HOME"] == "/tmp/cache"
    assert "ANANTA_LORA_TRAINING_TOKEN" not in environment


def test_isolated_child_reports_invalid_fraction_before_reading_job_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_context = tmp_path / "missing-context.json"
    result_path = tmp_path / "result.json"
    monkeypatch.setenv(CUDA_MEMORY_FRACTION_ENV, "NaN")
    monkeypatch.setattr(
        sys,
        "argv",
        ["job_process", "--context", str(missing_context), "--result", str(result_path)],
    )

    assert job_process.main() == 3

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result == {
        "error": {
            "code": "invalid_cuda_memory_fraction",
            "message": (
                "ANANTA_LORA_TRAINING_CUDA_MEMORY_FRACTION must be a finite number greater than 0 and at most 1"
            ),
            "retryable": False,
        },
        "status": "failed",
    }
