from __future__ import annotations

import hashlib
import inspect
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from worker.training.backends.base import TrainingBackendError, TrainingContext
from worker.training.backends.peft_trl import PeftTrlTrainingBackend
from worker.training.backends.unsloth import UnslothTrainingBackend
from worker.training.contracts import (
    CONTRACT_VERSION,
    BaseModelSpec,
    DatasetManifest,
    SplitManifest,
    TrainingConfiguration,
    TrainingJobRequest,
)
from worker.training.datasets import VerifiedDataset
from worker.training.process_control import CancellationToken


def _module(name: str, **attributes: Any) -> ModuleType:
    module = ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


def _write_split(path: Path, rows: list[dict[str, Any]]) -> SplitManifest:
    payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode()
    path.write_bytes(payload)
    return SplitManifest(
        relative_path=path.name,
        sha256=hashlib.sha256(payload).hexdigest(),
        record_count=len(rows),
    )


def _context(
    tmp_path: Path,
    *,
    backend: str = "peft_trl",
    quantization: str = "none",
    resume: bool = False,
) -> tuple[TrainingContext, list[tuple[str, dict[str, Any]]]]:
    train_path = tmp_path / "train.jsonl"
    validation_path = tmp_path / "validation.jsonl"
    train_manifest = _write_split(train_path, [{"text": "train-only"}])
    validation_manifest = _write_split(validation_path, [{"text": "validation-only"}])
    configuration = TrainingConfiguration(
        seed=17,
        max_steps=12,
        num_train_epochs=2.5,
        learning_rate=0.0003,
        train_batch_size=2,
        eval_batch_size=3,
        gradient_accumulation_steps=4,
        eval_steps=2,
        save_steps=4,
        early_stopping_patience=5,
        lora_rank=16,
        lora_alpha=32,
        lora_dropout=0.1,
        max_sequence_length=384,
        quantization=quantization,
        gradient_checkpointing=True,
        target_modules=("q_proj", "v_proj"),
    )
    dataset_manifest = DatasetManifest(
        dataset_id="dataset-1",
        dataset_version="v1",
        train=train_manifest,
        validation=validation_manifest,
    )
    request = TrainingJobRequest(
        contract_version=CONTRACT_VERSION,
        job_id="job-1",
        attempt_id="attempt-1",
        fencing_token=1,
        correlation_id="correlation-1",
        job_type="train_lora",
        backend=backend,
        resource_profile="nvidia" if quantization != "none" else "cpu",
        tenant_scope_digest="a" * 64,
        workspace_ref="workspace-1",
        deadline_epoch_ms=2**62,
        base_model=BaseModelSpec(
            model_id="local/base-model",
            relative_path="base-model",
            snapshot_hash="b" * 64,
        ),
        dataset=dataset_manifest,
        configuration=configuration,
    )
    events: list[tuple[str, dict[str, Any]]] = []
    resume_path = tmp_path / "checkpoint-4" if resume else None
    if resume_path is not None:
        resume_path.mkdir()
    model_path = tmp_path / "models" / "base-model"
    model_path.mkdir(parents=True)
    (model_path / "config.json").write_text(
        '{"model_type":"llama"}',
        encoding="utf-8",
    )
    (model_path / "model.safetensors").write_bytes(b"weights")
    return (
        TrainingContext(
            request=request,
            dataset=VerifiedDataset(
                train_path=train_path,
                validation_path=validation_path,
                train_records=1,
                validation_records=1,
                dataset_hash=dataset_manifest.identity_hash,
            ),
            model_path=model_path,
            artifact_root=tmp_path / "artifacts",
            checkpoint_root=tmp_path / "checkpoints",
            resume_path=resume_path,
            cancel=CancellationToken(),
            emit=lambda event_type, payload: events.append((event_type, dict(payload))),
        ),
        events,
    )


class _FakeDataset:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    @classmethod
    def from_list(cls, rows: list[dict[str, Any]]) -> "_FakeDataset":
        return cls(rows)


class _FakeTokenizer:
    pad_token_id = None
    eos_token = "<eos>"

    def __init__(self) -> None:
        self.pad_token: str | None = None

    def save_pretrained(self, target: str) -> None:
        path = Path(target)
        path.mkdir(parents=True, exist_ok=True)
        (path / "tokenizer.json").write_text("{}", encoding="utf-8")


class _FakeModel:
    def __init__(self) -> None:
        self.gradient_checkpointing_enabled = False

    def gradient_checkpointing_enable(self) -> None:
        self.gradient_checkpointing_enabled = True

    def save_pretrained(self, target: str, *, safe_serialization: bool) -> None:
        assert safe_serialization is True
        path = Path(target)
        path.mkdir(parents=True, exist_ok=True)
        (path / "adapter_model.safetensors").write_bytes(b"adapter")


