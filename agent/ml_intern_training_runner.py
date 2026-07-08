"""Local LoRA/QLoRA training runner for ml_intern training jobs.

The hub-side ``MlInternTrainingJobService`` owns validation, policy and job
dispatch. This module is intentionally a narrow worker process: it receives a
fully materialized spec file, runs one training backend, writes adapter
artifacts into the provided output directory, and exits with a process status.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RunnerError(RuntimeError):
    """Training runner failure with a user-facing message."""


@dataclass(frozen=True)
class RunnerSpec:
    job_id: str
    backend: str
    base_model: str
    dataset_path: Path
    output_dir: Path
    method: str
    load_in_4bit: bool
    lora_rank: int
    lora_alpha: int
    lora_dropout: float
    max_seq_length: int
    batch_size: int
    gradient_accumulation_steps: int
    learning_rate: float
    external_network_allowed: bool
    max_steps: int | None
    num_train_epochs: float | None
    target_modules: list[str] | None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an ml_intern LoRA/QLoRA training job.")
    parser.add_argument("--spec", required=True, help="Path to the JSON training spec.")
    args = parser.parse_args(argv)

    try:
        spec = _load_spec(Path(args.spec))
        _run(spec)
        _append_log(spec.output_dir, {"event": "runner_completed", "job_id": spec.job_id})
        return 0
    except Exception as exc:
        output_dir = _best_effort_output_dir(args.spec)
        if output_dir is not None:
            _append_log(
                output_dir,
                {
                    "event": "runner_failed",
                    "error": str(exc),
                    "traceback": traceback.format_exc(limit=8),
                },
            )
        print(str(exc), file=sys.stderr)
        return 1


def _load_spec(path: Path) -> RunnerSpec:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError(f"could not read training spec {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RunnerError("training spec must be a JSON object")

    backend = str(raw.get("backend") or "").strip().lower()
    if backend not in {"unsloth", "peft_trl"}:
        raise RunnerError(f"unsupported live training backend: {backend!r}")

    base_model = str(raw.get("base_model") or "").strip()
    if not base_model:
        raise RunnerError("base_model is required")

    dataset_path = Path(str(raw.get("dataset_path") or "")).resolve()
    if not dataset_path.exists() or not dataset_path.is_file():
        raise RunnerError(f"dataset_path does not exist: {dataset_path}")

    output_dir = Path(str(raw.get("output_dir") or "")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    return RunnerSpec(
        job_id=str(raw.get("job_id") or ""),
        backend=backend,
        base_model=base_model,
        dataset_path=dataset_path,
        output_dir=output_dir,
        method=str(raw.get("method") or "qlora").strip().lower(),
        load_in_4bit=bool(raw.get("load_in_4bit", True)),
        lora_rank=_bounded_int(raw.get("lora_rank"), default=16, low=1, high=256),
        lora_alpha=_bounded_int(raw.get("lora_alpha"), default=32, low=1, high=512),
        lora_dropout=_bounded_float(raw.get("lora_dropout"), default=0.05, low=0.0, high=0.9),
        max_seq_length=_bounded_int(raw.get("max_seq_length"), default=2048, low=128, high=32768),
        batch_size=_bounded_int(raw.get("batch_size"), default=1, low=1, high=128),
        gradient_accumulation_steps=_bounded_int(raw.get("gradient_accumulation_steps"), default=1, low=1, high=1024),
        learning_rate=_bounded_float(raw.get("learning_rate"), default=2e-4, low=1e-7, high=1.0),
        external_network_allowed=bool(raw.get("external_network_allowed", False)),
        max_steps=_optional_int(raw.get("max_steps"), low=1, high=1_000_000),
        num_train_epochs=_optional_float(raw.get("num_train_epochs"), low=0.01, high=1000.0),
        target_modules=_optional_str_list(raw.get("target_modules")),
    )


def _run(spec: RunnerSpec) -> None:
    _append_log(
        spec.output_dir,
        {
            "event": "runner_started",
            "job_id": spec.job_id,
            "backend": spec.backend,
            "base_model": spec.base_model,
            "dataset_path": str(spec.dataset_path),
        },
    )
    _write_runner_manifest(spec, status="running")
    if spec.backend == "unsloth":
        _run_unsloth(spec)
    elif spec.backend == "peft_trl":
        _run_peft_trl(spec)
    else:
        raise RunnerError(f"unsupported backend: {spec.backend}")
    _write_runner_manifest(spec, status="trained")


def _run_unsloth(spec: RunnerSpec) -> None:
    try:
        from datasets import load_dataset
        from trl import SFTTrainer
        from unsloth import FastLanguageModel
    except ImportError as exc:
        raise RunnerError(
            "unsloth backend requires installed packages: unsloth, datasets, transformers, trl"
        ) from exc

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=spec.base_model,
        max_seq_length=spec.max_seq_length,
        load_in_4bit=spec.load_in_4bit,
        local_files_only=not spec.external_network_allowed,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=spec.lora_rank,
        target_modules=spec.target_modules or _default_target_modules(),
        lora_alpha=spec.lora_alpha,
        lora_dropout=spec.lora_dropout,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=3407,
    )
    dataset = load_dataset("json", data_files=str(spec.dataset_path), split="train")
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        formatting_func=_format_records,
        max_seq_length=spec.max_seq_length,
        args=_training_args(spec),
    )
    trainer.train()
    model.save_pretrained(str(spec.output_dir))
    tokenizer.save_pretrained(str(spec.output_dir))


def _run_peft_trl(spec: RunnerSpec) -> None:
    try:
        import torch
        from datasets import load_dataset
        from peft import LoraConfig, get_peft_model
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from trl import SFTTrainer
    except ImportError as exc:
        raise RunnerError(
            "peft_trl backend requires installed packages: torch, peft, datasets, transformers, trl"
        ) from exc

    quantization_config = None
    if spec.load_in_4bit:
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
        )
    model = AutoModelForCausalLM.from_pretrained(
        spec.base_model,
        quantization_config=quantization_config,
        device_map="auto",
        local_files_only=not spec.external_network_allowed,
    )
    tokenizer = AutoTokenizer.from_pretrained(
        spec.base_model,
        local_files_only=not spec.external_network_allowed,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    peft_config = LoraConfig(
        r=spec.lora_rank,
        lora_alpha=spec.lora_alpha,
        lora_dropout=spec.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=spec.target_modules or _default_target_modules(),
    )
    model = get_peft_model(model, peft_config)
    dataset = load_dataset("json", data_files=str(spec.dataset_path), split="train")
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        formatting_func=_format_records,
        max_seq_length=spec.max_seq_length,
        args=_training_args(spec),
    )
    trainer.train()
    model.save_pretrained(str(spec.output_dir))
    tokenizer.save_pretrained(str(spec.output_dir))


def _training_args(spec: RunnerSpec) -> Any:
    from transformers import TrainingArguments

    kwargs: dict[str, Any] = {
        "output_dir": str(spec.output_dir / "trainer"),
        "per_device_train_batch_size": spec.batch_size,
        "gradient_accumulation_steps": spec.gradient_accumulation_steps,
        "learning_rate": spec.learning_rate,
        "logging_steps": 1,
        "save_strategy": "epoch",
        "report_to": [],
    }
    if spec.max_steps is not None:
        kwargs["max_steps"] = spec.max_steps
    else:
        kwargs["num_train_epochs"] = spec.num_train_epochs if spec.num_train_epochs is not None else 1.0
    return TrainingArguments(**kwargs)


def _format_records(records: dict[str, list[Any]]) -> list[str]:
    count = max((len(v) for v in records.values() if isinstance(v, list)), default=0)
    formatted: list[str] = []
    for index in range(count):
        item = {
            key: values[index]
            for key, values in records.items()
            if isinstance(values, list) and index < len(values)
        }
        formatted.append(_format_record(item))
    return formatted


def _format_record(record: dict[str, Any]) -> str:
    messages = record.get("messages")
    if isinstance(messages, list):
        parts = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user").strip()
            content = str(message.get("content") or "").strip()
            if content:
                parts.append(f"{role}: {content}")
        return "\n".join(parts)

    instruction = str(record.get("instruction") or "").strip()
    input_text = str(record.get("input") or "").strip()
    output = str(record.get("output") or record.get("response") or "").strip()
    prompt = instruction if not input_text else f"{instruction}\n\n{input_text}"
    return f"### Instruction:\n{prompt}\n\n### Response:\n{output}".strip()


def _write_runner_manifest(spec: RunnerSpec, *, status: str) -> None:
    payload = {
        "schema": "mlintern_training_runner_manifest.v1",
        "job_id": spec.job_id,
        "backend": spec.backend,
        "base_model": spec.base_model,
        "method": spec.method,
        "status": status,
        "load_in_4bit": spec.load_in_4bit,
        "lora_rank": spec.lora_rank,
        "lora_alpha": spec.lora_alpha,
        "lora_dropout": spec.lora_dropout,
        "max_seq_length": spec.max_seq_length,
        "target_modules": spec.target_modules or _default_target_modules(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (spec.output_dir / "runner_manifest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _append_log(output_dir: Path, payload: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now(timezone.utc).isoformat(), **payload}
    with (output_dir / "training_log.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _best_effort_output_dir(spec_path: str) -> Path | None:
    try:
        raw = json.loads(Path(spec_path).read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("output_dir"):
            return Path(str(raw["output_dir"]))
    except Exception:
        return None
    return None


def _default_target_modules() -> list[str]:
    return ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def _bounded_int(value: Any, *, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(parsed, high))


def _bounded_float(value: Any, *, default: float, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(parsed, high))


def _optional_int(value: Any, *, low: int, high: int) -> int | None:
    if value is None or value == "":
        return None
    return _bounded_int(value, default=low, low=low, high=high)


def _optional_float(value: Any, *, low: float, high: float) -> float | None:
    if value is None or value == "":
        return None
    return _bounded_float(value, default=low, low=low, high=high)


def _optional_str_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    items = [str(item).strip() for item in value if str(item or "").strip()]
    return items or None


if __name__ == "__main__":
    raise SystemExit(main())
