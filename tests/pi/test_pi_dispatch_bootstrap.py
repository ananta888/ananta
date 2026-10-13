"""Headless Hub startup requires independent deployment and policy ports."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask

from agent.bootstrap.pi_dispatch import DEPLOYMENTS_KEY, POLICY_KEY, STATUS_KEY, initialize_pi_dispatch
from agent.services.pi_dispatch_runtime import get_pi_dispatch_service
from tests.pi.test_pi_dispatch_transaction import preparation
from tests.pi.test_pi_result_completion_policy import admission as admission  # noqa: F401
from tests.pi.test_pi_run_evidence_reservation import reservation as reservation  # noqa: F401


@pytest.mark.parametrize('enabled', [False, True])
def test_missing_independent_ports_is_bounded_and_never_builds_service(enabled):
    app = Flask(__name__)
    app.config.update(ROLE='hub', ANANTA_PI_EVIDENCE_ENABLED=enabled)
    factory = Mock(side_effect=AssertionError('must not guess deployment facts'))
    result = initialize_pi_dispatch(app, service_factory=factory)
    assert result['state'] == ('blocked' if enabled else 'disabled')
    assert app.extensions[STATUS_KEY] == result
    assert get_pi_dispatch_service(app) is None
    factory.assert_not_called()


@pytest.mark.parametrize('role,enabled', [('worker', True), ('hub', 'true')])
def test_invalid_startup_scope_or_enable_flag_rejected(role, enabled):
    app = Flask(__name__)
    app.config.update(ROLE=role, ANANTA_PI_EVIDENCE_ENABLED=enabled)
    with pytest.raises(ValueError):
        initialize_pi_dispatch(app)


def test_startup_installs_concrete_service_only_from_explicit_hub_ports(reservation):
    app = Flask(__name__)
    app.config.update(ROLE='hub', ANANTA_PI_EVIDENCE_ENABLED=True)
    app.extensions[DEPLOYMENTS_KEY] = reservation.service._manifests._deployments
    app.extensions[POLICY_KEY] = SimpleNamespace(digest_for_task=lambda task: reservation.values['policy_digest'])
    service = preparation(reservation)
    factory = Mock(return_value=service)
    assert initialize_pi_dispatch(app, service_factory=factory)['state'] == 'ready'
    assert get_pi_dispatch_service(app) is service
    assert factory.call_args.kwargs['deployments'] is app.extensions[DEPLOYMENTS_KEY]
    assert factory.call_args.kwargs['policy_digest'] is app.extensions[POLICY_KEY].digest_for_task
