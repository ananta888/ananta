from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agent.services.research_training_artifact_service import ResearchTrainingArtifactService
from agent.services.research_training_lineage_service import ResearchTrainingLineageService
from agent.services.research_training_run_service import ResearchTrainingDenied
from ananta_contracts.research_training import ResearchRunSpecV1, ResearchTrainingContractError
from tests.research_training.helpers import services, spec
from worker.training.research.mock_backend import DeterministicResearchMockBackend
from worker.training.research.runner import ResearchStageRunner


def test_schemas_are_closed_and_parse_examples() -> None:
    for schema_path in Path("schemas/research-training").glob("*.json"):
        schema = json.loads(schema_path.read_text())
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["additionalProperties"] is False


def test_contract_rejects_unknown_fields_and_cycles(tmp_path: Path) -> None:
    _, recipes = services(tmp_path / "state.sqlite3")
    value = spec(recipes)
    value["unknown"] = True
    with pytest.raises(ResearchTrainingContractError, match="run_spec_fields"):
        ResearchRunSpecV1.from_mapping(value)

    value = spec(recipes)
    value["pipeline"]["stages"][0]["dependencies"] = ["export"]
    with pytest.raises(ResearchTrainingContractError, match="pipeline_cycle"):
        ResearchRunSpecV1.from_mapping(value)


def test_hub_executes_delegated_dag_without_human_intervention(tmp_path: Path) -> None:
    runs, recipes = services(tmp_path / "state.sqlite3")
    run = runs.create(spec=spec(recipes), idempotency_key="automatic-test")
    replay = runs.create(spec=spec(recipes), idempotency_key="automatic-test")
    assert replay["run_id"] == run["run_id"]
    assert replay["replayed"] is True

    artifacts = ResearchTrainingArtifactService(tmp_path / "artifacts", max_artifact_bytes=1_048_576)
    lineage = ResearchTrainingLineageService(tmp_path / "lineage.sqlite3")
    runner = ResearchStageRunner(DeterministicResearchMockBackend())
    parent_digests: list[str] = []
    while run["state"] != "completed":
        claim = runs.claim_next(
            tenant_id="tenant-a", run_id=run["run_id"], worker_id="worker-a", expected_revision=run["revision"]
        )
        stage_id = claim["claimed_stage_id"]
        stage = claim["stages"][stage_id]
        result = runner.execute(
            run_spec=claim["spec"], run_id=run["run_id"], stage={
                key: stage[key]
                for key in ("stage_id", "kind", "dependencies", "required_capability", "max_attempts")
            } | {"timeout_seconds": next(
                item["timeout_seconds"] for item in claim["spec"]["pipeline"]["stages"] if item["stage_id"] == stage_id
            )},
            attempt_id=stage["attempt_id"], parent_artifact_digests=parent_digests,
        )
        receipt = artifacts.publish(manifest=result["manifest"], content=result["content"])
        lineage.register(manifest=result["manifest"], artifact_ref=receipt["artifact_ref"])
        parent_digests = [result["manifest"]["artifact_digest"]]
        run = runs.transition(
            tenant_id="tenant-a", run_id=run["run_id"], stage_id=stage_id,
            attempt_id=stage["attempt_id"], worker_authorization=claim["worker_authorization"],
            target="completed", expected_revision=claim["revision"], artifact_manifest=result["manifest"],
        )

    assert run["automatic_release_eligible"] is True
    assert run["human_intervention_required"] is False
    assert len(lineage.list_run(tenant_id="tenant-a", run_id=run["run_id"])["items"]) == 2


def test_stale_or_forged_worker_transition_is_rejected(tmp_path: Path) -> None:
    runs, recipes = services(tmp_path / "state.sqlite3")
    run = runs.create(spec=spec(recipes), idempotency_key="fencing-test")
    claim = runs.claim_next(
        tenant_id="tenant-a", run_id=run["run_id"], worker_id="worker-a", expected_revision=run["revision"]
    )
    stage = claim["stages"][claim["claimed_stage_id"]]
    with pytest.raises(ResearchTrainingDenied, match="authorization_invalid"):
        runs.transition(
            tenant_id="tenant-a", run_id=run["run_id"], stage_id=stage["stage_id"],
            attempt_id=stage["attempt_id"], worker_authorization="0" * 64, target="failed",
            expected_revision=claim["revision"], reason_code="research_synthetic_failure",
        )

    forged = copy.deepcopy(claim)
    assert forged["revision"] == 2
