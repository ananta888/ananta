"""Real SQL attempt bindings and synthetic model files; no release Evidence."""

from dataclasses import asdict

import pytest

from agent.db_models import MlInternTrainingAttemptDB, MlInternTrainingJobDB
from agent.services.specialist_evaluation_receipt import SpecialistEvaluationReceipt
from agent.services.specialist_release_execution import SpecialistReleaseExecutionBindings
from ananta_contracts.specialist_decision import canonical_digest
from tests.test_specialist_runtime_candidate import candidate as _candidate


class CapturedExecutionPolicyFixture:
    """Observe exact bindings without granting production Evidence."""

    def __init__(self):
        self.bindings = None
        self.after_bindings = lambda: None

    def require_scope(self, **kwargs):
        pass

    def resolve(self, **kwargs):
        pass

    def require_execution_bindings(self, bindings):
        self.bindings = tuple(bindings)
        self.after_bindings()


@pytest.fixture
def executions(app, tmp_path, monkeypatch):
    service, registry, principal, winner, adapter = _candidate.__wrapped__(app, tmp_path, monkeypatch)
    repo = service._outcomes._repository
    report = {
        "training_job_id": winner.outcome.job_id, "adapter_id": winner.outcome.adapter_id,
        "artifact_sha256": winner.outcome.adapter_digest, "lineage": winner.lineage.to_mapping(), "evaluations": {},
    }
    jobs = []
    for role in ("validation", "holdout"):
        job, _replayed = repo.create_job(MlInternTrainingJobDB(
            tenant_id=principal.tenant_id, owner_subject=principal.subject,
            task_id=f"synthetic-{role}-task", dataset_id=winner.job.dataset_id,
            job_type="evaluate_lora", status="completed", base_model=winner.job.base_model,
            idempotency_key_digest=canonical_digest({"role": role}), request_digest="d" * 64,
            request_spec={}, result_summary={"synthetic": True, "role": role},
            adapter_id=winner.outcome.adapter_id, worker_job_id=f"synthetic-{role}-assignment",
        ))
        attempt = repo.create_attempt(MlInternTrainingAttemptDB(
            job_id=job.id, tenant_id=principal.tenant_id, owner_subject=principal.subject,
            attempt_number=1, status="completed", worker_id="synthetic-worker", worker_url="http://synthetic.invalid",
            fencing_token_digest="e" * 64, lease_expires_at=1, deadline_at=2,
        ))
        job.active_attempt_id = attempt.id
        job = repo.save_job(job, expected_version=job.version)
        report["evaluations"][role] = asdict(SpecialistEvaluationReceipt(
            job.id, job.version, attempt.id, job.dataset_id, 1, job.adapter_id,
            winner.outcome.adapter_digest, winner.outcome.base_model_digest, job.request_digest,
        ))
        jobs.append(job)
    evidence = {"source_ids": (), "run_ids": (), "job_id": jobs[-1].id, "fencing_token_digest": "e" * 64}
    policy = CapturedExecutionPolicyFixture()
    bindings = SpecialistReleaseExecutionBindings(repository=repo, evidence=policy, outcomes=service._outcomes)
    return bindings, repo, principal, winner, adapter, report, evidence, policy, jobs, registry


def test_current_sql_jobs_bind_training_validation_and_holdout_exactly(executions):
    bindings, repo, principal, winner, _adapter, report, evidence, policy, jobs, _registry = executions
    bindings.require(principal, report, evidence)
    training = repo.get_job(principal, winner.outcome.job_id)
    assert len(policy.bindings) == 3
    for actual, job in zip(policy.bindings, [training, *jobs]):
        assert actual.task_id == job.task_id
        assert actual.assignment_id == job.worker_job_id
        assert actual.dispatch_lease_id == job.active_attempt_id
        assert actual.input_digest == job.request_digest
        assert actual.result_digest == canonical_digest(job.result_summary)


@pytest.mark.parametrize("mutation", ["adapter_bytes", "job", "assignment", "attempt", "fence", "reused"])
def test_changed_execution_or_training_artifact_cannot_form_release_bindings(executions, mutation):
    bindings, repo, principal, _winner, adapter, report, evidence, policy, jobs, _registry = executions
    if mutation == "adapter_bytes":
        (adapter / "adapter_config.json").write_text('{"changed":true}')
    elif mutation == "fence":
        evidence["fencing_token_digest"] = "a" * 64
    elif mutation == "reused":
        report["evaluations"]["validation"] = dict(report["evaluations"]["holdout"])
    elif mutation == "attempt":
        attempt = repo.get_attempt(jobs[-1].active_attempt_id)
        attempt.status = "interrupted"
        repo.save_attempt(attempt, expected_version=attempt.version)
    else:
        job = jobs[-1]
        if mutation == "job":
            job.status = "running"
        else:
            job.worker_job_id = None
        repo.save_job(job, expected_version=job.version)
    with pytest.raises(ValueError):
        bindings.require(principal, report, evidence)
    assert policy.bindings is None


def test_mutation_during_evidence_verification_is_refused_at_final_sql_recheck(executions):
    bindings, repo, principal, winner, _adapter, report, evidence, policy, _jobs, _registry = executions

    def mutate():
        job = repo.get_job(principal, winner.outcome.job_id)
        job.result_summary = {"synthetic": True, "changed_after_binding": True}
        repo.save_job(job, expected_version=job.version)

    policy.after_bindings = mutate
    with pytest.raises(ValueError, match="specialist_release_execution_changed"):
        bindings.require(principal, report, evidence)
