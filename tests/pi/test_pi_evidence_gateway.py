"""Registered Worker gateway ownership checks precede the Pi evidence port."""

from unittest.mock import Mock

import pytest

from agent.services.workflow_worker_gateway_service import WorkflowWorkerGatewayError
from tests.workflow.test_workflow_worker_gateway_service import fixture


@pytest.mark.parametrize('change', [
    'exact', 'anonymous', 'foreign_worker', 'foreign_task', 'stale_fence', 'unavailable'])
def test_pi_gateway_requires_registered_exact_owner_before_read(change):
    reader = Mock(return_value={'technical_test_callback': True})
    service, base, *_ = fixture(pi_evidence=None if change == 'unavailable' else reader)
    raw = {**base, 'command': 'pi_evidence_read', 'hub_task_id': 'hub-task-1',
        'command_id': 'test-command', 'input_digest': 'a' * 64}
    identity = {'authenticated_worker_id': 'worker-1', 'authenticated_worker_url': 'http://worker-1:5000'}
    if change == 'anonymous':
        identity = {}
    elif change == 'foreign_worker':
        identity['authenticated_worker_id'] = 'worker-2'
    elif change == 'foreign_task':
        raw['hub_task_id'] = 'foreign-task'
    elif change == 'stale_fence':
        raw['fencing_token'] += 1
    if change == 'exact':
        assert service.execute(raw, **identity) == {'technical_test_callback': True}
        assert reader.call_args.kwargs['expected_worker_id'] == 'worker-1'
        assert reader.call_args.kwargs['expected_workflow_id'] == base['binding']['workflow_id']
    else:
        with pytest.raises(WorkflowWorkerGatewayError):
            service.execute(raw, **identity)
        reader.assert_not_called()


def test_reserved_context_gateway_uses_only_authenticated_callback_and_records_safe_event():
    from ananta_contracts.native_context_bundle import NativeApprovedContext, NativeContextBundleProjection

    projection = NativeContextBundleProjection.create(hub_task_id='hub-task-1', bundle_id='test-bundle',
        command={'technical': 'gateway-callback-test'},
        approved=NativeApprovedContext(content='synthetic approved context', policy_digest='b'*64))
    callback = Mock(return_value=projection.to_dict())
    service, base, *_ = fixture(pi_contexts=callback)
    raw = {**base, 'command': 'native_context_read', 'hub_task_id': 'hub-task-1', 'command_id': 'test-command'}
    identity = dict(authenticated_worker_id='worker-1', authenticated_worker_url='http://worker-1:5000')
    assert service.execute(raw, **identity) == projection.to_dict()
    assert callback.call_args.kwargs['expected_worker_id'] == 'worker-1'
    assert callback.call_args.kwargs['expected_workflow_id'] == base['binding']['workflow_id']
    callback.reset_mock()
    with pytest.raises(WorkflowWorkerGatewayError):
        service.execute({**raw, 'hub_task_id': 'foreign-task'}, **identity)
    callback.assert_not_called()
