from types import SimpleNamespace

from flask import Flask

from agent.metrics import TASK_SUCCESS_RATE, WORKER_BUSY_SECONDS, WORKER_PROPOSE_DURATION_SECONDS
from agent.routes.tasks.autopilot_tick_engine import _dispatch_one_task


def _sample_value(metric, sample_name: str) -> float:
    total = 0.0
    for family in metric.collect():
        for sample in family.samples:
            if sample.name == sample_name:
                total += float(sample.value)
    return total


def test_dispatch_populates_core_bottleneck_metrics(monkeypatch):
    app = Flask(__name__)

    class _Loop:
        def _agent_config(self):
            return {}

        def _forward_with_retry(self, _url, endpoint, _payload, token=None):
            if endpoint.endswith('/step/propose'):
                return {"command": "echo ok", "tool_calls": None, "reason": "ok"}
            return {"status": "completed", "exit_code": 0, "output": "ok"}

        def _circuit_open_details(self, _url):
            return 0, 0

        def _is_worker_circuit_open(self, _url):
            return False

    class _DecisionService:
        def normalize_proposal_data(self, data):
            return dict(data)

        def build_proposal_snapshot(self, data):
            return {
                "command": data.get("command"),
                "tool_calls": data.get("tool_calls"),
                "reason": data.get("reason"),
                "backend": "test",
                "routing": {"reason": "test"},
            }

        def normalize_execute_result(self, data):
            return str(data.get("status") or "failed"), int(data.get("exit_code") or 1), str(data.get("output") or "")

        def apply_quality_gate_if_needed(self, task, task_status, output, exit_code, agent_cfg):
            return task_status, output, None

        def evaluate_tool_guardrails_for_autopilot(self, **kwargs):
            return SimpleNamespace(allowed=True, reasons=[], blocked_tools=[])

    services = SimpleNamespace(autopilot_decision_service=_DecisionService())

    monkeypatch.setattr(
        "agent.routes.tasks.autopilot_tick_engine._select_model_for_task",
        lambda **kwargs: ("m1", {"selected_model": "m1", "source": "test"}),
    )
    monkeypatch.setattr(
        "agent.routes.tasks.autopilot_tick_engine._proposal_strategy_candidates",
        lambda **kwargs: [{"model": "m1", "source": "test", "temperature": None}],
    )

    task = SimpleNamespace(id="t-metrics", title="t", description="d", status="todo", verification_status={})
    worker = SimpleNamespace(url="http://worker", token="token")

    before_propose = _sample_value(WORKER_PROPOSE_DURATION_SECONDS, "worker_propose_duration_seconds_sum")
    before_busy = _sample_value(WORKER_BUSY_SECONDS, "worker_busy_seconds_sum")
    before_success = _sample_value(TASK_SUCCESS_RATE, "task_success_total")

    result = _dispatch_one_task(
        task=task,
        target_worker=worker,
        was_assigned=False,
        loop=_Loop(),
        services=services,
        policy={"execute_timeout": 10, "execute_retries": 0, "level": "safe"},
        fallback_policy={"allow_hub_worker_fallback": True, "escalate_on_fallback_block": True, "fallback_block_status": "blocked"},
        runtime_caps={"runtime": {}, "models": {}},
        queue_positions={},
        local_worker_url="http://local",
        app=app,
        append_trace_event=lambda *a, **k: None,
        update_local_task_status=lambda *a, **k: None,
    )

    after_propose = _sample_value(WORKER_PROPOSE_DURATION_SECONDS, "worker_propose_duration_seconds_sum")
    after_busy = _sample_value(WORKER_BUSY_SECONDS, "worker_busy_seconds_sum")
    after_success = _sample_value(TASK_SUCCESS_RATE, "task_success_total")

    assert result.dispatched is True
    assert result.completed is True
    assert after_propose >= before_propose
    assert after_busy >= before_busy
    assert after_success >= before_success + 1
