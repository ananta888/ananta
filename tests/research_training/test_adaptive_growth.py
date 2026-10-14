"""Real CPU shape, invariance, resume and bounded-compute regression tests."""
# ruff: noqa: E402 -- check optional execution dependencies before importing Worker modules

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

torch = pytest.importorskip("torch", reason="optional growth execution image dependency")
pytest.importorskip("safetensors")

from agent.services.adaptive_growth_policy import AdaptiveGrowthPolicy, GrowthSignals
from ananta_contracts.adaptive_growth import GrowthBudget, GrowthModelConfig
from worker.training.growth.checkpoint import restore, serialize
from worker.training.growth.experiment import reference_splits, run_experiment, validate_splits
from worker.training.growth.model import build_model
from worker.training.growth.trainer import TorchGrowthTrainer
from worker.training.growth.transforms import grow_depth, grow_width, migrate_adam, prune_experts, upcycle

pytestmark = pytest.mark.timeout(60)


@pytest.fixture(autouse=True)
def reproducible_torch():
    torch.set_num_threads(1)
    torch.manual_seed(7)


@pytest.mark.parametrize("transform", [grow_depth, grow_width, upcycle])
@pytest.mark.parametrize("seed", [3, 11, 23])
def test_expansion_preserves_logits_and_is_causal(transform, seed):
    torch.manual_seed(seed)
    model = build_model(GrowthModelConfig()).eval()
    expanded = transform(model).eval()
    tokens = torch.tensor([[1, 5, 8, 3, 9]])
    with torch.no_grad():
        assert torch.allclose(model(tokens), expanded(tokens), atol=2e-6)
        changed = tokens.clone()
        changed[0, -1] = 100
        assert torch.allclose(expanded(tokens)[:, :-1], expanded(changed)[:, :-1], atol=2e-6)


def train_once(model, optimizer, start=0):
    return TorchGrowthTrainer().train(
        model, optimizer, tokens=tuple(range(256)), steps=2, budget=GrowthBudget(), start_step=start
    )


@pytest.mark.parametrize("operation,transform", [("depth", grow_depth), ("width", grow_width), ("upcycle", upcycle)])
def test_adam_migration_is_trainable(operation, transform):
    model = build_model(GrowthModelConfig())
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001)
    train_once(model, optimizer)
    expanded = transform(model)
    migrated = migrate_adam(optimizer, model, expanded, operation=operation)
    assert migrated.state
    metrics = train_once(expanded, migrated, 2)
    assert metrics["steps"] == 2 and metrics["optimizer_bytes"] > 0
    if operation == "upcycle":
        assert len(metrics["expert_utilization"][0]) == 4


def test_safetensors_resume_is_exact_and_tampering_is_rejected():
    model = build_model(GrowthModelConfig())
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001)
    train_once(model, optimizer)
    checkpoint = serialize(model, optimizer, steps=2)
    digest = hashlib.sha256(checkpoint).hexdigest()
    resumed, resumed_optimizer, metadata = restore(checkpoint, expected_digest=digest)
    train_once(model, optimizer, 2)
    train_once(resumed, resumed_optimizer, metadata["steps"])
    for name, value in model.state_dict().items():
        assert torch.equal(value, resumed.state_dict()[name])
    with pytest.raises(ValueError, match="digest_mismatch"):
        restore(checkpoint + b"modified", expected_digest=digest)


def test_cloned_expert_pruning_preserves_output_and_bad_indices_fail():
    model = upcycle(build_model(GrowthModelConfig())).eval()
    pruned = prune_experts(model, keep=(1, 3)).eval()
    tokens = torch.tensor([[3, 4, 5]])
    assert torch.allclose(model(tokens), pruned(tokens), atol=1e-6)
    with pytest.raises(ValueError):
        prune_experts(model, keep=(4,))
    optimizer = torch.optim.AdamW(model.parameters())
    train_once(model, optimizer)
    pruned = prune_experts(model, keep=(1, 3))
    migrated = migrate_adam(optimizer, model, pruned, operation="prune", keep=(1, 3))
    assert train_once(pruned, migrated)["steps"] == 2


def test_unsupported_architecture_fails_before_modification():
    with pytest.raises(ValueError, match="architecture_unsupported"):
        grow_width(torch.nn.Linear(4, 4))


def test_hub_controller_is_opt_in_bounded_and_only_proposes():
    signals = GrowthSignals((1.0, 1.001, 1.0, 1.0), 0.1, (0.5, 0.5), 100, 0, 200, 100)
    assert AdaptiveGrowthPolicy().propose(signals)["reason"] == "disabled"
    policy = AdaptiveGrowthPolicy(enabled=True)
    assert policy.propose(signals)["eligible"]
    assert policy.propose(signals)["grants_permission"] is False
    for changed, reason in [
        (replace(signals, steps_since_change=1), "cooldown"),
        (replace(signals, changes=2), "change_limit"),
        (replace(signals, remaining_tokens=1), "budget_exhausted"),
        (replace(signals, expert_utilization=(0.0, 1.0)), "expert_collapse"),
    ]:
        assert policy.propose(changed)["reason"] == reason
    from agent.services.adaptive_growth_policy import GrowthRegressionPolicy

    guard = GrowthRegressionPolicy()
    assert (
        guard.assess(baseline_loss=1, candidate_loss=1.2, checkpoint_verified=True)["action"]
        == "restore_parent_checkpoint"
    )
    assert guard.assess(baseline_loss=1, candidate_loss=1.2, checkpoint_verified=False)["action"] == "blocked"


