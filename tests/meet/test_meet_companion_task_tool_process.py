"""Actual Hub HTTP/SQL evidence through owned HTTP and model pipe subprocesses."""

import sys
import threading

import pytest

from tests.meet.test_meet_companion_task_control_http import running_hub
from tests.meet.test_meet_companion_task_grant_client import assignment
from tests.meet.test_meet_companion_task_grants import assigned as assigned
from tests.meet.test_meet_companion_task_tools import KEY
from tests.meet.test_meet_companion_task_tools import hub as hub
from worker.meet_media.companion_execution_process import CompanionExecutionProcess
from worker.meet_media.companion_task_grant_client import AssignedCompanionGrantClient
from worker.meet_media.companion_task_tool_process import AssignedCompanionToolProcess

pytestmark = pytest.mark.timeout(15)

CHILD = """
import json, os, sys
from worker.meet_media.companion_execution_ports import CompanionExecutionPorts
request = json.loads(sys.stdin.buffer.readline())
assert 'MEET_WORKER_KEY_FILE' not in os.environ
ports = CompanionExecutionPorts(sys.stdin.buffer, sys.stdout.buffer, allowed={'tool'})
result = ports.call('tool', {'tool': 'codecompass.retrieve', 'arguments': {'query': request['text']}})
assert result['run']['evidence_scope'] == 'test'
assert result['sources']
print(json.dumps({'kind': 'answer', 'result': 'synthetic scoped result'}), flush=True)
"""


def port(f, endpoint):
    value = assignment() | {"session_id": "hub-session", "deadline": f.f.context["deadline"]}
    grants = AssignedCompanionGrantClient(value, endpoint, KEY)
    return AssignedCompanionToolProcess(grants)


def test_model_child_calls_only_owned_tool_port_against_actual_hub_registry(hub):
    with running_hub(hub.app) as endpoint:
        tools = port(hub, endpoint)
        execution = CompanionExecutionProcess(command=(sys.executable, "-u", "-c", CHILD),
                                               environment=tools.environment, ports={"tool": tools})
        ticks = []
        try:
            assert execution.call("answer", "Meet", cooperate=lambda: ticks.append(True)) == "synthetic scoped result"
            assert len(hub.calls) == 1 and len(ticks) >= 3
        finally:
            execution.close()
        assert execution._process.poll() is not None


def test_revocation_while_http_is_pending_keeps_owner_responsive_and_kills_both_children(hub, monkeypatch):
    import worker.meet_media.companion_task_tool_process as module

    entered, release = threading.Event(), threading.Event()
    transports = []
    actual_transport = module.CompanionProcessTransport

    def tracked(*args, **kwargs):
        transport = actual_transport(*args, **kwargs)
        transports.append(transport)
        return transport

    monkeypatch.setattr(module, "CompanionProcessTransport", tracked)

    @hub.app.before_request
    def hold():
        entered.set()
        assert release.wait(2), "bounded synthetic HTTP delay"

    def cooperate():
        if entered.is_set():
            hub.f.task.status = "cancelled"
            release.set()
            raise PermissionError("synthetic_task_cancelled")

    with running_hub(hub.app) as endpoint:
        tools = port(hub, endpoint)
        execution = CompanionExecutionProcess(command=(sys.executable, "-u", "-c", CHILD),
                                               environment=tools.environment, ports={"tool": tools})
        try:
            with pytest.raises(PermissionError, match="task_cancelled"):
                execution.call("answer", "Meet", cooperate=cooperate)
        finally:
            release.set()
            execution.close()
        assert execution._process.poll() is not None
        assert len(transports) == 1 and transports[0].process.poll() is not None
        assert not hub.calls


def test_model_cannot_replace_owner_endpoint_credentials_or_assignment(hub):
    with running_hub(hub.app) as endpoint:
        tools = port(hub, endpoint)
        with pytest.raises(ValueError, match="tool_port_invalid"):
            tools({"tool": "codecompass.retrieve", "arguments": {}, "endpoint": "foreign"}, cooperate=lambda: None)
        assert not hub.calls
