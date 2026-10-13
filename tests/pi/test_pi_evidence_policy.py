"""Explicit immutable Hub preauthorization remains an additional scope restriction."""

from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlmodel import Session

from agent.db_models import TaskDB
from agent.services.pi_evidence_policy import PiEvidencePolicyPin, PinnedPiEvidencePolicy
from ananta_contracts.native_context_bundle import native_context_digest
from ananta_contracts.provider_invocation import ProviderInvocationContext
from tests.pi.test_pi_result_completion_policy import admission as admission  # noqa: F401
from tests.pi.test_pi_run_evidence_reservation import reservation as reservation  # noqa: F401


@pytest.fixture
def policy(reservation):
    command = reservation.admission.command
    context = ProviderInvocationContext.from_value(command.provider_context)
    pin = PiEvidencePolicyPin(manifest=reservation.values['manifest'], policy_version=command.policy_version,
        provider_profile_id=context.provider_profile_id, provider_id=context.selected_provider_id,
        model_id=context.selected_model_id, preauthorized_read_only=True)
    task = reservation.admission.repository.get_by_id(reservation.admission.task_id)
    deployments = Mock(require_manifest=Mock(return_value=pin.manifest))
    return SimpleNamespace(pin=pin, task=task, deployments=deployments)


def test_explicit_policy_digest_is_immutable_and_exactly_task_bound(policy):
    service = PinnedPiEvidencePolicy(pins=[policy.pin], deployments=policy.deployments)
    assert service.digest_for_task(policy.task) == policy.pin.digest
    assert service.digest_for_task(policy.task) == policy.pin.digest
    assert policy.deployments.require_manifest.call_args.kwargs['worker_id'] == policy.pin.manifest.worker_id


def test_no_artifact_policy_preserves_existing_digest(policy):
    previous = asdict(policy.pin)
    previous.pop('preauthorized_result_artifacts')
    assert policy.pin.digest == native_context_digest({'schema': 'ananta.pi-evidence-policy.v1', **previous,
        'execution_profile': 'pi-sdk-no-tools-v1', 'permission_mode': 'read_only'})


@pytest.mark.parametrize('change', ['unapproved', 'tenant', 'project', 'worker', 'version', 'deployment'])
def test_unapproved_or_foreign_task_never_gets_policy_digest(policy, change):
    pin = policy.pin
    if change == 'unapproved':
        pin = replace(pin, preauthorized_read_only=False)
    elif change == 'tenant':
        policy.task.tenant_id = 'foreign-tenant'
    elif change == 'project':
        policy.task.project_id = 'foreign-project'
    elif change == 'worker':
        policy.task.assigned_agent_url = 'http://foreign:5000'
    elif change == 'version':
        pin = replace(pin, policy_version='different-policy')
    else:
        policy.deployments.require_manifest.return_value = replace(pin.manifest, node_version='24.19.0')
    with pytest.raises(ValueError):
        PinnedPiEvidencePolicy(pins=[pin], deployments=policy.deployments).digest_for_task(policy.task)


def test_policy_defaults_to_denied_and_duplicate_scope_is_rejected(policy):
    pin = replace(policy.pin, preauthorized_read_only=False)
    with pytest.raises(ValueError, match='not_preauthorized'):
        PinnedPiEvidencePolicy(pins=[pin], deployments=policy.deployments).digest_for_task(policy.task)
    with pytest.raises(ValueError, match='duplicate'):
        PinnedPiEvidencePolicy(pins=[policy.pin, policy.pin], deployments=policy.deployments)


def test_actual_reservation_sources_bind_the_hub_policy_digest(policy, reservation):
    from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
    from tests.pi.test_pi_dispatch_transaction import assigned, preparation

    service = PinnedPiEvidencePolicy(pins=[policy.pin], deployments=policy.deployments)
    prepared = preparation(reservation, policy_digest=service.digest_for_task).prepare(
        task_id=reservation.admission.task_id, assignment=assigned(reservation))
    with Session(reservation.admission.engine) as session:
        projection = prepared.payload['evidence_assignment']
        run = session.get(HubRunEvidenceIdentityDB,
            (policy.task.tenant_id, policy.task.project_id, projection['run_id']))
        assert run.state == 'reserved' and run.synthetic is True
        for source_id in run.source_ids:
            source = session.get(HubSourceEvidenceIdentityDB, (run.tenant_id, run.project_id, source_id))
            assert source.policy_digest == policy.pin.digest
        assert session.get(TaskDB, run.task_id).status == 'in_progress'


@pytest.mark.parametrize('field', ['provider_profile_id', 'provider_id', 'model_id'])
def test_changed_provider_pin_cannot_authorize_current_task(policy, field):
    pin = replace(policy.pin, **{field: 'different-target'})
    with pytest.raises(ValueError, match='task_denied'):
        PinnedPiEvidencePolicy(pins=[pin], deployments=policy.deployments).digest_for_task(policy.task)
    policy.deployments.require_manifest.assert_not_called()


def test_unapproved_policy_rolls_back_actual_reservation_before_execution(policy, reservation):
    from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
    from tests.pi.test_pi_dispatch_transaction import assigned, preparation

    denied = PinnedPiEvidencePolicy(pins=[replace(policy.pin, preauthorized_read_only=False)],
        deployments=policy.deployments)
    with pytest.raises(ValueError, match='not_preauthorized'):
        preparation(reservation, policy_digest=denied.digest_for_task).prepare(
            task_id=reservation.admission.task_id, assignment=assigned(reservation))
    from sqlmodel import select

    with Session(reservation.admission.engine) as session:
        assert session.exec(select(HubRunEvidenceIdentityDB)).all() == []
        assert session.get(TaskDB, reservation.admission.task_id).status == 'assigned'
