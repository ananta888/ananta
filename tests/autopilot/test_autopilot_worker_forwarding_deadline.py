"""Propose forwards carry a bounded transport deadline (no tick-blocking retry)."""
from agent.routes.tasks.autopilot_worker_forwarding import AutopilotWorkerForwarder
from agent.routes.tasks.utils import _step_deadline_monotonic, _step_forward_deadline_seconds


def test_step_forward_deadline_seconds_uses_phase_timeouts():
    cfg = {'autopilot_security_policies': {'balanced': {'propose_timeout': 240, 'execute_timeout': 120}}}
    assert _step_forward_deadline_seconds('/tasks/T/step/propose', cfg) == 240
    assert _step_forward_deadline_seconds('/tasks/T/step/execute', cfg) == 120


def test_step_forward_deadline_seconds_defaults_and_bounds():
    assert _step_forward_deadline_seconds('/tasks/T/step/propose', {}) == 240
    low = {'autopilot_security_policies': {'balanced': {'propose_timeout': 5}}}
    assert _step_forward_deadline_seconds('/tasks/T/step/propose', low) == 30


def test_step_deadline_monotonic_only_for_step_endpoints():
    assert _step_deadline_monotonic('/register', {}) is None
    assert _step_deadline_monotonic('/tasks/T/step/propose', {}) is not None


def _forwarder(config):
    return AutopilotWorkerForwarder(
        app_config_provider=lambda: config,
        repository_registry_provider=lambda: None,
        resilience_config_provider=lambda: {},
        forward_to_worker=lambda *a, **k: None,
        record_worker_success=lambda *a, **k: None,
        record_worker_failure=lambda *a, **k: None,
        record_provider_backpressure=lambda *a, **k: None,
        record_forward_http_error=lambda *a, **k: None,
        append_trace_event=lambda *a, **k: None,
    )


def test_propose_forward_gets_bounded_deadline():
    forwarder = _forwarder({'autopilot_security_policies': {'balanced': {'propose_timeout': 240}}})
    deadline = forwarder.trusted_deadline(endpoint='/tasks/T-1/step/propose', payload={'task_id': 'T-1'})
    assert deadline is not None
    assert deadline.budget_seconds == 240


def test_propose_deadline_defaults_when_config_missing():
    forwarder = _forwarder({})
    deadline = forwarder.trusted_deadline(endpoint='/tasks/T-1/step/propose', payload={'task_id': 'T-1'})
    assert deadline is not None
    assert deadline.budget_seconds == 240


def test_propose_deadline_has_lower_bound():
    forwarder = _forwarder({'autopilot_security_policies': {'balanced': {'propose_timeout': 5}}})
    assert forwarder.trusted_deadline(
        endpoint='/tasks/T-1/step/propose', payload={'task_id': 'T-1'}).budget_seconds == 30


def test_non_step_or_mismatched_task_has_no_deadline():
    forwarder = _forwarder({'autopilot_security_policies': {'balanced': {'propose_timeout': 240}}})
    assert forwarder.trusted_deadline(endpoint='/tasks/T-1', payload={'task_id': 'T-1'}) is None
    assert forwarder.trusted_deadline(endpoint='/tasks/T-1/step/propose', payload={'task_id': 'OTHER'}) is None


def test_propose_deadline_reads_effective_policy_from_agent_config():
    # The effective policy lives under AGENT_CONFIG; the top level must not
    # silently mask it and drop the deadline to the hardcoded default.
    forwarder = _forwarder(
        {'AGENT_CONFIG': {'autopilot_security_policies': {'balanced': {'propose_timeout': 300}}}}
    )
    deadline = forwarder.trusted_deadline(endpoint='/tasks/T-1/step/propose', payload={'task_id': 'T-1'})
    assert deadline.budget_seconds == 300


def test_propose_deadline_never_shorter_than_task_propose_timeout():
    # The model may plan for task_propose_timeout_seconds; the transport must
    # wait at least that long or every slow proposal dies at the default.
    forwarder = _forwarder(
        {
            'AGENT_CONFIG': {
                'autopilot_security_policies': {'balanced': {'propose_timeout': 240}},
                'task_propose_timeout_seconds': 600,
            }
        }
    )
    deadline = forwarder.trusted_deadline(endpoint='/tasks/T-1/step/propose', payload={'task_id': 'T-1'})
    assert deadline.budget_seconds == 600


def test_step_forward_deadline_honors_task_propose_timeout_only_for_propose():
    cfg = {'task_propose_timeout_seconds': 600}
    assert _step_forward_deadline_seconds('/tasks/T/step/propose', cfg) == 600
    assert _step_forward_deadline_seconds('/tasks/T/step/execute', cfg) == 180
