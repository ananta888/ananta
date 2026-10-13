"""Real SQL idle timer ticks preserve command preconditions without relaxing leases."""

from dataclasses import replace

import pytest

from agent.services.native_graph_orchestration_service import NativeGraphRequest
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from tests.bpmn.completion_helpers import harness as harness
from tests.bpmn.test_execution_routing import execution_plan
from tests.bpmn.test_workflow_preflight import _timer_workflow
from tests.test_native_graph_runtime import signed_control


def waiting_timer(harness):
    plan = execution_plan(_timer_workflow())
    plan = replace(plan, metadata={**plan.metadata, 'workflow_rollout_scope': {'project_id': 'synthetic-project'}})
    request = NativeGraphRequest(plan, 'synthetic-idle-timer', 'synthetic-control')
    result = harness.hub.start(request)
    for _ in range(10):
        if result.checkpoint.state.runtime_metadata.get('bpmn_waits'):
            return request, result
        result = harness.hub.advance(request)
    pytest.fail('timer activation was not armed within ten ticks')


def test_idle_sql_timer_ticks_keep_loaded_pause_binding_valid_after_restart(harness):
    request, waiting = waiting_timer(harness)
    command = signed_control(keys=harness.keys, checkpoint=waiting.checkpoint, command_type='pause',
                             step_id='__workflow__', command_id='synthetic-idle-pause')
    before = list(harness.hub.stream(request))
    for _ in range(20):
        harness.now += 0.2
        idle = harness.hub.advance(request)
        assert idle.checkpoint == waiting.checkpoint
    assert list(harness.hub.stream(request)) == before
    assert harness.queue.submissions == []
    harness.restart()
    paused = harness.hub.resume(request, command=command)
    assert paused.status == 'paused'
    assert paused.checkpoint.revision > waiting.checkpoint.revision
    assert harness.queue.submissions == []


def test_unchanged_checkpoint_never_bypasses_expired_sql_control_lease(harness):
    request, waiting = waiting_timer(harness)
    with harness.hub._run_leases.acquire(request) as lease:
        _, state, plan = harness.hub._lifecycle.load_verified(request.plan, request)
        state.control_lease = lease
        assert state.to_workflow_state(secret_refs=request.secret_refs) == waiting.checkpoint.state
        harness.now += 31
        with pytest.raises(OptimisticConcurrencyError, match='lease_stale'):
            harness.hub._lifecycle.save_checkpoint(plan, request, state)
