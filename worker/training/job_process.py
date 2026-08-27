"""Child entrypoint for exactly one isolated training backend attempt."""

from __future__ import annotations

import argparse
import json
import os
import signal
from pathlib import Path
from typing import Any, Callable, Mapping

from worker.training.contracts import (
    AdapterEvaluationJobRequest,
    TrainingContractError,
    TrainingJobRequest,
    parse_job_request,
)
from worker.training.cuda_allocator import CudaAllocatorConfigurationError, configure_cuda_allocator_from_environment
from worker.training.datasets import VerifiedDataset, VerifiedValidationDataset
from worker.training.process_control import CancellationToken, TrainingCancelled


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--context", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    context_path = Path(args.context)
    result_path = Path(args.result)
    cancel = CancellationToken()
    signal.signal(signal.SIGTERM, lambda signum, frame: cancel.cancel())
    try:
        configure_cuda_allocator_from_environment()
    except CudaAllocatorConfigurationError as exc:
        _write_result(
            result_path,
            {
                "status": "failed",
                "error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable},
            },
        )
        return 3

    # Loading backend implementations is deliberately deferred until after the
    # isolated child has successfully applied its configured CUDA ceiling.
    from worker.training.backends.base import TrainingBackendError

    try:
        payload = json.loads(context_path.read_text(encoding="utf-8"))
        request = parse_job_request(payload["request"])
        if isinstance(request, AdapterEvaluationJobRequest):
            return _run_evaluation(request, payload, result_path, cancel)
        return _run_training(request, payload, result_path, cancel)
    except TrainingCancelled:
        _write_result(
            result_path,
            {"status": "failed", "error": {"code": "cancelled", "message": "training cancelled", "retryable": False}},
        )
        return 2
    except (TrainingBackendError, TrainingContractError) as exc:
        _write_result(
            result_path,
            {
                "status": "failed",
                "error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable},
            },
        )
        return 3
    except MemoryError:
        _write_result(
            result_path,
            {
                "status": "failed",
                "error": {"code": "out_of_memory", "message": "training exhausted memory", "retryable": True},
            },
        )
        return 5
    except (KeyError, TypeError, ValueError, OSError):
        _write_result(
            result_path,
            {
                "status": "failed",
                "error": {
                    "code": "worker_process_failed",
                    "message": "isolated job context is invalid",
                    "retryable": False,
                },
            },
        )
        return 4


def _run_training(
    request: TrainingJobRequest,
    payload: Mapping[str, Any],
    result_path: Path,
    cancel: CancellationToken,
) -> int:
    from worker.training.backends import (
        MockTrainingBackend,
        NeedleTrainingBackend,
        PeftTrlTrainingBackend,
        UnslothAudioTrainingBackend,
        UnslothEmbeddingTrainingBackend,
        UnslothTrainingBackend,
        UnslothVisionTrainingBackend,
    )
    from worker.training.backends.autotrain import AutoTrainTrainingBackend
    from worker.training.backends.axolotl import AxolotlTrainingBackend
    from worker.training.backends.base import TrainingBackend, TrainingBackendError, TrainingContext, run_backend
    from worker.training.backends.llamafactory import LlamaFactoryTrainingBackend
    from worker.training.backends.torchtune import TorchtuneTrainingBackend

    dataset_data = _mapping(payload.get("dataset"), "dataset")
    dataset = VerifiedDataset(
        train_path=Path(str(dataset_data["train_path"])),
        validation_path=Path(str(dataset_data["validation_path"])),
        train_records=int(dataset_data["train_records"]),
        validation_records=int(dataset_data["validation_records"]),
        dataset_hash=str(dataset_data["dataset_hash"]),
    )
    factories: dict[str, Callable[[], TrainingBackend]] = {
        "autotrain": AutoTrainTrainingBackend,
        "axolotl": AxolotlTrainingBackend,
        "llamafactory": LlamaFactoryTrainingBackend,
        "mock": MockTrainingBackend,
        "needle": NeedleTrainingBackend,
        "peft_trl": PeftTrlTrainingBackend,
        "torchtune": TorchtuneTrainingBackend,
        "unsloth": UnslothTrainingBackend,
        "unsloth_audio": UnslothAudioTrainingBackend,
        "unsloth_embedding": UnslothEmbeddingTrainingBackend,
        "unsloth_vision": UnslothVisionTrainingBackend,
    }
    factory = factories.get(request.backend)
    if factory is None:
        raise TrainingBackendError("backend_unavailable", "training backend is not available in child image")
    backend = factory()
    context = TrainingContext(
        request=request,
        dataset=dataset,
        model_path=Path(str(payload["model_path"])),
        artifact_root=Path(str(payload["artifact_root"])),
        checkpoint_root=Path(str(payload["checkpoint_root"])),
        resume_path=Path(str(payload["resume_path"])) if payload.get("resume_path") else None,
        cancel=cancel,
        emit=_emit,
        checkpoint_state_root=(
            Path(str(payload["checkpoint_state_root"])) if payload.get("checkpoint_state_root") else None
        ),
    )
    outcome = run_backend(backend, context)
    _write_result(
        result_path,
        {
            "status": "succeeded",
            "metrics": dict(outcome.metrics),
            "artifacts": [str(path) for path in outcome.artifacts],
            "best_checkpoint": str(outcome.best_checkpoint) if outcome.best_checkpoint else None,
        },
    )
    return 0


def _run_evaluation(
    request: AdapterEvaluationJobRequest,
    payload: Mapping[str, Any],
    result_path: Path,
    cancel: CancellationToken,
) -> int:
    from worker.training.evaluation import AdapterEvaluationContext, evaluator_for_backend

    dataset_data = _mapping(payload.get("validation_dataset"), "validation_dataset")
    dataset = VerifiedValidationDataset(
        validation_path=Path(str(dataset_data["validation_path"])),
        validation_records=int(dataset_data["validation_records"]),
        dataset_hash=str(dataset_data["dataset_hash"]),
    )
    context = AdapterEvaluationContext(
        request=request,
        dataset=dataset,
        model_path=Path(str(payload["model_path"])),
        adapter_path=Path(str(payload["adapter_path"])),
        artifact_root=Path(str(payload["artifact_root"])),
        cancel=cancel,
        emit=_emit,
    )
    outcome = evaluator_for_backend(request.backend).evaluate_existing_adapter(context)
    _write_result(
        result_path,
        {
            "status": "succeeded",
            "metrics": dict(outcome.metrics),
            "artifacts": [str(path) for path in outcome.artifacts],
            "best_checkpoint": None,
        },
    )
    return 0


def _emit(event_type: str, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps({"type": event_type, "payload": dict(payload)}, sort_keys=True, separators=(",", ":"))
    print(f"ANANTA_LORA_EVENT {encoded}", flush=True)


def _write_result(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(dict(payload), sort_keys=True, separators=(",", ":")), encoding="utf-8")
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    temporary.replace(path)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
