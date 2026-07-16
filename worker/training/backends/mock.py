"""Deterministic no-ML backend for CI, API tests and operational smoke tests."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from worker.training.backends.base import TrainingContext, TrainingOutcome


class MockTrainingBackend:
    name = "mock"

    def availability(self) -> tuple[bool, str | None]:
        return True, None

    def prepare(self, context: TrainingContext) -> dict[str, Any]:
        if not context.model_path.exists():
            raise FileNotFoundError(context.model_path)
        context.emit("phase", {"phase": "preparing"})
        return {"seed": context.request.configuration.seed}

    def train(self, context: TrainingContext, prepared: Any) -> dict[str, Any]:
        config = context.request.configuration
        best_loss = float("inf")
        best_step = 0
        stale_evaluations = 0
        last_step = 0
        for step in range(1, config.max_steps + 1):
            context.cancel.raise_if_cancelled()
            loss = round(1.0 / (step + 1), 8)
            payload: dict[str, Any] = {
                "step": step,
                "max_steps": config.max_steps,
                "epoch": round(step / config.max_steps * config.num_train_epochs, 8),
                "loss": loss,
                "learning_rate": config.learning_rate * (1.0 - (step / config.max_steps)),
            }
            if step % config.eval_steps == 0 or step == config.max_steps:
                eval_loss = round(loss + 0.05, 8)
                payload["eval_loss"] = eval_loss
                if eval_loss < best_loss:
                    best_loss = eval_loss
                    best_step = step
                    stale_evaluations = 0
                else:
                    stale_evaluations += 1
            context.emit("progress", payload)
            last_step = step
            if step % config.save_steps == 0 or step == config.max_steps:
                checkpoint = context.checkpoint_root / f"checkpoint-{step}.json"
                checkpoint.parent.mkdir(parents=True, exist_ok=True)
                checkpoint.write_text(
                    json.dumps({"step": step, "loss": loss, "seed": prepared["seed"]}, sort_keys=True),
                    encoding="utf-8",
                )
                context.emit("checkpoint", {"step": step, "name": checkpoint.name})
            if config.early_stopping_patience and stale_evaluations >= config.early_stopping_patience:
                context.emit("phase", {"phase": "early_stopped", "step": step})
                break
        return {"last_step": last_step, "best_step": best_step or last_step, "best_eval_loss": best_loss}

    def evaluate(self, context: TrainingContext, prepared: Any, trained: Any) -> Mapping[str, Any]:
        context.emit("phase", {"phase": "evaluating"})
        base_loss = 1.0
        adapter_loss = float(trained["best_eval_loss"])
        if adapter_loss == float("inf"):
            adapter_loss = round(1.0 / (int(trained["last_step"]) + 1) + 0.05, 8)
        return {
            "validation_records": context.dataset.validation_records,
            "base": {"eval_loss": base_loss},
            "adapter": {"eval_loss": adapter_loss},
            "delta": {"eval_loss": round(adapter_loss - base_loss, 8)},
            "best_step": trained["best_step"],
        }

    def save(
        self,
        context: TrainingContext,
        prepared: Any,
        trained: Any,
        metrics: Mapping[str, Any],
    ) -> TrainingOutcome:
        context.emit("phase", {"phase": "saving"})
        context.artifact_root.mkdir(parents=True, exist_ok=True)
        adapter_config = context.artifact_root / "adapter_config.json"
        adapter_weights = context.artifact_root / "adapter_model.safetensors"
        metrics_file = context.artifact_root / "evaluation.json"
        adapter_config.write_text(
            json.dumps(
                {
                    "base_model_name_or_path": context.request.base_model.model_id,
                    "r": context.request.configuration.lora_rank,
                    "lora_alpha": context.request.configuration.lora_alpha,
                    "lora_dropout": context.request.configuration.lora_dropout,
                    "mock": True,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        deterministic = hashlib.sha256(context.request.request_hash.encode("ascii")).digest()
        header = json.dumps(
            {
                "mock_lora.weight": {
                    "dtype": "U8",
                    "shape": [len(deterministic)],
                    "data_offsets": [0, len(deterministic)],
                }
            },
            separators=(",", ":"),
        ).encode("utf-8")
        adapter_weights.write_bytes(len(header).to_bytes(8, "little") + header + deterministic)
        metrics_file.write_text(json.dumps(dict(metrics), sort_keys=True), encoding="utf-8")
        best_checkpoint = context.checkpoint_root / f"checkpoint-{trained['best_step']}.json"
        return TrainingOutcome(
            metrics=metrics,
            artifacts=(adapter_config, adapter_weights, metrics_file),
            best_checkpoint=best_checkpoint if best_checkpoint.exists() else None,
        )
