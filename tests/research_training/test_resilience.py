from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.research_training_capability_service import ResearchTrainingCapabilityService
from agent.services.research_training_recipe_service import ResearchTrainingRecipeService
from agent.services.research_training_run_service import ResearchTrainingDenied, ResearchTrainingRunService
from agent.services.research_training_state_store import ResearchTrainingStateStore
from agent.services.research_training_sweep_service import ResearchTrainingSweepService
from ananta_contracts.research_training import STAGE_CAPABILITIES

from .helpers import policy, recipe_request, spec


def service(path: Path, now: list[float]) -> tuple[ResearchTrainingRunService, ResearchTrainingRecipeService]:
    configured = policy()
    recipes = ResearchTrainingRecipeService(configured)
    capabilities = ResearchTrainingCapabilityService(configured)
    capabilities.report_worker(
        {
            "state": "available",
            "reason_code": None,
            "engine_version": "mock-v1",
            "capabilities": sorted(set(STAGE_CAPABILITIES.values())),
            "gpu_profiles": ["none"],
            "network_probe_performed": False,
        }
    )
    return (
        ResearchTrainingRunService(
            ResearchTrainingStateStore(path),
            policy=configured,
            capabilities=capabilities,
            recipes=recipes,
            signing_key=b"r" * 32,
            clock=lambda: now[0],
        ),
        recipes,
    )


def test_preemption_checkpoint_is_monotone_and_reclaimed_without_human(tmp_path: Path) -> None:
    now = [100.0]
    runs, recipes = service(tmp_path / "state.sqlite3", now)
    created = runs.create(spec=spec(recipes), idempotency_key="preemption-test")
    claimed = runs.claim_next(
        tenant_id="tenant-a",
        run_id=created["run_id"],
        worker_id="worker-a",
        worker_inventory_digest="1" * 64,
        expected_revision=created["revision"],
        lease_seconds=30,
    )
    first_attempt = claimed["stages"]["tokenizer"]["attempt_id"]
    preempted = runs.preempt(
        tenant_id="tenant-a",
        run_id=created["run_id"],
        stage_id="tokenizer",
        attempt_id=first_attempt,
        worker_authorization=claimed["worker_authorization"],
        checkpoint_digest="2" * 64,
        optimizer_step=7,
        expected_revision=claimed["revision"],
    )
    assert preempted["stages"]["tokenizer"]["resume_optimizer_step"] == 7
    assert preempted["stages"]["tokenizer"]["status"] == "ready"
    reclaimed = runs.claim_next(
        tenant_id="tenant-a",
        run_id=created["run_id"],
        worker_id="worker-b",
        worker_inventory_digest="3" * 64,
        expected_revision=preempted["revision"],
        lease_seconds=30,
    )
    assert reclaimed["stages"]["tokenizer"]["attempt_id"] != first_attempt
    assert reclaimed["stages"]["tokenizer"]["attempts"] == 2
    assert reclaimed["stages"]["tokenizer"]["resume_checkpoint_digest"] == "2" * 64
    assert reclaimed["human_intervention_required"] is False


def test_expired_lease_retries_but_deterministic_failure_stops(tmp_path: Path) -> None:
    now = [100.0]
    runs, recipes = service(tmp_path / "state.sqlite3", now)
    created = runs.create(spec=spec(recipes), idempotency_key="lease-test")
    claimed = runs.claim_next(
        tenant_id="tenant-a",
        run_id=created["run_id"],
        worker_id="worker-a",
        expected_revision=created["revision"],
        lease_seconds=5,
    )
    now[0] = 106.0
    with pytest.raises(ResearchTrainingDenied, match="lease_expired"):
        runs.heartbeat(
            tenant_id="tenant-a",
            run_id=created["run_id"],
            stage_id="tokenizer",
            attempt_id=claimed["stages"]["tokenizer"]["attempt_id"],
            worker_authorization=claimed["worker_authorization"],
            expected_revision=claimed["revision"],
        )
    recovered = runs.reconcile_expired(
        tenant_id="tenant-a",
        run_id=created["run_id"],
        expected_revision=claimed["revision"],
    )
    assert recovered["stages"]["tokenizer"]["status"] == "ready"
    retry = runs.claim_next(
        tenant_id="tenant-a",
        run_id=created["run_id"],
        worker_id="worker-a",
        expected_revision=recovered["revision"],
    )
    failed = runs.transition(
        tenant_id="tenant-a",
        run_id=created["run_id"],
        stage_id="tokenizer",
        attempt_id=retry["stages"]["tokenizer"]["attempt_id"],
        worker_authorization=retry["worker_authorization"],
        target="failed",
        expected_revision=retry["revision"],
        failure_class="deterministic_input",
        reason_code="research_dataset_invalid",
    )
    assert failed["state"] == "failed"
    assert failed["stages"]["tokenizer"]["status"] == "failed"
    with pytest.raises(ResearchTrainingDenied, match="not_claimable"):
        runs.claim_next(
            tenant_id="tenant-a",
            run_id=created["run_id"],
            worker_id="worker-a",
            expected_revision=failed["revision"],
        )


def test_sweep_plan_materialization_and_comparison_are_durable_and_independent(
    tmp_path: Path,
) -> None:
    runs, recipes = service(tmp_path / "runs.sqlite3", [100.0])
    template = spec(recipes)
    template.pop("recipe")
    sweeps = ResearchTrainingSweepService(
        recipes=recipes,
        runs=runs,
        state_path=tmp_path / "sweeps.sqlite3",
    )
    plan = sweeps.plan(
        recipe_request=recipe_request(),
        depths=[1, 2],
        spec_template=template,
    )
    assert plan["shared_inputs_deduplicated"] is True
    assert all(candidate["preflight"]["worker_call_performed"] is False for candidate in plan["candidates"])
    materialized = sweeps.materialize(plan=plan, idempotency_prefix="sweep-candidate")
    assert [item["state"] for item in materialized["runs"]] == ["created", "created"]
    assert len({item["run_id"] for item in materialized["runs"]}) == 2
    comparison = sweeps.compare(
        plan_digest=plan["plan_digest"],
        candidates=[
            {"candidate_index": index, "run_id": item["run_id"], "metrics": {"loss": 2.0 - index}}
            for index, item in enumerate(materialized["runs"])
        ],
    )
    assert sweeps.get(
        kind="comparison",
        record_digest=comparison["comparison_digest"],
    ) == comparison
