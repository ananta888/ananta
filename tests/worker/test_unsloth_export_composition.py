from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from worker.training.backends.base import TrainingOutcome
from worker.training.backends.peft_trl import PeftTrlTrainingBackend
from worker.training.backends.unsloth import UnslothTrainingBackend
from worker.training.contracts import TrainingContractError, TrainingExportSpec


class _Cancellation:
    def raise_if_cancelled(self) -> None:
        return None


class _Tokenizer:
    def save_pretrained(self, destination: str | Path) -> None:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "tokenizer.json").write_text("{}", encoding="utf-8")


class _Model:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def save_pretrained(self, destination: str | Path) -> None:
        self.calls.append(("adapter", None))
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "adapter_model.safetensors").write_bytes(b"adapter")

    def save_pretrained_merged(
        self,
        destination: str | Path,
        tokenizer: Any,
        *,
        save_method: str,
    ) -> None:
        self.calls.append(("merged_16bit", save_method))
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "model.safetensors").write_bytes(b"merged")

    def save_pretrained_gguf(
        self,
        destination: str | Path,
        tokenizer: Any,
        *,
        quantization_method: str,
    ) -> None:
        self.calls.append(("gguf", quantization_method))
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "model.gguf").write_bytes(b"gguf")


def _context(tmp_path: Path, export: TrainingExportSpec) -> Any:
    request = SimpleNamespace(
        exports=(export,),
        tenant_scope_digest="a" * 64,
        job_id="job-1",
        attempt_id="attempt-1",
        dataset=SimpleNamespace(identity_hash="b" * 64),
        base_model=SimpleNamespace(snapshot_hash="c" * 64),
    )
    return SimpleNamespace(
        request=request,
        artifact_root=tmp_path,
        cancel=_Cancellation(),
        emit=lambda event_type, payload: None,
    )


@pytest.mark.parametrize(
    ("raw_export", "expected_call", "expected_directory"),
    [
        ({"format": "adapter"}, ("adapter", None), "export-adapter"),
        ({"format": "merged_16bit"}, ("merged_16bit", "merged_16bit"), "export-merged-16bit"),
        (
            {"format": "gguf", "quantization_method": "q4_k_m"},
            ("gguf", "q4_k_m"),
            "export-gguf-q4-k-m",
        ),
    ],
)
def test_unsloth_save_composes_atomic_executor_for_each_supported_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_export: dict[str, str],
    expected_call: tuple[str, str | None],
    expected_directory: str,
) -> None:
    base_artifact = tmp_path / "canonical-adapter.safetensors"
    base_artifact.write_bytes(b"canonical")
    monkeypatch.setattr(
        PeftTrlTrainingBackend,
        "save",
        lambda self, context, prepared, trained, metrics: TrainingOutcome(
            metrics=metrics,
            artifacts=(base_artifact,),
        ),
    )
    model = _Model()
    tokenizer = _Tokenizer()
    backend = UnslothTrainingBackend(admission_policy=object())

    outcome = backend.save(
        _context(tmp_path, TrainingExportSpec.from_mapping(raw_export)),
        {"model": model, "tokenizer": tokenizer},
        {"trainer": SimpleNamespace(model=model)},
        {"adapter": {"eval_loss": 0.1}},
    )

    assert model.calls == [expected_call]
    assert base_artifact in outcome.artifacts
    assert tmp_path / expected_directory / "ananta-export-manifest.json" in outcome.artifacts


@pytest.mark.parametrize(
    "raw_export",
    [
        {"format": "unknown"},
        {"format": "adapter", "quantization_method": "q4_k_m"},
        {"format": "gguf"},
        {"format": "gguf", "quantization_method": "unsupported"},
        {"format": "adapter", "destination": "/tmp/escape"},
    ],
)
def test_training_export_contract_rejects_ambiguous_or_path_bearing_specs(
    raw_export: dict[str, str],
) -> None:
    with pytest.raises(TrainingContractError):
        TrainingExportSpec.from_mapping(raw_export)
