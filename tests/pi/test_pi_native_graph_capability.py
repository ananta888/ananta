"""Hub recognizes Pi delegation without granting a Worker execution authority.

Synthetic policy/handler/queue fixtures exercise real Hub and Worker runtimes;
these observations are not SDK/provider or production release evidence.
"""

import pytest

from agent.services.native_graph_orchestration_service import NativeGraphRequest
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from ananta_contracts.pi_native import PI_NATIVE_CAPABILITY, PI_NATIVE_TASK_KIND
from tests.test_native_graph_runtime import runtime


def pi_plan(*, capability=PI_NATIVE_CAPABILITY):
    return ExecutionPlan.from_mapping({
        'tenant_id': 'tenant-a', 'plan_id': 'synthetic-pi-plan',
        'workflow_id': 'synthetic-pi-workflow', 'policy_version': 'policy-v1',
        'capabilities': [capability],
        'nodes': [{'id': 'pi-turn', 'task_kind': PI_NATIVE_TASK_KIND,
                   'required_capabilities': [capability]}],
        'budget': {'max_attempts': 1},
    })


def test_pi_capability_is_recognized_before_hub_delegates_a_signed_node():
    hub, queue, handler, _, _, _ = runtime()
    queue.runtime._capabilities |= frozenset({PI_NATIVE_CAPABILITY})
    plan = pi_plan()
    assert hub.validate(plan).valid
    request = NativeGraphRequest(plan, 'synthetic-pi-run', 'synthetic-control')
    result = hub.start(request)
    if result.status != 'completed':
        result = hub.advance(request)
    assert result.status == 'completed'
    assert handler.calls == ['pi-turn']
    assert len(queue.submissions) == 1
    command, = queue.submissions
    assert command.node.required_capabilities == (PI_NATIVE_CAPABILITY,)
    assert command.authorization.signature


def test_recognition_does_not_enable_pi_on_a_worker_missing_its_capability():
    hub, queue, handler, _, _, _ = runtime()
    request = NativeGraphRequest(pi_plan(), 'synthetic-pi-run', 'synthetic-control')
    result = hub.start(request)
    if result.status != 'failed':
        result = hub.advance(request)
    assert result.status == 'failed'
    assert handler.calls == []
    assert len(queue.submissions) == 1
    assert PI_NATIVE_CAPABILITY in result.reason_code


def test_worker_policy_still_denies_a_recognized_pi_capability():
    class DenyPi:
        def allow_node(self, command):
            return False, 'synthetic_pi_policy_denied'

    hub, queue, handler, _, _, _ = runtime()
    queue.runtime._capabilities |= frozenset({PI_NATIVE_CAPABILITY})
    queue.runtime._policy = DenyPi()
    request = NativeGraphRequest(pi_plan(), 'synthetic-pi-run', 'synthetic-control')
    result = hub.start(request)
    if result.status != 'failed':
        result = hub.advance(request)
    assert result.status == 'failed'
    assert handler.calls == []
    assert 'synthetic_pi_policy_denied' in result.reason_code


def test_hub_policy_can_deny_pi_before_creating_any_worker_task(monkeypatch):
    hub, queue, handler, _, _, _ = runtime()
    monkeypatch.setattr(hub._policy, 'authorize_delegation',
                        lambda **scope: (False, 'synthetic_hub_pi_policy_denied'))
    result = hub.start(NativeGraphRequest(pi_plan(), 'synthetic-pi-run', 'synthetic-control'))
    assert result.status == 'failed'
    assert 'synthetic_hub_pi_policy_denied' in result.reason_code
    assert queue.submissions == [] and handler.calls == []


def test_unknown_delegated_capability_is_rejected_before_ingestion():
    hub, queue, handler, _, _, _ = runtime()
    request = NativeGraphRequest(pi_plan(capability='unregistered.synthetic'),
                                 'synthetic-pi-run', 'synthetic-control')
    with pytest.raises(ValueError, match='native_capability_unsupported:unregistered.synthetic'):
        hub.start(request)
    assert queue.submissions == [] and handler.calls == []
