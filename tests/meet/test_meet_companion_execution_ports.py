"""Actual owned subprocess pipe; synthetic ports do not issue Hub evidence."""

import json
import sys
import time

import pytest

from worker.meet_media.companion_execution_process import CompanionExecutionError, CompanionExecutionProcess

pytestmark = pytest.mark.timeout(10)


def runner(code, **options):
    return CompanionExecutionProcess(command=(sys.executable, "-u", "-c", code), **options)


CHILD = """
import json, sys
from worker.meet_media.companion_execution_ports import CompanionExecutionPorts
for line in sys.stdin.buffer:
    request = json.loads(line)
    ports = CompanionExecutionPorts(sys.stdin.buffer, sys.stdout.buffer, allowed={'tool'})
    first = ports.call('tool', {'tool': 'codecompass.retrieve', 'arguments': {'query': request['text']}})
    second = ports.call('tool', {'tool': 'codecompass.layers_heads', 'arguments': {}})
    print(json.dumps({'kind': request['kind'], 'result': first['text'] + second['text']}), flush=True)
"""


def test_real_child_uses_injected_owner_port_and_resets_sequence_per_answer():
    calls, ticks = [], []

    def tool(arguments, *, cooperate):
        cooperate()
        calls.append(arguments)
        return {"text": "scoped"}

    execution = runner(CHILD, ports={"tool": tool})
    try:
        for text in ("one", "two"):
            assert execution.call("answer", text, cooperate=lambda: ticks.append(True)) == "scopedscoped"
        assert [item["arguments"].get("query") for item in calls] == ["one", None, "two", None]
        assert len(ticks) >= 12
    finally:
        execution.close()
    assert execution._process.poll() is not None


@pytest.mark.parametrize("frame", [
    {"kind": "port", "sequence": 2, "port": "tool", "arguments": {}},
    {"kind": "port", "sequence": True, "port": "tool", "arguments": {}},
    {"kind": "port", "sequence": 1, "port": "delegate", "arguments": {}},
    {"kind": "port", "sequence": 1, "port": "tool", "arguments": {}, "endpoint": "foreign"},
])
def test_unowned_broadened_or_out_of_sequence_call_never_invokes_port(frame):
    execution = runner(f"import sys; sys.stdin.readline(); print({json.dumps(frame)!r}, flush=True)",
                       ports={"tool": lambda *args, **kwargs: pytest.fail("untrusted port executed")})
    with pytest.raises(CompanionExecutionError, match="protocol_invalid"):
        execution.call("answer", "synthetic", cooperate=lambda: None)
    assert execution._process.poll() is not None


def test_port_calls_are_disabled_without_explicit_owner_adapter():
    execution = runner(CHILD)
    with pytest.raises(CompanionExecutionError, match="protocol_invalid"):
        execution.call("answer", "synthetic", cooperate=lambda: None)
    assert execution._process.poll() is not None


def test_current_control_loss_inside_port_kills_child_and_prevents_reply():
    def revoked(arguments, *, cooperate):
        raise PermissionError("synthetic_chat_revision_changed")

    execution = runner(CHILD, ports={"tool": revoked})
    with pytest.raises(PermissionError, match="chat_revision_changed"):
        execution.call("answer", "synthetic", cooperate=lambda: None)
    assert execution._process.poll() is not None
    with pytest.raises(CompanionExecutionError, match="execution_closed"):
        execution.call("answer", "again", cooperate=lambda: None)


def test_oversize_port_result_terminates_before_child_receives_it():
    execution = runner(CHILD, ports={"tool": lambda arguments, **kwargs: {"text": "x" * 32_768}})
    with pytest.raises(ValueError, match="port_oversize"):
        execution.call("answer", "synthetic", cooperate=lambda: None)
    assert execution._process.poll() is not None


def test_duplicate_fields_do_not_dispatch_to_owner():
    code = """
import sys
sys.stdin.readline()
print('{"kind":"port","sequence":1,"port":"delegate","port":"tool","arguments":{}}', flush=True)
"""
    execution = runner(code, ports={"tool": lambda *args, **kwargs: pytest.fail("duplicate fields executed")})
    with pytest.raises(CompanionExecutionError, match="protocol_invalid"):
        execution.call("answer", "synthetic", cooperate=lambda: None)
    assert execution._process.poll() is not None


def test_speech_operation_cannot_use_model_tool_ports():
    execution = runner(CHILD, ports={"tool": lambda *args, **kwargs: pytest.fail("speech accessed model tools")})
    with pytest.raises(CompanionExecutionError, match="protocol_invalid"):
        execution.call("speech", "synthetic", cooperate=lambda: None)
    assert execution._process.poll() is not None


def test_total_port_budget_is_checked_by_owner_even_for_noncompliant_child():
    code = """
import json, sys
sys.stdin.readline()
for sequence in range(1, 34):
    print(json.dumps({'kind':'port','sequence':sequence,'port':'tool','arguments':{}}), flush=True)
    sys.stdin.readline()
"""
    calls = []
    execution = runner(code, ports={"tool": lambda arguments, **kwargs: calls.append(True)})
    with pytest.raises(CompanionExecutionError, match="protocol_invalid"):
        execution.call("answer", "synthetic", cooperate=lambda: None)
    assert len(calls) == 32 and execution._process.poll() is not None


def test_deadline_cannot_be_extended_by_owner_port_response():
    def delayed(arguments, *, cooperate):
        time.sleep(0.2)
        return {"text": "expired"}

    execution = runner(CHILD, ports={"tool": delayed}, timeout_seconds=0.15)
    with pytest.raises(CompanionExecutionError, match="execution_timeout"):
        execution.call("answer", "synthetic", cooperate=lambda: None)
    assert execution._process.poll() is not None


@pytest.mark.parametrize("port,size,accepted", [("model", 40_000, True), ("tool", 40_000, False),
                                               ("model", 128_000, False)])
def test_larger_model_requests_do_not_broaden_other_ports(port, size, accepted):
    code = f"""
import json,sys
sys.stdin.readline()
print(json.dumps({{'kind':'port','sequence':1,'port':{port!r},'arguments':{{'history':'x'*{size}}}}}),flush=True)
reply=json.loads(sys.stdin.readline())
print(json.dumps({{'kind':'answer','result':reply['result']}}),flush=True)
"""
    calls = []

    def handler(arguments, *, cooperate):
        calls.append(arguments)
        return str(len(arguments["history"]))

    execution = runner(code, ports={port: handler})
    try:
        if accepted:
            assert execution.call("answer", "synthetic", cooperate=lambda: None) == str(size)
            assert len(calls) == 1 and len(calls[0]["history"]) == size
        else:
            with pytest.raises(CompanionExecutionError, match="protocol_invalid"):
                execution.call("answer", "synthetic", cooperate=lambda: None)
            assert not calls and execution._process.poll() is not None
    finally:
        execution.close()