def _install_peft_prepare_modules(
    monkeypatch: pytest.MonkeyPatch,
    *,
    load_error: BaseException | None = None,
) -> dict[str, Any]:
    calls: dict[str, Any] = {}
    tokenizer = _FakeTokenizer()
    model = _FakeModel()

    class AutoTokenizer:
        @staticmethod
        def from_pretrained(path: str, **kwargs: Any) -> _FakeTokenizer:
            calls["tokenizer"] = (path, kwargs)
            if load_error is not None:
                raise load_error
            return tokenizer

    class AutoModelForCausalLM:
        @staticmethod
        def from_pretrained(path: str, **kwargs: Any) -> _FakeModel:
            calls["model"] = (path, kwargs)
            return model

    class BitsAndBytesConfig:
        def __init__(self, **kwargs: Any) -> None:
            calls["quantization"] = kwargs

    class LoraConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            calls["lora"] = kwargs

    monkeypatch.setitem(
        sys.modules,
        "torch",
        _module(
            "torch",
            cuda=SimpleNamespace(is_available=lambda: False),
            bfloat16="bfloat16",
            float32="float32",
        ),
    )
    monkeypatch.setitem(sys.modules, "datasets", _module("datasets", Dataset=_FakeDataset))
    monkeypatch.setitem(
        sys.modules,
        "peft",
        _module(
            "peft",
            LoraConfig=LoraConfig,
            prepare_model_for_kbit_training=lambda value, **_kwargs: value,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        _module(
            "transformers",
            AutoModelForCausalLM=AutoModelForCausalLM,
            AutoTokenizer=AutoTokenizer,
            BitsAndBytesConfig=BitsAndBytesConfig,
        ),
    )
    calls["tokenizer_instance"] = tokenizer
    calls["model_instance"] = model
    return calls


def _install_unsloth_prepare_modules(
    monkeypatch: pytest.MonkeyPatch,
    *,
    load_error: BaseException | None = None,
) -> dict[str, Any]:
    calls: dict[str, Any] = {}
    tokenizer = _FakeTokenizer()
    model = _FakeModel()

    class FastLanguageModel:
        @staticmethod
        def from_pretrained(**kwargs: Any) -> tuple[_FakeModel, _FakeTokenizer]:
            calls["model"] = kwargs
            if load_error is not None:
                raise load_error
            return model, tokenizer

        @staticmethod
        def get_peft_model(value: _FakeModel, **kwargs: Any) -> _FakeModel:
            calls["lora"] = kwargs
            return value

    monkeypatch.setitem(sys.modules, "datasets", _module("datasets", Dataset=_FakeDataset))
    monkeypatch.setitem(sys.modules, "unsloth", _module("unsloth", FastLanguageModel=FastLanguageModel))
    return calls


def _install_trainer_modules(
    monkeypatch: pytest.MonkeyPatch,
    *,
    constructor_error: BaseException | None = None,
    train_error: BaseException | None = None,
    sequence_length_parameter: str = "max_seq_length",
) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    class TrainerCallback:
        pass

    class EarlyStoppingCallback:
        def __init__(self, *, early_stopping_patience: int) -> None:
            self.early_stopping_patience = early_stopping_patience

    class SFTConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            calls["configuration"] = kwargs

    SFTConfig.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [inspect.Parameter(sequence_length_parameter, inspect.Parameter.KEYWORD_ONLY)]
    )

    class SFTTrainer:
        def __init__(self, **kwargs: Any) -> None:
            if constructor_error is not None:
                raise constructor_error
            calls["trainer"] = kwargs
            self.model = kwargs["model"]
            best_checkpoint = Path(kwargs["args"].kwargs["output_dir"]) / "checkpoint-8"
            self.state = SimpleNamespace(best_model_checkpoint=str(best_checkpoint))

        def train(self, *, resume_from_checkpoint: str | None) -> SimpleNamespace:
            calls["resume_from_checkpoint"] = resume_from_checkpoint
            if train_error is not None:
                raise train_error
            return SimpleNamespace(metrics={"train_loss": 0.25, "eval_loss": 0.2})

    monkeypatch.setitem(
        sys.modules,
        "transformers",
        _module(
            "transformers",
            EarlyStoppingCallback=EarlyStoppingCallback,
            TrainerCallback=TrainerCallback,
        ),
    )
    monkeypatch.setitem(sys.modules, "trl", _module("trl", SFTConfig=SFTConfig, SFTTrainer=SFTTrainer))
    return calls


