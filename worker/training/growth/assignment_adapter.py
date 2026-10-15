"""Reuse the ResearchAssignmentRunner's exact Hub admission/result boundary."""

from __future__ import annotations

from ananta_contracts.adaptive_growth import GrowthBudget, GrowthModelConfig
from ananta_contracts.research_training import canonical_json
from worker.training.growth.experiment import run_experiment
from worker.training.research.backend import ResearchStageOutput


def execute_growth_assignment(assignment, *, workspace, checkpoint_manager=None) -> ResearchStageOutput:
    parameters = dict(assignment.parameters)
    if set(parameters) - {"model", "budget", "seeds", "steps", "device"}:
        raise ValueError("growth_assignment_parameters_invalid")
    config = GrowthModelConfig(**parameters.get("model", {}))
    recipe = assignment.run_spec.recipe
    if (
        recipe.architecture != "ananta.byte-transformer-growth.v1"
        or recipe.vocab_size != config.vocab_size
        or recipe.depth != config.depth
        or recipe.context_length != config.context_length
        or recipe.resolved_hyperparameters.get("hidden_size", config.width) != config.width
        or recipe.resolved_hyperparameters.get("attention_heads", config.heads) != config.heads
    ):
        raise ValueError("growth_assignment_recipe_mismatch")
    if recipe.precision != "float32" or recipe.world_size != 1:
        raise ValueError("growth_assignment_precision_or_distributed_unsupported")
    budget = GrowthBudget(**parameters.get("budget", {}))
    if budget.max_seconds > assignment.stage.timeout_seconds:
        raise ValueError("growth_assignment_deadline_exceeds_lease")
    if budget.max_training_tokens > assignment.run_spec.recipe.max_steps * config.context_length:
        raise ValueError("growth_assignment_steps_exceed_recipe")
    if parameters.get("device", "cpu") == "cuda" and budget.max_seconds > assignment.run_spec.budget.gpu_hours * 3600:
        raise ValueError("growth_assignment_gpu_budget_exceeded")
    records = workspace.read_dataset(assignment.dataset_manifest)
    splits = {name: {"de": [], "en": []} for name in ("train", "validation", "holdout")}
    for split, languages in splits.items():
        # The established Research contract names the frozen holdout "test".
        for record in records.get("test" if split == "holdout" else split, []):
            language = record.get("language")
            if language not in languages or not {"text", "language"} <= set(record):
                raise ValueError("growth_assignment_language_missing")
            languages[language].append(record["text"])
    stored_bytes = 0

    def publish(content, step):
        nonlocal stored_bytes
        if stored_bytes + len(content) > assignment.run_spec.budget.storage_bytes:
            raise ValueError("growth_assignment_checkpoint_storage_exceeded")
        receipt = checkpoint_manager.publish(
            stage_id=assignment.stage.stage_id, attempt_id=assignment.attempt_id, optimizer_step=step, content=content
        )
        stored_bytes += len(content)
        return receipt

    report = run_experiment(
        splits=splits,
        config=config,
        budget=budget,
        seeds=tuple(parameters.get("seeds", (11, 23, 47))),
        steps=parameters.get("steps", 8),
        device=parameters.get("device", "cpu"),
        checkpoint_sink=publish if checkpoint_manager is not None else None,
        dataset_license_ids=tuple(sorted({shard.license_id for shard in assignment.dataset_manifest.shards})),
    )
    report["dataset_manifest_digest"] = assignment.dataset_manifest.digest
    report["classification"] = "research_technical_observation"
    content = canonical_json(report).encode()
    if len(content) + stored_bytes > assignment.run_spec.budget.storage_bytes:
        raise ValueError("growth_assignment_storage_exceeded")
    return ResearchStageOutput(
        artifact_kind="adaptive_growth_report",
        content=content,
        metrics={
            "training_tokens": float(report["consumed_tokens"]),
            "estimated_training_flops": float(report["estimated_training_flops"]),
            "comparison_arms": float(len(report["rows"])),
        },
    )
