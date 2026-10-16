"""Closed Git grants and publication failure paths, fully headless."""
from types import SimpleNamespace

import pytest
from flask import Flask

from agent.services.self_development.publisher import VerifiedPublicationPolicy


def grant():
    policy = SimpleNamespace(assert_current=lambda: None, value={'branch': 'ananta/self-development'})
    verified = []
    return VerifiedPublicationPolicy(policy, ['tests/test_one.py'], 'test(autopilot): cover one case',
                                     lambda: verified.append(True)), verified


def test_publication_grant_is_bound_to_exact_explicit_stage_paths():
    app = Flask(__name__)
    service, verified = grant()
    with app.app_context():
        exact = service.authorize('git.stage', 'stage', target_id='self-development', approval_id=None,
            arguments={'workspace_id': 'self-development', 'paths': ['tests/test_one.py']})
        broader = service.authorize('git.stage', 'stage', target_id='self-development', approval_id=None,
            arguments={'workspace_id': 'self-development', 'paths': ['tests/test_one.py', 'agent/auth.py']})
    assert exact.allowed
    assert not broader.allowed
    assert len(verified) == 2


def test_mandatory_human_gate_prevents_automatic_publication():
    app = Flask(__name__)
    app.config['AGENT_CONFIG'] = {'approval_lifecycle': {'human_required_tools': ['git.commit']}}
    service, _ = grant()
    with app.app_context():
        decision = service.authorize('git.commit', 'commit', target_id='self-development', approval_id=None,
            arguments={'workspace_id': 'self-development', 'message': 'test(autopilot): cover one case'})
    assert not decision.allowed


def test_publication_revalidates_evidence_before_each_action():
    app = Flask(__name__)
    service, _ = grant()

    def revoked():
        raise ValueError('run_binding_revoked')

    service.verify = revoked
    with app.app_context(), pytest.raises(ValueError, match='run_binding_revoked'):
        service.authorize('git.stage', 'stage', target_id='self-development', approval_id=None,
            arguments={'workspace_id': 'self-development', 'paths': ['tests/test_one.py']})


def test_global_ops_deny_is_not_overridden():
    app = Flask(__name__)
    app.config['AGENT_CONFIG'] = {'ops': {'mutating_default': 'deny'}}
    # Ops configuration has an established dedicated namespace.
    from agent.services.ops_policy_service import OpsPolicyService
    service, _ = grant()
    with app.app_context():
        original = OpsPolicyService._config
        try:
            OpsPolicyService._config = lambda self: {'mutating_default': 'deny'}
            decision = service.authorize('git.stage', 'stage', target_id='self-development', approval_id=None,
                arguments={'workspace_id': 'self-development', 'paths': ['tests/test_one.py']})
        finally:
            OpsPolicyService._config = original
    assert not decision.allowed
