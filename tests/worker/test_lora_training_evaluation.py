from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from worker.training.backends.base import TrainingBackendError
from worker.training.evaluation import (
    PeftAdapterEvaluator,
    UnslothAdapterModelLoader,
    _evaluate_base_and_adapter,
    evaluator_for_backend,
)


class _AdapterModel:
    def __init__(self) -> None:
        self.disabled = False

    @contextmanager
    def disable_adapter(self):
        self.disabled = True
        try:
            yield
        finally:
            self.disabled = False


class _Trainer:
    def __init__(self, *, model: _AdapterModel, **_: Any) -> None:
        self.model = model
        self.states: list[bool] = []

    def evaluate(self) -> dict[str, float]:
        self.states.append(self.model.disabled)
        return {"eval_loss": 1.0 if self.model.disabled else 0.75}


def test_quantized_base_comparison_uses_loaded_peft_model_with_adapter_disabled() -> None:
    model = _AdapterModel()
    trainers: list[_Trainer] = []
    phases: list[str] = []
    checkpoints: list[str] = []

    def trainer_factory(**kwargs: Any) -> _Trainer:
        trainer = _Trainer(**kwargs)
        trainers.append(trainer)
        return trainer

    base, adapter = _evaluate_base_and_adapter(
        adapter_model=model,
        trainer_factory=trainer_factory,
        trainer_arguments=object(),
        eval_dataset=object(),
        data_collator=object(),
        before_adapter_evaluation=lambda: checkpoints.append("checked"),
        emit=lambda _kind, payload: phases.append(str(payload["phase"])),
    )

    assert base == {"eval_loss": 1.0}
    assert adapter == {"eval_loss": 0.75}
    assert len(trainers) == 1
    assert trainers[0].states == [True, False]
    assert checkpoints == ["checked"]
    assert phases == ["evaluating_adapter"]


def test_base_comparison_fails_closed_without_peft_disable_adapter_support() -> None:
    with pytest.raises(TrainingBackendError, match="cannot disable the adapter") as exc_info:
        _evaluate_base_and_adapter(
            adapter_model=object(),
            trainer_factory=_Trainer,
            trainer_arguments=object(),
            eval_dataset=object(),
            data_collator=object(),
            before_adapter_evaluation=lambda: None,
            emit=lambda _kind, _payload: None,
        )

    assert exc_info.value.code == "evaluation_failed"


class _Cuda:
    @staticmethod
    def is_available() -> bool:
        return True


class _Torch:
    cuda = _Cuda()


class _FastLanguageModel:
    calls: list[dict[str, Any]] = []
    inference_models: list[object] = []

    @classmethod
    def from_pretrained(cls, **kwargs: Any) -> tuple[object, object]:
        cls.calls.append(kwargs)
        return object(), object()

    @classmethod
    def for_inference(cls, model: object) -> None:
        cls.inference_models.append(model)


def test_unsloth_loader_loads_adapter_directory_through_patched_runtime(tmp_path) -> None:
    from types import SimpleNamespace

    _FastLanguageModel.calls.clear()
    _FastLanguageModel.inference_models.clear()
    adapter_path = tmp_path / "adapter"
    context = SimpleNamespace(
        adapter_path=adapter_path,
        request=SimpleNamespace(
            configuration=SimpleNamespace(quantization="4bit", max_sequence_length=128),
        ),
    )

    model, _tokenizer = UnslothAdapterModelLoader(_FastLanguageModel).load(context, _Torch())

    assert _FastLanguageModel.calls == [{
        "model_name": str(adapter_path),
        "max_seq_length": 128,
        "load_in_4bit": True,
        "load_in_8bit": False,
        "load_in_16bit": False,
        "local_files_only": True,
        "trust_remote_code": False,
    }]
    assert _FastLanguageModel.inference_models == [model]


def test_unsloth_backend_selects_its_own_substitutable_model_loader() -> None:
    evaluator = evaluator_for_backend("unsloth")

    assert isinstance(evaluator, PeftAdapterEvaluator)
    assert isinstance(evaluator._model_loader, UnslothAdapterModelLoader)
