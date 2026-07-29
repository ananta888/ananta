"""Optional Unsloth strategy with the same worker-facing backend contract."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from worker.training.backends.base import TrainingBackendError, TrainingContext, TrainingOutcome
from worker.training.backends.peft_trl import PeftTrlTrainingBackend
from worker.training.backends.unsloth_checkpoint import UnslothCheckpointLifecycle
from worker.training.exports import ExportError, ExportFormat, ExportRequest, UnslothExportExecutor
from worker.training.vram_admission import VramAdmissionError, VramAdmissionPolicy


class UnslothTrainingBackend(PeftTrlTrainingBackend):
    name = "unsloth"

    def __init__(
        self,
        *,
        admission_policy: VramAdmissionPolicy | None = None,
        export_executor_factory: Callable[[Path], UnslothExportExecutor] = UnslothExportExecutor,
    ) -> None:
        self._admission_policy = admission_policy or VramAdmissionPolicy.from_environment()
        self._export_executor_factory = export_executor_factory
        self.checkpoint_lifecycle = UnslothCheckpointLifecycle(
            backend_name=self.name
        )

    def availability(self) -> tuple[bool, str | None]:
        available, detail = super().availability()
        if not available:
            return available, detail
        if importlib.util.find_spec("unsloth") is None:
            return False, "missing dependency: unsloth"
        return True, None

    def prepare(self, context: TrainingContext) -> dict[str, Any]:
        available, detail = self.availability()
        if not available:
            raise TrainingBackendError("dependency_unavailable", detail or "Unsloth is unavailable")
        context.emit("phase", {"phase": "loading_model"})
        try:
            self._assert_text_model(context.model_path)
            admission = self._admission_policy.admit(
                model_path=context.model_path,
                configuration=context.request.configuration,
            )
            context.emit("resource_admission", admission.as_event())
            from datasets import Dataset
            from unsloth import FastLanguageModel

            from worker.training.datasets import iter_jsonl

            config = context.request.configuration
            model, tokenizer = FastLanguageModel.from_pretrained(
                model_name=str(context.model_path),
                max_seq_length=config.max_sequence_length,
                load_in_4bit=config.quantization == "4bit",
                local_files_only=True,
            )
            model = FastLanguageModel.get_peft_model(
                model,
                r=config.lora_rank,
                target_modules=list(config.target_modules),
                lora_alpha=config.lora_alpha,
                lora_dropout=config.lora_dropout,
                bias="none",
                use_gradient_checkpointing="unsloth" if config.gradient_checkpointing else False,
                random_state=config.seed,
            )
            train_rows = [
                {"text": self._render_record(row, tokenizer)} for row in iter_jsonl(context.dataset.train_path)
            ]
            validation_rows = [
                {"text": self._render_record(row, tokenizer)} for row in iter_jsonl(context.dataset.validation_path)
            ]
            return {
                "model": model,
                "tokenizer": tokenizer,
                "peft_config": None,
                "train_dataset": Dataset.from_list(train_rows),
                "validation_dataset": Dataset.from_list(validation_rows),
            }
        except TrainingBackendError:
            raise
        except VramAdmissionError as exc:
            raise TrainingBackendError(exc.code, exc.message, retryable=exc.retryable) from exc
        except ImportError as exc:
            # Unsloth imports several optional accelerators lazily during
            # model construction; keep those distinct from invalid models.
            raise TrainingBackendError("dependency_unavailable", str(exc)) from exc
        except MemoryError as exc:
            raise TrainingBackendError(
                "out_of_memory", "Unsloth model preparation exhausted memory", retryable=True
            ) from exc
        except Exception as exc:
            if "out of memory" in str(exc).lower():
                raise TrainingBackendError(
                    "out_of_memory", "Unsloth model preparation exhausted memory", retryable=True
                ) from exc
            if any(token in str(exc).lower() for token in ("cuda driver", "driver version", "no kernel image")):
                raise TrainingBackendError(
                    "gpu_driver_incompatible",
                    "Unsloth model preparation rejected the installed GPU driver",
                ) from exc
            if any(token in str(exc).lower() for token in ("triton", "kernel", "flash_attn", "xformers")):
                raise TrainingBackendError(
                    "gpu_kernel_incompatible",
                    "Unsloth model preparation rejected the installed kernel stack",
                ) from exc
            raise TrainingBackendError(
                "model_load_failed", f"local Unsloth model could not be prepared: {exc}"
            ) from exc

    def train(self, context: TrainingContext, prepared: Any) -> dict[str, Any]:
        # SFTTrainer accepts a model that is already PEFT-wrapped; passing a
        # second peft_config would incorrectly attach another adapter.
        return super().train(context, prepared)

    def save(
        self,
        context: TrainingContext,
        prepared: Mapping[str, Any],
        trained: Mapping[str, Any],
        metrics: Mapping[str, Any],
    ) -> TrainingOutcome:
        outcome = super().save(context, prepared, trained, metrics)
        if not context.request.exports:
            return outcome

        trainer = trained.get("trainer")
        model = getattr(trainer, "model", None)
        if model is None:
            model = prepared.get("model")
        tokenizer = prepared.get("tokenizer")
        if model is None or tokenizer is None:
            raise TrainingBackendError(
                "export_state_unavailable",
                "trained model and tokenizer are required for post-training export",
            )

        executor = self._export_executor_factory(artifact_root=context.artifact_root)
        artifacts = list(outcome.artifacts)
        for export in context.request.exports:
            context.cancel.raise_if_cancelled()
            context.emit("phase", {"phase": "exporting"})
            destination = self._export_directory_name(
                export.format,
                export.quantization_method,
            )
            try:
                result = executor.execute(
                    model=model,
                    tokenizer=tokenizer,
                    request=ExportRequest(
                        tenant_id=context.request.tenant_scope_digest,
                        job_id=context.request.job_id,
                        attempt_id=context.request.attempt_id,
                        dataset_hash=context.request.dataset.identity_hash,
                        base_model_hash=context.request.base_model.snapshot_hash,
                        destination=destination,
                        format=ExportFormat(export.format),
                        quantization_method=export.quantization_method,
                    ),
                )
            except ExportError as exc:
                raise TrainingBackendError(exc.code, str(exc), retryable=False) from exc
            except MemoryError as exc:
                raise TrainingBackendError(
                    "out_of_memory",
                    "Unsloth post-training export exhausted memory",
                    retryable=True,
                ) from exc
            except Exception as exc:
                raise TrainingBackendError(
                    "export_failed",
                    "Unsloth post-training export failed",
                    retryable=False,
                ) from exc
            export_root = (context.artifact_root / result.destination).resolve()
            try:
                export_root.relative_to(context.artifact_root.resolve())
            except ValueError as exc:
                raise TrainingBackendError(
                    "export_destination_escape",
                    "Unsloth export result escaped the admitted artifact root",
                    retryable=False,
                ) from exc
            artifacts.extend(
                path
                for path in sorted(export_root.rglob("*"))
                if path.is_file() and not path.is_symlink()
            )
        return TrainingOutcome(
            metrics=outcome.metrics,
            artifacts=tuple(artifacts),
            best_checkpoint=outcome.best_checkpoint,
        )

    @staticmethod
    def _export_directory_name(export_format: str, quantization_method: str | None) -> str:
        if export_format == "adapter":
            return "export-adapter"
        if export_format == "merged_16bit":
            return "export-merged-16bit"
        return f"export-gguf-{str(quantization_method).replace('_', '-')}"

    @staticmethod
    def capabilities() -> dict[str, Any]:
        return {
            "schema": "ananta.unsloth-training-capability.v1",
            "backend": "unsloth",
            "modality": "text",
            "methods": ["lora", "qlora"],
            "quantization": ["none", "4bit"],
            "export_formats": ["adapter", "merged_16bit", "gguf"],
            "gguf_quantization_methods": ["q4_k_m", "q5_k_m", "q8_0"],
            "local_models_only": True,
            "trust_remote_code": False,
        }

    @staticmethod
    def _assert_text_model(model_path: Path) -> None:
        config_path = model_path / "config.json"
        try:
            if not config_path.is_file() or config_path.is_symlink() or config_path.stat().st_size > 1024 * 1024:
                raise TrainingBackendError(
                    "model_config_invalid",
                    "local text model must contain a bounded regular config.json",
                )
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except TrainingBackendError:
            raise
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise TrainingBackendError("model_config_invalid", "local model config could not be verified") from exc
        if not isinstance(raw, dict):
            raise TrainingBackendError("model_config_invalid", "local model config must be a JSON object")
        multimodal_keys = {
            "audio_config",
            "audio_token_index",
            "image_token_index",
            "vision_config",
            "vision_feature_layer",
            "vision_feature_select_strategy",
        }
        if any(key in raw for key in multimodal_keys):
            raise TrainingBackendError(
                "unsupported_model_modality",
                "the text Unsloth backend does not admit vision or audio model configurations",
            )
