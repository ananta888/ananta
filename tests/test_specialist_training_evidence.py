"""Actual Hub SQL/Registry lifecycle; all model/source/run fixtures are synthetic."""

from dataclasses import replace

import pytest

from agent.services.hub_evidence_registry_service import HubEvidenceRegistryError
from agent.services.ml_intern_training_control_service import MlInternTrainingControlService
from agent.services.specialist_dataset_binding import specialist_dataset_binding
from agent.services.specialist_training_evidence import (
    ASSIGNMENT_KEY,
    SpecialistRunPolicyContext,
    SpecialistTrainingEvidenceService,
)
from ananta_contracts.specialist_decision import canonical_digest
from tests.hub.test_hub_evidence_registry_service import service as _hub_registry
from tests.test_ml_intern_training_control_service import ForcedCancelledExecutionPort, InlineExecutor
from tests.test_specialist_training_submission import prepared as _prepared


@pytest.fixture
def lifecycle(app, tmp_path, monkeypatch):
    repo, principal, command, _queue, _root = _prepared.__wrapped__(app, tmp_path, monkeypatch)
    hub = _hub_registry.__wrapped__()
    dataset = repo.get_dataset(principal, command.dataset_id)
    origins = {
        "specialist_dataset": canonical_digest({"dataset_binding": specialist_dataset_binding(dataset)}),
        "specialist_base_model": canonical_digest({"model_id": command.base_model, "snapshot_hash": "b" * 64}),
    }
    sources = tuple(hub.register_source(
        tenant_id=principal.tenant_id, project_id="synthetic-project", origin_type=origin,
        origin_digest=origins[origin],
        content_digest=content, policy_digest="c" * 64, evidence_scope="test", synthetic=True,
    ) for origin, content in (("specialist_dataset", dataset.content_sha256), ("specialist_base_model", "b" * 64)))
    context = SpecialistRunPolicyContext(
        project_id="synthetic-project", repository_revision="1" * 40,
        execution_profile_digest="d" * 64, environment_digest="e" * 64, base_model_digest="b" * 64,
        evidence_scope="test", synthetic=True, sources=sources,
    )

    class PolicyContextFixture:
        value = context

        def read(self, observed_principal, job):
            assert observed_principal == principal
            return self.value

    policy = PolicyContextFixture()
    evidence = SpecialistTrainingEvidenceService(
        repository=repo, registry=hub, contexts=policy, model_catalog={command.base_model: {"snapshot_hash": "b" * 64}},
    )
    control = MlInternTrainingControlService(
        {"enabled": True, "artifact_root": str(tmp_path / "artifacts")}, repository=repo,
        executor=InlineExecutor(), evidence_lifecycle=evidence,
    )
    calls = []

    def execute(job, dataset_path):
        projection = job.result_summary[ASSIGNMENT_KEY]
        current = repo.get_job(principal, job.id)
        assert current.result_summary[ASSIGNMENT_KEY] == projection
        # The Registry projection is available only while its run is reserved.
        assert hub.assignment_projection(
            tenant_id=principal.tenant_id, project_id=context.project_id, run_id=projection["run_id"],
            task_id=job.task_id, assignment_id=job.worker_job_id, dispatch_lease_id=job.active_attempt_id,
            input_digest=job.request_digest, expected_binding_digest=projection["binding_digest"],
        ) == projection
        calls.append(job.id)
        return {"status": "dry_run_completed", "metrics": {}, "artifacts": []}

    monkeypatch.setattr(control, "_execute_local_bounded", execute)
    return repo, principal, command, hub, policy, evidence, control, calls


def _create(lifecycle):
    repo, principal, command, _hub, _policy, _evidence, control, _calls = lifecycle
    accepted, _replayed = control.create_job(
        principal, command.request_spec, idempotency_key="synthetic-evidence-cycle",
    )
    return repo.get_job(principal, accepted["id"])


def test_actual_control_reserves_before_execution_and_completes_exact_run(lifecycle):
    repo, principal, _command, hub, policy, evidence, _control, calls = lifecycle
    job = _create(lifecycle)
    assert job.status == "completed" and calls == [job.id]
    projection = job.result_summary[ASSIGNMENT_KEY]
    run = hub.require_run_result(
        tenant_id=principal.tenant_id, project_id=policy.value.project_id, run_id=projection["run_id"],
        task_id=job.task_id, assignment_id=job.worker_job_id, dispatch_lease_id=job.active_attempt_id,
        input_digest=job.request_digest, source_ids=projection["source_ids"],
        result_digest=canonical_digest(job.result_summary), expected_binding_digest=projection["binding_digest"],
    )
    assert run.synthetic and run.evidence_scope == "test"
    assert not hub.verify_release_binding(
        tenant_id=principal.tenant_id, project_id=policy.value.project_id, run_id=run.run_id,
        task_id=job.task_id, repository_revision=policy.value.repository_revision,
        source_ids=run.source_ids, required_scope="production",
    ).verified
    evidence.complete(principal, repo.get_job(principal, job.id))  # Exact completion replay is idempotent.
    with pytest.raises(ValueError, match="pre_execution_attempt_required"):
        evidence.reserve(principal, job, repo.get_attempt(job.active_attempt_id))