def test_comparison_is_bilingual_repeatable_and_not_production_evidence():
    report = run_experiment(splits=reference_splits(), seeds=(11, 23), steps=4)
    assert len(report["rows"]) == 28
    assert report["consumed_tokens"] <= report["budget"]["max_training_tokens"]
    assert report["production_release_evidence"] is False
    assert all(set(row["loss"]["holdout"]) == {"de", "en"} for row in report["rows"])
    for seed in (11, 23):
        rows = [r for r in report["rows"] if r["seed"] == seed and r["protocol"] == "matched_tokens"]
        assert len({r["training_tokens"] for r in rows}) == 1


def test_split_contamination_and_insufficient_budget_fail_explicitly():
    splits = reference_splits()
    splits["holdout"]["de"] = splits["train"]["de"]
    with pytest.raises(ValueError, match="contamination"):
        validate_splits(splits)
    with pytest.raises(ValueError, match="budget"):
        run_experiment(splits=reference_splits(), seeds=(11, 23), steps=4, budget=GrowthBudget(max_training_tokens=1))


def test_growth_assignment_uses_real_hub_issued_evidence_and_existing_runner(tmp_path):
    from tests.research_training.test_governed_execution import registry
    from tests.research_training.real_helpers import pipeline_spec, stage
    from agent.services.research_training_dataset_service import ResearchTrainingDatasetService
    from agent.services.research_training_evidence_service import ResearchTrainingEvidenceService
    from worker.training.research.assignment_runner import ResearchAssignmentRunner
    from worker.training.research.real_backend import LocalResearchBackend
    from worker.training.research.workspace import ResearchWorkspaceReader

    evidence = ResearchTrainingEvidenceService(registry())
    candidates = []
    for split, languages in reference_splits().items():
        path = tmp_path / (split + ".jsonl")
        records = [{"language": language, "text": text * 4} for language, texts in languages.items() for text in texts]
        path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
        candidates.append(
            {
                "origin": {"kind": "generated-fixture", "version": "growth-v1"},
                "relative_ref": path.name,
                "split": "test" if split == "holdout" else split,
                "media_type": "application_jsonl",
                "license_id": "synthetic-test",
                "consent_class": "synthetic",
            }
        )
    dataset = ResearchTrainingDatasetService(
        tmp_path, evidence=evidence, allowed_licenses=["synthetic-test"], maximum_dataset_bytes=100000
    ).admit(
        tenant_id="tenant-a",
        project_id="project-a",
        candidates=candidates,
        policy={"usage": "test-only"},
        evidence_scope="test",
    )
    definition = stage("growth", "adaptive_growth", [], "adaptive_growth")
    spec = pipeline_spec(dataset, [definition])
    spec["recipe"].update(architecture="ananta.byte-transformer-growth.v1", vocab_size=256, max_steps=1024)
    spec["recipe"]["resolved_hyperparameters"].update(hidden_size=16, attention_heads=2)
    run = {
        "tenant_id": "tenant-a",
        "run_id": "research-growth-test",
        "spec": spec,
        "stages": {"growth": {"status": "running", "attempt_id": "growth-attempt", "worker_id": "growth-worker"}},
    }
    import platform

    raw = evidence.reserve_assignment(
        run=run,
        stage_id="growth",
        task_id="growth-task",
        assignment_id="growth-assignment",
        dispatch_lease_id="growth-lease",
        dataset_manifest=dataset,
        runtime={
            "schema": "ananta.research-training-runtime.v1",
            "repository_revision": "a" * 64,
            "image_digest": "c" * 64,
            "python_version": platform.python_version(),
            "torch_version": str(torch.__version__),
            "cuda_version": str(torch.version.cuda or "none"),
            "backend_name": "ananta-local-torch",
            "backend_version": "v1",
            "hardware_profile_digest": "d" * 64,
            "deterministic_algorithms": True,
        },
        inputs=[],
        parameters={"model": {"context_length": 128}, "seeds": [11, 23], "steps": 4},
        quota_reservation_id="growth-quota",
        workspace_subdir="workspace",
        idempotency_key="growth-test",
        evidence_scope="test",
        synthetic=True,
    )
    result = ResearchAssignmentRunner(
        LocalResearchBackend(ResearchWorkspaceReader(tmp_path, maximum_input_bytes=100000))
    ).execute(raw)
    assert result["evidence_run_id"] == raw["hub_evidence"]["run_id"]
    assert result["manifest"]["artifact_kind"] == "adaptive_growth_report"
    assert result["follow_up_stage_created"] is False
    report = json.loads(result["content"])
    assert report["production_release_evidence"] is False
    from ananta_contracts.research_training import canonical_digest

    assert report["dataset_manifest_digest"] == canonical_digest(raw["dataset_manifest"])
    changed = dict(raw, dispatch_lease_id="another-lease")
    with pytest.raises(ValueError):
        ResearchAssignmentRunner(
            LocalResearchBackend(ResearchWorkspaceReader(tmp_path, maximum_input_bytes=100000))
        ).execute(changed)


def test_checkpoint_sink_persists_recoverable_lineage(tmp_path):
    from worker.training.research.checkpoint import ResearchCheckpointManager

    manager = ResearchCheckpointManager(tmp_path, max_checkpoint_bytes=1_000_000)
    receipts = []

    def sink(content, step):
        receipt = manager.publish(stage_id="growth", attempt_id="test-attempt", optimizer_step=step, content=content)
        receipts.append(receipt)
        return receipt

    report = run_experiment(splits=reference_splits(), seeds=(11, 23), steps=4, checkpoint_sink=sink)
    assert all(row["checkpoint_receipt"] for row in report["rows"])
    receipt = receipts[0]
    content = manager.read(checkpoint_ref=receipt["checkpoint_ref"], expected_digest=receipt["checkpoint_digest"])
    _, _, metadata = restore(content, expected_digest=receipt["checkpoint_digest"])
    assert metadata["steps"] == receipt["optimizer_step"]
