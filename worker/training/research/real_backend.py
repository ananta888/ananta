"""Real, bounded research-stage adapters for local CPU/CUDA Workers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ananta_contracts.research_training import canonical_json
from ananta_contracts.research_training_execution import ResearchStageAssignmentV1
from ananta_contracts.research_training_rl import ResearchRlConfigV1
from worker.training.research.backend import ResearchStageOutput
from worker.training.research.checkpoint import ResearchCheckpointManager
from worker.training.research.inference_benchmark import TorchInferenceBenchmark
from worker.training.research.modeling import load_checkpoint, serialize_portable_checkpoint
from worker.training.research.preemption import (
    PreemptionController,
    ResearchStagePreempted,
    ResearchTrainingPreempted,
)
from worker.training.research.reward import ExactMatchReward, NumericToleranceReward
from worker.training.research.rl_engine import TorchRlEngine
from worker.training.research.text_data import record_text
from worker.training.research.training_engine import (
    TorchLanguageModelEvaluator,
    TorchLanguageModelTrainer,
    config_from_recipe,
)
from worker.training.research.workspace import ResearchWorkspaceReader
from worker.training.tokenizers.byte_bpe import ByteBpeTokenizer, ByteBpeTrainer
from worker.training.tokenizers.evaluation import TokenizerEvaluator

_CAPABILITIES = frozenset(
    {
        "tokenizer_training",
        "tokenizer_evaluation",
        "full_weight_training",
        "model_evaluation",
        "rl_training",
        "inference_benchmark",
        "model_export",
    }
)


class LocalResearchBackend:
    """Dispatch one already-authorized stage to a focused execution adapter."""

    def __init__(
        self,
        workspace: ResearchWorkspaceReader,
        *,
        checkpoint_manager: ResearchCheckpointManager | None = None,
        preemption: PreemptionController | None = None,
    ) -> None:
        self._workspace = workspace
        self._tokenizer_trainer = ByteBpeTrainer()
        self._tokenizer_evaluator = TokenizerEvaluator()
        self._trainer = TorchLanguageModelTrainer()
        self._evaluator = TorchLanguageModelEvaluator()
        self._benchmark = TorchInferenceBenchmark()
        self._rl = TorchRlEngine()
        self._checkpoint_manager = checkpoint_manager
        self._preemption = preemption

    @property
    def capabilities(self) -> frozenset[str]:
        return _CAPABILITIES

    def execute(self, assignment: ResearchStageAssignmentV1) -> ResearchStageOutput:
        handlers = {
            "tokenizer_train": self._tokenizer_train,
            "tokenizer_eval": self._tokenizer_eval,
            "pretrain": lambda value: self._train(value, supervised=False),
            "base_eval": self._model_eval,
            "sft": lambda value: self._train(value, supervised=True),
            "chat_eval": self._model_eval,
            "rl": self._rl_train,
            "rl_eval": self._model_eval,
            "inference_benchmark": self._inference_benchmark,
            "export": self._export,
        }
        return handlers[assignment.stage.kind](assignment)

    def _tokenizer_train(self, assignment: ResearchStageAssignmentV1) -> ResearchStageOutput:
        records = self._workspace.read_dataset(assignment.dataset_manifest)["train"]
        texts = [record_text(record) for record in records]
        raw_specials = assignment.parameters.get(
            "special_tokens",
            [
                "<system>",
                "</system>",
                "<user>",
                "</user>",
                "<assistant>",
                "</assistant>",
                "<tool>",
                "</tool>",
            ],
        )
        if not isinstance(raw_specials, Sequence) or isinstance(raw_specials, (str, bytes)):
            raise ValueError("research_tokenizer_special_tokens_invalid")
        tokenizer = self._tokenizer_trainer.train(
            texts,
            vocab_size=assignment.run_spec.recipe.vocab_size,
            special_tokens=[str(item) for item in raw_specials],
        )
        content = tokenizer.serialize()
        manifest = tokenizer.manifest(
            tokenizer_id=f"tokenizer-{assignment.run_spec.recipe.recipe_id}",
            dataset_manifest_digest=assignment.dataset_manifest.digest,
        )
        return ResearchStageOutput(
            artifact_kind="tokenizer",
            content=content,
            metrics={
                "vocab_size": float(tokenizer.vocab_size),
                "merges": float(len(tokenizer.merges)),
                "artifact_manifest_bytes": float(len(canonical_json(manifest.to_dict()))),
            },
        )

    def _tokenizer_eval(self, assignment: ResearchStageAssignmentV1) -> ResearchStageOutput:
        tokenizer = self._tokenizer(assignment)
        records = self._workspace.read_dataset(assignment.dataset_manifest)
        report = self._tokenizer_evaluator.evaluate(
            tokenizer,
            splits={name: [record_text(item) for item in values] for name, values in records.items() if values},
        )
        return ResearchStageOutput(
            artifact_kind="tokenizer_report",
            content=canonical_json(report).encode(),
            metrics={
                "bytes_per_token": float(report["bytes_per_token"]),
                "characters_per_token": float(report["characters_per_token"]),
                "unknown_token_rate": float(report["unknown_token_rate"]),
                "special_token_rate": float(report["special_token_rate"]),
            },
        )

    def _train(self, assignment: ResearchStageAssignmentV1, *, supervised: bool) -> ResearchStageOutput:
        tokenizer = self._tokenizer(assignment)
        config = config_from_recipe(assignment.run_spec.recipe.to_dict(), tokenizer_vocab_size=tokenizer.vocab_size)
        parent_model = None
        resume_kind = "sft_checkpoint" if supervised else "base_checkpoint"
        resume_inputs = [item for item in assignment.inputs if item.artifact_kind == resume_kind]
        if resume_inputs:
            if len(resume_inputs) != 1:
                raise ValueError("research_resume_checkpoint_input_invalid")
            parent_model, parent_config = load_checkpoint(self._workspace.read_artifact(resume_inputs[0]))
            if parent_config != config:
                raise ValueError("research_resume_parent_config_mismatch")
        elif supervised:
            parent_model, parent_config = load_checkpoint(self._input(assignment, "base_checkpoint"))
            if parent_config != config:
                raise ValueError("research_sft_parent_config_mismatch")
        records = self._workspace.read_dataset(assignment.dataset_manifest)["train"]
        resolved = assignment.run_spec.recipe.resolved_hyperparameters
        kind = "sft_checkpoint" if supervised else "base_checkpoint"
        latest_checkpoint: dict[str, Any] | None = None

        def checkpoint(model: Any, optimizer_step: int) -> None:
            nonlocal latest_checkpoint
            if self._checkpoint_manager is None:
                raise RuntimeError("research_checkpoint_manager_unavailable")
            content = serialize_portable_checkpoint(
                model,
                config,
                metadata={
                    "artifact_kind": kind,
                    "stage_id": assignment.stage.stage_id,
                    "optimizer_steps": str(optimizer_step),
                    "preemption_checkpoint": "true",
                },
            )
            latest_checkpoint = self._checkpoint_manager.publish(
                stage_id=assignment.stage.stage_id,
                attempt_id=assignment.attempt_id,
                optimizer_step=optimizer_step,
                content=content,
            )

        try:
            outcome = self._trainer.train(
                records=records,
                tokenizer=tokenizer,
                config=config,
                max_steps=assignment.run_spec.recipe.max_steps,
                seed=assignment.run_spec.recipe.seed,
                learning_rate=float(resolved["learning_rate"]),
                weight_decay=float(resolved["weight_decay"]),
                gradient_clip=float(assignment.parameters.get("gradient_clip", 1.0)),
                precision=assignment.run_spec.recipe.precision,
                parent_model=parent_model,
                supervised=supervised,
                checkpoint_cadence=int(assignment.parameters.get("checkpoint_cadence", 0)),
                checkpoint_callback=checkpoint if self._checkpoint_manager is not None else None,
                preemption_requested=(lambda: self._preemption.requested) if self._preemption else None,
                initial_step=int(assignment.parameters.get("resume_optimizer_step", 0)),
            )
        except ResearchTrainingPreempted as exc:
            if latest_checkpoint is None:
                raise RuntimeError("research_preemption_checkpoint_missing") from exc
            raise ResearchStagePreempted(latest_checkpoint) from exc
        except RuntimeError as exc:
            self._raise_controlled_oom(exc)
            raise
        content = serialize_portable_checkpoint(
            outcome.model,
            config,
            metadata={
                "artifact_kind": kind,
                "stage_id": assignment.stage.stage_id,
                "recipe_digest": assignment.run_spec.recipe.digest,
                "dataset_digest": assignment.dataset_manifest.digest,
                "runtime_digest": assignment.runtime.digest,
                "optimizer_steps": str(outcome.optimizer_steps),
            },
        )
        return ResearchStageOutput(artifact_kind=kind, content=content, metrics=outcome.metrics)

    def _model_eval(self, assignment: ResearchStageAssignmentV1) -> ResearchStageOutput:
        tokenizer = self._tokenizer(assignment)
        checkpoint_kind = {
            "base_eval": "base_checkpoint",
            "chat_eval": "sft_checkpoint",
            "rl_eval": "rl_checkpoint",
        }[assignment.stage.kind]
        model, config = load_checkpoint(self._input(assignment, checkpoint_kind))
        split_records = self._workspace.read_dataset(assignment.dataset_manifest)
        records = split_records["validation"] or split_records["test"] or split_records["train"]
        metrics = self._evaluator.evaluate(
            model=model,
            config=config,
            tokenizer=tokenizer,
            records=records,
        )
        report = {
            "schema": "ananta.research-training-model-evaluation.v1",
            "stage_kind": assignment.stage.kind,
            "dataset_manifest_digest": assignment.dataset_manifest.digest,
            "checkpoint_digest": self._input_descriptor(assignment, checkpoint_kind).artifact_digest,
            "metrics": metrics,
            "samples_redacted": True,
        }
        artifact_kind = {
            "base_eval": "base_evaluation",
            "chat_eval": "chat_evaluation",
            "rl_eval": "rl_evaluation",
        }[assignment.stage.kind]
        return ResearchStageOutput(
            artifact_kind=artifact_kind,
            content=canonical_json(report).encode(),
            metrics=metrics,
        )

    def _rl_train(self, assignment: ResearchStageAssignmentV1) -> ResearchStageOutput:
        tokenizer = self._tokenizer(assignment)
        model, config = load_checkpoint(self._input(assignment, "sft_checkpoint"))
        raw_config = assignment.parameters.get("rl_config")
        if not isinstance(raw_config, Mapping):
            raise ValueError("research_rl_config_required")
        rl_config = ResearchRlConfigV1.from_mapping(raw_config)
        if rl_config.reward.provider == "exact_match_v1":
            provider = ExactMatchReward(case_sensitive=True)
        elif rl_config.reward.provider == "numeric_tolerance_v1":
            provider = NumericToleranceReward(
                tolerance=float(assignment.parameters.get("reward_tolerance", 0.0))
            )
        else:
            raise ValueError("research_reward_provider_unsupported")
        records = self._workspace.read_dataset(assignment.dataset_manifest)["train"]
        try:
            metrics = self._rl.train(
                model=model,
                model_config=config,
                tokenizer=tokenizer,
                records=records,
                config=rl_config,
                reward_provider=provider,
            )
        except RuntimeError as exc:
            self._raise_controlled_oom(exc)
            raise
        content = serialize_portable_checkpoint(
            model,
            config,
            metadata={
                "artifact_kind": "rl_checkpoint",
                "stage_id": assignment.stage.stage_id,
                "reward_digest": rl_config.reward.digest,
                "rollouts_redacted": str(rl_config.reward.redact_rollouts).lower(),
                "runtime_digest": assignment.runtime.digest,
            },
        )
        return ResearchStageOutput(artifact_kind="rl_checkpoint", content=content, metrics=metrics)

    def _inference_benchmark(self, assignment: ResearchStageAssignmentV1) -> ResearchStageOutput:
        tokenizer = self._tokenizer(assignment)
        checkpoint = next(
            (
                kind
                for kind in ("rl_checkpoint", "sft_checkpoint", "base_checkpoint")
                if any(item.artifact_kind == kind for item in assignment.inputs)
            ),
            None,
        )
        if checkpoint is None:
            raise ValueError("research_inference_checkpoint_missing")
        model, config = load_checkpoint(self._input(assignment, checkpoint))
        raw_prompts = assignment.parameters.get("prompts", ["Hello"])
        if not isinstance(raw_prompts, Sequence) or isinstance(raw_prompts, (str, bytes)):
            raise ValueError("research_inference_prompts_invalid")
        report = self._benchmark.run(
            model=model,
            config=config,
            tokenizer=tokenizer,
            prompts=[str(item) for item in raw_prompts],
            repetitions=int(assignment.parameters.get("repetitions", 3)),
            maximum_new_tokens=int(assignment.parameters.get("maximum_new_tokens", 8)),
            runtime_digest=assignment.runtime.digest,
            hardware_digest=assignment.runtime.hardware_profile_digest,
        )
        metrics = {
            key: float(value)
            for key, value in report.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        return ResearchStageOutput(
            artifact_kind="inference_benchmark",
            content=canonical_json(report).encode(),
            metrics=metrics,
        )

    def _export(self, assignment: ResearchStageAssignmentV1) -> ResearchStageOutput:
        candidates = [item for item in assignment.inputs if item.artifact_kind.endswith("_checkpoint")]
        if len(candidates) != 1:
            raise ValueError("research_export_checkpoint_invalid")
        source_content = self._workspace.read_artifact(candidates[0])
        model, config = load_checkpoint(source_content)  # strict safe format/config validation
        content = serialize_portable_checkpoint(
            model,
            config,
            metadata={
                "artifact_kind": "model_export",
                "source_checkpoint_digest": candidates[0].artifact_digest,
                "runtime_digest": assignment.runtime.digest,
            },
        )
        return ResearchStageOutput(
            artifact_kind="model_export",
            content=content,
            metrics={"export_size_bytes": float(len(content)), "safe_tensor_format": 1.0},
            executable=False,
        )

    def _tokenizer(self, assignment: ResearchStageAssignmentV1) -> ByteBpeTokenizer:
        descriptor = self._input_descriptor(assignment, "tokenizer")
        return ByteBpeTokenizer.from_bytes(
            self._workspace.read_artifact(descriptor), expected_digest=descriptor.artifact_digest
        )

    def _input(self, assignment: ResearchStageAssignmentV1, kind: str) -> bytes:
        return self._workspace.read_artifact(self._input_descriptor(assignment, kind))

    @staticmethod
    def _input_descriptor(assignment: ResearchStageAssignmentV1, kind: str):
        matches = [item for item in assignment.inputs if item.artifact_kind == kind]
        if len(matches) != 1:
            raise ValueError(f"research_{kind}_input_invalid")
        return matches[0]

    @staticmethod
    def _raise_controlled_oom(error: RuntimeError) -> None:
        if error.__class__.__name__ == "OutOfMemoryError" or "out of memory" in str(error).lower():
            raise ValueError("research_training_oom_controlled_smaller_profile_required") from error


__all__ = ["LocalResearchBackend"]