@pytest.mark.parametrize("mutation", ["inputs", "project", "source_binding", "model", "expired"])
def test_missing_or_changed_authority_prevents_any_execution(lifecycle, mutation):
    repo, _principal, _command, _hub, policy, evidence, _control, calls = lifecycle
    if mutation == "inputs":
        policy.value = replace(policy.value, sources=())
    elif mutation == "project":
        policy.value = replace(policy.value, project_id="foreign-project")
    elif mutation == "source_binding":
        policy.value = replace(policy.value, sources=(replace(policy.value.sources[0], binding_digest="f" * 64),))
    elif mutation == "model":
        policy.value = replace(policy.value, base_model_digest="f" * 64)
    else:
        evidence._clock = lambda: float("inf")
    job = _create(lifecycle)
    assert job.status == "failed" and not calls and ASSIGNMENT_KEY not in job.result_summary
    assert repo.get_attempt(job.active_attempt_id).status == "failed"


def test_registry_completion_failure_is_bounded_audited_and_not_release_evidence(lifecycle, monkeypatch):
    repo, principal, _command, hub, policy, _evidence, _control, calls = lifecycle

    def fail(**kwargs):
        raise HubEvidenceRegistryError("synthetic_registry_unavailable")

    monkeypatch.setattr(hub, "record_result", fail)
    job = _create(lifecycle)
    assert job.status == "completed" and calls == [job.id]
    events = repo.list_events(principal, job.id, after_sequence=0, limit=100)
    pending = [event for event in events if event.event_type == "evidence_completion_pending"]
    assert len(pending) == 1 and pending[0].payload["reason_code"] == "synthetic_registry_unavailable"
    projection = job.result_summary[ASSIGNMENT_KEY]
    assert not hub.verify_release_binding(
        tenant_id=principal.tenant_id, project_id=policy.value.project_id, run_id=projection["run_id"],
        task_id=job.task_id, repository_revision=policy.value.repository_revision,
        source_ids=projection["source_ids"], required_scope="production",
    ).verified


def test_changed_result_after_completion_cannot_replace_hub_identity(lifecycle):
    repo, principal, _command, _hub, _policy, evidence, _control, _calls = lifecycle
    job = _create(lifecycle)
    job.result_summary = {**job.result_summary, "changed": True}
    changed = repo.save_job(job, expected_version=job.version)
    with pytest.raises(ValueError, match="evidence_run_result_binding_mismatch"):
        evidence.complete(principal, changed)


def test_live_policy_fixture_delivers_pre_reserved_projection_to_worker_port(lifecycle, tmp_path):
    repo, principal, command, hub, policy, evidence, _control, _calls = lifecycle
    seen = []

    class SyntheticWorker(ForcedCancelledExecutionPort):
        def execute(self, *, job_id, spec, attempt_id, **kwargs):
            job = repo.get_job(principal, job_id)
            projection = spec[ASSIGNMENT_KEY]
            assert job.result_summary[ASSIGNMENT_KEY] == projection
            assert projection["dispatch_lease_id"] == attempt_id
            assert hub.assignment_projection(
                tenant_id=principal.tenant_id, project_id=policy.value.project_id, run_id=projection["run_id"],
                task_id=job.task_id, assignment_id=job.worker_job_id, dispatch_lease_id=attempt_id,
            ) == projection
            seen.append(job_id)
            return {"status": "dry_run_completed", "metrics": {}, "artifacts": []}

    control = MlInternTrainingControlService(
        {"enabled": True, "mode": "live", "base_models": [command.base_model],
         "artifact_root": str(tmp_path / "artifacts")}, repository=repo,
        executor=InlineExecutor(), execution_port=SyntheticWorker(), evidence_lifecycle=evidence,
    )
    accepted, _replayed = control.create_job(
        principal, {**command.request_spec, "mode": "live", "live_confirmed": True,
                    "risk_reason": "Explicit headless synthetic mock-worker policy fixture"},
        idempotency_key="synthetic-live-worker-projection",
    )
    job = repo.get_job(principal, accepted["id"])
    assert job.status == "completed" and seen == [job.id] and job.adapter_id is None


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
def test_failed_and_cancelled_execution_preserve_fenced_terminal_run(lifecycle, monkeypatch, terminal):
    repo, principal, _command, hub, policy, _evidence, control, _calls = lifecycle
    before = control._execute_local_bounded

    def execute(job, dataset_path):
        before(job, dataset_path)
        if terminal == "failed":
            raise RuntimeError("synthetic worker failure")
        return {"status": "cancelled", "cancelled": True, "cancel_mode": "forced"}

    monkeypatch.setattr(control, "_execute_local_bounded", execute)
    job = _create(lifecycle)
    assert job.status == terminal and repo.get_attempt(job.active_attempt_id).status == terminal
    projection = job.result_summary[ASSIGNMENT_KEY]
    run = hub.require_run_result(
        tenant_id=principal.tenant_id, project_id=policy.value.project_id, run_id=projection["run_id"],
        task_id=job.task_id, assignment_id=job.worker_job_id, dispatch_lease_id=job.active_attempt_id,
        input_digest=job.request_digest, source_ids=projection["source_ids"],
        result_digest=canonical_digest(job.result_summary), expected_binding_digest=projection["binding_digest"],
        terminal_state=terminal,
    )
    assert run.state == terminal and run.synthetic