def test_peft_prepare_is_offline_and_keeps_train_and_validation_separate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, events = _context(tmp_path)
    backend = PeftTrlTrainingBackend()
    monkeypatch.setattr(backend, "availability", lambda: (True, None))
    calls = _install_peft_prepare_modules(monkeypatch)

    prepared = backend.prepare(context)

    model_path = str(context.model_path)
    assert calls["tokenizer"] == (
        model_path,
        {"local_files_only": True, "trust_remote_code": False},
    )
    assert calls["model"][0] == model_path
    assert calls["model"][1]["local_files_only"] is True
    assert calls["model"][1]["trust_remote_code"] is False
    assert calls["model"][1]["device_map"] is None
    assert calls["lora"] == {
        "r": 16,
        "lora_alpha": 32,
        "lora_dropout": 0.1,
        "bias": "none",
        "task_type": "CAUSAL_LM",
        "target_modules": ["q_proj", "v_proj"],
    }
    assert prepared["train_dataset"].rows == [{"text": "train-only"}]
    assert prepared["validation_dataset"].rows == [{"text": "validation-only"}]
    assert prepared["train_dataset"] is not prepared["validation_dataset"]
    assert calls["model_instance"].gradient_checkpointing_enabled is True
    assert events == [("phase", {"phase": "loading_model"})]


def test_unsloth_prepare_uses_local_qlora_parameters_and_distinct_splits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, _events = _context(tmp_path, backend="unsloth", quantization="4bit")
    backend = UnslothTrainingBackend()
    monkeypatch.setattr(backend, "availability", lambda: (True, None))
    calls = _install_unsloth_prepare_modules(monkeypatch)

    prepared = backend.prepare(context)

    assert calls["model"] == {
        "model_name": str(context.model_path),
        "max_seq_length": 384,
        "load_in_4bit": True,
        "local_files_only": True,
    }
    assert calls["lora"] == {
        "r": 16,
        "target_modules": ["q_proj", "v_proj"],
        "lora_alpha": 32,
        "lora_dropout": 0.1,
        "bias": "none",
        "use_gradient_checkpointing": "unsloth",
        "random_state": 17,
    }
    assert prepared["peft_config"] is None
    assert prepared["train_dataset"].rows == [{"text": "train-only"}]
    assert prepared["validation_dataset"].rows == [{"text": "validation-only"}]
    assert prepared["train_dataset"] is not prepared["validation_dataset"]


@pytest.mark.parametrize(
    ("backend", "peft_config"),
    [
        (PeftTrlTrainingBackend(), object()),
        (UnslothTrainingBackend(), None),
    ],
    ids=("peft_trl", "unsloth"),
)
def test_trainer_receives_schedule_splits_seed_resume_early_stopping_and_best_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: PeftTrlTrainingBackend,
    peft_config: object | None,
) -> None:
    context, _events = _context(tmp_path, backend=backend.name, resume=True)
    calls = _install_trainer_modules(monkeypatch)
    model = _FakeModel()
    tokenizer = _FakeTokenizer()
    train_dataset = object()
    validation_dataset = object()
    prepared = {
        "model": model,
        "tokenizer": tokenizer,
        "peft_config": peft_config,
        "train_dataset": train_dataset,
        "validation_dataset": validation_dataset,
    }

    trained = backend.train(context, prepared)

    assert calls["configuration"] == {
        "output_dir": str(context.checkpoint_root),
        "max_steps": 12,
        "num_train_epochs": 2.5,
        "per_device_train_batch_size": 2,
        "per_device_eval_batch_size": 3,
        "gradient_accumulation_steps": 4,
        "learning_rate": 0.0003,
        "eval_strategy": "steps",
        "eval_steps": 2,
        "save_strategy": "steps",
        "save_steps": 4,
        "logging_steps": 1,
        "load_best_model_at_end": True,
        "metric_for_best_model": "eval_loss",
        "greater_is_better": False,
        "seed": 17,
        "data_seed": 17,
        "max_seq_length": 384,
        "report_to": [],
    }
    trainer_arguments = calls["trainer"]
    assert trainer_arguments["train_dataset"] is train_dataset
    assert trainer_arguments["eval_dataset"] is validation_dataset
    assert trainer_arguments["peft_config"] is peft_config
    assert trainer_arguments["processing_class"] is tokenizer
    assert trainer_arguments["callbacks"][1].early_stopping_patience == 5
    assert calls["resume_from_checkpoint"] == str(context.resume_path)
    assert trained["train_metrics"] == {"train_loss": 0.25, "eval_loss": 0.2}

    outcome = backend.save(context, prepared, trained, {"adapter": {"eval_loss": 0.2}})
    assert outcome.best_checkpoint == context.checkpoint_root / "checkpoint-8"
    assert {path.name for path in outcome.artifacts} == {
        "adapter_model.safetensors",
        "tokenizer.json",
        "evaluation.json",
    }


