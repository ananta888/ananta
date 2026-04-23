from __future__ import annotations

from agent.services.doom_loop_service import get_doom_loop_service


def test_doom_loop_detector_flags_repeated_tool_calls():
    service = get_doom_loop_service()
    signals = [
        service.build_signal(
            task_id="task-1",
            trace_id="trace-1",
            backend_name="apply_patch",
            action_type="tool_call",
            failure_type="success",
            iteration_count=index + 1,
            action_signature="apply_patch:file-a",
            progress_made=False,
        )
        for index in range(4)
    ]
    decision = service.detect(signals=signals)
    payload = decision.as_dict()
    assert payload["detected"] is True
    assert payload["classification"] == "repeated_tool_call"
    assert payload["action"] in {"inject_correction", "require_review", "pause", "abort"}


def test_doom_loop_detector_flags_repeated_failures_and_abort_escalation():
    service = get_doom_loop_service()
    signals = [
        service.build_signal(
            task_id="task-2",
            trace_id="trace-2",
            backend_name="shell",
            action_type="shell_command",
            failure_type="timeout",
            iteration_count=index + 1,
            action_signature="pytest -q",
            progress_made=False,
        )
        for index in range(9)
    ]
    decision = service.detect(signals=signals)
    payload = decision.as_dict()
    assert payload["detected"] is True
    assert payload["classification"] == "repeated_failure"
    assert payload["severity"] == "critical"
    assert payload["action"] == "abort"


def test_doom_loop_detector_flags_oscillation_pattern():
    service = get_doom_loop_service()
    failures = ["timeout", "command_failure", "timeout", "command_failure", "timeout"]
    signals = [
        service.build_signal(
            task_id="task-3",
            trace_id="trace-3",
            backend_name="shell",
            action_type="shell_command",
            failure_type=failure,
            iteration_count=index + 1,
            action_signature="lint",
            progress_made=False,
        )
        for index, failure in enumerate(failures)
    ]
    decision = service.detect(signals=signals)
    payload = decision.as_dict()
    assert payload["detected"] is True
    assert payload["classification"] == "oscillating_retry_pattern"


def test_collect_signals_from_execution_history():
    service = get_doom_loop_service()
    history = [
        {
            "event_type": "execution_result",
            "loop_signals": [
                {
                    "task_id": "task-4",
                    "trace_id": "trace-4",
                    "backend_name": "shell",
                    "action_type": "shell_command",
                    "failure_type": "timeout",
                    "iteration_count": 1,
                    "action_signature": "make test",
                }
            ],
        }
    ]
    records = service.collect_signals_from_history(history)
    assert len(records) == 1
    assert records[0]["task_id"] == "task-4"
    assert records[0]["action_type"] == "shell_command"

