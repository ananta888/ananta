"""Actual scoped tool/model HTTP through model child; synthetic MCP/provider fixtures."""

import sys

import pytest
from flask import jsonify, request
from sqlalchemy import select

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
from tests.meet.test_meet_companion_model_runs import generated
from tests.meet.test_meet_companion_model_runs import models as models
from tests.meet.test_meet_companion_task_control_http import running_hub
from tests.meet.test_meet_companion_task_grants import assigned as assigned
from tests.meet.test_meet_companion_task_model_http import http as http
from tests.meet.test_meet_companion_task_model_process import process
from tests.meet.test_meet_companion_task_tools import hub as hub
from worker.meet_media.companion_assigned_tools import assigned_toolbox
from worker.meet_media.companion_execution_process import CompanionExecutionProcess
from worker.meet_media.companion_task_grant_client import AssignedCompanionGrantClient
from worker.meet_media.companion_task_tool_process import AssignedCompanionToolProcess

pytestmark = pytest.mark.timeout(15)


@pytest.mark.parametrize("mode", ["toolbox", "dialog", "installed"])
def test_actual_assigned_tool_round_supplies_verified_sources_to_separate_model_run(http, mode, monkeypatch, tmp_path):
    calls = []

    @http.app.post("/chat/completions")
    def provider():
        with http.database.connect() as connection:
            active = connection.execute(select(HubRunEvidenceIdentityDB.__table__).where(
                HubRunEvidenceIdentityDB.state == "reserved")).mappings().one()
        assert len(active["source_ids"]) == 2
        calls.append(request.get_json())
        return jsonify(generated())

    @http.app.get("/models")
    def listing():
        return jsonify({"data": [{"id": "synthetic-model"}]})

    with running_hub(http.app) as endpoint:
        helper = process(http, endpoint)
        grants = AssignedCompanionGrantClient(helper.assignment, helper.endpoint, helper.key)
        tools = AssignedCompanionToolProcess(grants)
        base = endpoint.split("/api/meet/v1/")[0]
        code = f"""
import json,sys
from worker.meet_media.companion_assigned_tools import assigned_toolbox
from worker.meet_media.companion_assigned_provider import AssignedCompanionProvider
from worker.meet_media.companion_execution_ports import CompanionExecutionPorts
from worker.meet_media.openai_http import OpenAiHttp
incoming=json.loads(sys.stdin.buffer.readline())
ports=CompanionExecutionPorts(sys.stdin.buffer,sys.stdout.buffer,allowed={{'tool','model'}})
box,results=assigned_toolbox(ports)
assert len(box.definitions())==8
box.force('codecompass_search',{{'query':'Meet'}})
provider=AssignedCompanionProvider(OpenAiHttp({base!r},headers={{}}),ports,backend='openai',tool_results=results.snapshot)
provider.chat({{'model':'synthetic-model','stream':False,'max_tokens':128,
 'messages':[{{'role':'user','content':incoming['text']}},*box.forced_messages(json_arguments=True)]}})
print(json.dumps({{'kind':'answer','result':'synthetic source-bound answer'}}),flush=True)
"""
        if mode == "dialog":
            code = """
import json,sys
from worker.meet_media.companion_assigned_dialog import AssignedCompanionDialog
from worker.meet_media.companion_execution_ports import CompanionExecutionPorts
dialog=AssignedCompanionDialog()
for line in sys.stdin.buffer:
    incoming=json.loads(line)
    ports=CompanionExecutionPorts(sys.stdin.buffer,sys.stdout.buffer,allowed={'tool','model'})
    reply=dialog.answer(incoming['text'],ports)
    assert dialog.ports.current is None
    if dialog.dialog.last_trace.route == 'self_explanation':
        assert dialog.provider.last_completion is None and dialog.receipts.snapshot() == []
    print(json.dumps({'kind':'answer','result':reply}),flush=True)
"""
        environment = helper.environment | {"MEET_LLM_BACKEND": "openai", "MEET_LLM_MODEL": "synthetic-model",
            "MEET_LLM_OPENAI_BASE_URL": base, "MEET_LLM_OPENAI_API_KEY": ""}
        execution = CompanionExecutionProcess(command=(sys.executable, "-u", "-c", code),
            environment=environment, ports={"tool": tools, "model": helper})
        if mode == "installed":
            from worker.meet_media.companion_task_model_selection import assigned_execution

            http.assigned[1].model_policy = http.app.extensions["meet_companion_model_runs"].policy
            http.app.extensions["meet_companion_task_grants"] = http.assigned[1]
            for name, value in environment.items():
                if name.startswith("MEET_LLM_"):
                    monkeypatch.setenv(name, value)
            monkeypatch.setenv("MEET_LLM_BACKEND", "ollama")  # Hub selection overrides a conflicting Worker default.
            monkeypatch.setenv("MEET_COMPANION_LOG", str(tmp_path / "synthetic.log"))
            execution = assigned_execution(grants, grants.read())
        try:
            if mode == "toolbox":
                assert execution.call("answer", "synthetic", cooperate=lambda: None) == "synthetic source-bound answer"
            else:
                assert execution.call("answer", "Wie ist die Code-Architektur aufgebaut?",
                                      cooperate=lambda: None) == "synthetic answer"
                assert execution.call("answer", "Wie hast du diese Antwort erzeugt?", cooperate=lambda: None)
        finally:
            execution.close()
    assert len(calls) == 1 and len(http.calls) == 1
    assert "[untrusted Hub tool codecompass.retrieve; source SRC_" in calls[0]["messages"][-1]["content"]


def test_missing_arguments_do_not_invoke_assigned_execution_port():
    class Forbidden:
        def call(self, *args):
            pytest.fail("invalid arguments invoked port")

    box, results = assigned_toolbox(Forbidden())
    with pytest.raises(ValueError, match="arguments_invalid"):
        box.run("codecompass_search", {})
    assert results.snapshot() == []


def test_receipts_are_copied_and_cleared_between_answers(http):
    from tests.meet.test_meet_companion_task_tools import post

    response = post(http)[1].get_json()

    class Port:
        def call(self, name, arguments):
            assert name == "tool" and arguments["tool"] == "codecompass.retrieve"
            return response

    box, receipts = assigned_toolbox(Port())
    box.force("codecompass_search", {"query": "Meet"})
    snapshot = receipts.snapshot()
    response["text"] = "changed caller object"
    assert receipts.snapshot() == snapshot
    snapshot[0]["text"] = "changed consumer object"
    assert receipts.snapshot()[0]["text"] != snapshot[0]["text"]
    box.reset()
    assert receipts.snapshot() == [] and box.calls == [] and box.forced == []