def test_trainer_uses_current_trl_sequence_length_keyword(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, _events = _context(tmp_path)
    backend = PeftTrlTrainingBackend()
    calls = _install_trainer_modules(monkeypatch, sequence_length_parameter="max_length")

    backend.train(
        context,
        {
            "model": _FakeModel(),
            "tokenizer": _FakeTokenizer(),
            "peft_config": object(),
            "train_dataset": object(),
            "validation_dataset": object(),
        },
    )

    assert calls["configuration"]["max_length"] == 384
    assert "max_seq_length" not in calls["configuration"]


def test_trainer_rejects_unknown_trl_sequence_length_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, _events = _context(tmp_path)
    backend = PeftTrlTrainingBackend()
    _install_trainer_modules(monkeypatch, sequence_length_parameter="unrelated_option")

    with pytest.raises(TrainingBackendError) as error:
        backend.train(
            context,
            {
                "model": object(),
                "tokenizer": object(),
                "peft_config": object(),
                "train_dataset": object(),
                "validation_dataset": object(),
            },
        )

    assert error.value.code == "dependency_incompatible"


@pytest.mark.parametrize("backend", [PeftTrlTrainingBackend(), UnslothTrainingBackend()])
def test_unavailable_backend_uses_dependency_reason_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: PeftTrlTrainingBackend,
) -> None:
    context, _events = _context(tmp_path, backend=backend.name)
    monkeypatch.setattr(backend, "availability", lambda: (False, "missing dependency: optional-engine"))

    with pytest.raises(TrainingBackendError) as error:
        backend.prepare(context)

    assert error.value.code == "dependency_unavailable"
    assert error.value.retryable is False


@pytest.mark.parametrize(
    ("failure", "expected_code", "retryable"),
    [
        (MemoryError(), "out_of_memory", True),
        (RuntimeError("CUDA out of memory"), "out_of_memory", True),
        (ImportError("accelerate is unavailable"), "dependency_unavailable", False),
        (ValueError("invalid local model config"), "model_load_failed", False),
    ],
)
def test_peft_model_loading_uses_stable_reason_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
    expected_code: str,
    retryable: bool,
) -> None:
    context, _events = _context(tmp_path)
    backend = PeftTrlTrainingBackend()
    monkeypatch.setattr(backend, "availability", lambda: (True, None))
    _install_peft_prepare_modules(monkeypatch, load_error=failure)

    with pytest.raises(TrainingBackendError) as error:
        backend.prepare(context)

    assert error.value.code == expected_code
    assert error.value.retryable is retryable


@pytest.mark.parametrize(
    ("failure", "expected_code", "retryable"),
    [
        (MemoryError(), "out_of_memory", True),
        (RuntimeError("CUDA out of memory"), "out_of_memory", True),
        (ImportError("xformers is unavailable"), "dependency_unavailable", False),
        (ValueError("invalid local model config"), "model_load_failed", False),
    ],
)
def test_unsloth_model_loading_uses_stable_reason_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
    expected_code: str,
    retryable: bool,
) -> None:
    context, _events = _context(tmp_path, backend="unsloth")
    backend = UnslothTrainingBackend()
    monkeypatch.setattr(backend, "availability", lambda: (True, None))
    _install_unsloth_prepare_modules(monkeypatch, load_error=failure)

    with pytest.raises(TrainingBackendError) as error:
        backend.prepare(context)

    assert error.value.code == expected_code
    assert error.value.retryable is retryable


@pytest.mark.parametrize(
    ("constructor_error", "train_error", "expected_code", "retryable"),
    [
        (MemoryError(), None, "out_of_memory", True),
        (None, RuntimeError("CUDA out of memory"), "out_of_memory", True),
        (None, ImportError("optional trainer dependency is unavailable"), "dependency_unavailable", False),
        (None, ValueError("trainer rejected configuration"), "training_failed", False),
    ],
)
def test_trainer_construction_and_execution_use_stable_reason_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    constructor_error: BaseException | None,
    train_error: BaseException | None,
    expected_code: str,
    retryable: bool,
) -> None:
    context, _events = _context(tmp_path)
    backend = PeftTrlTrainingBackend()
    _install_trainer_modules(
        monkeypatch,
        constructor_error=constructor_error,
        train_error=train_error,
    )
    prepared = {
        "model": object(),
        "tokenizer": object(),
        "peft_config": object(),
        "train_dataset": object(),
        "validation_dataset": object(),
    }

    with pytest.raises(TrainingBackendError) as error:
        backend.train(context, prepared)

    assert error.value.code == expected_code
    assert error.value.retryable is retryable
