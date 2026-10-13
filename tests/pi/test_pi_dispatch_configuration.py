"""Closed Hub configuration creates actual deployment/policy and SQL preparation."""

import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask
from sqlmodel import Session, select

from agent.bootstrap.pi_dispatch import initialize_pi_dispatch
from agent.bootstrap.pi_dispatch_configuration import compose_pi_dispatch_ports
from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
from agent.services.pi_dispatch_runtime import get_pi_dispatch_service
from agent.services.pi_runtime_deployments import PiRuntimeObservation
from ananta_contracts.provider_invocation import ProviderInvocationContext
from tests.pi.test_pi_dispatch_transaction import assigned, preparation
from tests.pi.test_pi_result_completion_policy import admission as admission  # noqa: F401
from tests.pi.test_pi_run_evidence_reservation import reservation as reservation  # noqa: F401


@pytest.fixture
def configured(reservation):
    manifest = reservation.values['manifest']
    command = reservation.admission.command
    context = ProviderInvocationContext.from_value(command.provider_context)
    raw = [{'manifest': manifest.to_dict(), 'container_id': 'c' * 64, 'node_binary': '/opt/node/bin/node',
        'package_json': '/opt/pi/node_modules/@earendil-works/pi-coding-agent/package.json', 'user_id': '1000:1000',
        'policy': {'policy_version': command.policy_version, 'provider_profile_id': context.provider_profile_id,
            'provider_id': context.selected_provider_id, 'model_id': context.selected_model_id,
            'preauthorized_read_only': True}}]
    observation = PiRuntimeObservation(container_id='c' * 64, image_digest=manifest.image_digest,
        repository_revision=manifest.repository_revision, node_version=manifest.node_version,
        pi_version=manifest.pi_version, running=True, readonly_rootfs=True)
    return SimpleNamespace(raw=raw, observer=Mock(observe=Mock(return_value=observation)))


def test_normal_hub_config_composes_concrete_ports_and_real_atomic_reservation(configured, reservation):
    app = Flask(__name__)
    app.config.update(ROLE='hub', AGENT_CONFIG={'pi_evidence': {'enabled': True, 'bindings': configured.raw}})
    def factory(**ports):
        return preparation(reservation, **ports)

    status = initialize_pi_dispatch(app, observer_factory=lambda: configured.observer, service_factory=factory)
    assert status['state'] == 'ready'
    configured.observer.observe.assert_not_called()
    service = get_pi_dispatch_service(app)
    prepared = service.prepare(task_id=reservation.admission.task_id, assignment=assigned(reservation))
    assert configured.observer.observe.call_count >= 1
    with Session(reservation.admission.engine) as session:
        run = session.exec(select(HubRunEvidenceIdentityDB)).one()
        assert run.run_id == prepared.payload['evidence_assignment']['run_id']
        assert run.state == 'reserved' and run.synthetic is True


@pytest.mark.parametrize('change', ['unknown', 'missing', 'string_approval', 'string_artifact_approval',
                                  'duplicate', 'relative_path', 'scope'])
def test_malformed_or_escalated_inventory_never_builds_ports(configured, change):
    raw = copy.deepcopy(configured.raw)
    if change == 'unknown':
        raw[0]['credential'] = 'must-not-be-admitted'
    elif change == 'missing':
        raw[0].pop('container_id')
    elif change == 'string_approval':
        raw[0]['policy']['preauthorized_read_only'] = 'true'
    elif change == 'string_artifact_approval':
        raw[0]['policy']['preauthorized_result_artifacts'] = 'true'
    elif change == 'duplicate':
        raw.append(copy.deepcopy(raw[0]))
    elif change == 'relative_path':
        raw[0]['node_binary'] = '../node'
    else:
        raw[0]['manifest']['evidence_scope'] = 'production'
    with pytest.raises(ValueError, match='configuration_invalid'):
        compose_pi_dispatch_ports(raw, observer=configured.observer)
    configured.observer.observe.assert_not_called()


def test_unactivated_configuration_never_creates_docker_observer(configured):
    app = Flask(__name__)
    app.config.update(ROLE='hub', AGENT_CONFIG={'pi_evidence': {'enabled': False, 'bindings': configured.raw}})
    observer = Mock(side_effect=AssertionError('disabled bootstrap must not create observer'))
    assert initialize_pi_dispatch(app, observer_factory=observer)['state'] == 'disabled'
    observer.assert_not_called()
