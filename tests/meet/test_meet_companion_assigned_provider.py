"""Actual model child/provider HTTP/Hub Registry; provider responses are synthetic."""

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
from worker.meet_media.companion_execution_process import CompanionExecutionProcess

pytestmark = pytest.mark.timeout(15)


def child(base, *, history=""):
    return f"""
import json,sys
from worker.meet_media.companion_assigned_provider import AssignedCompanionProvider
from worker.meet_media.companion_execution_ports import CompanionExecutionPorts
from worker.meet_media.openai_http import OpenAiHttp
incoming=json.loads(sys.stdin.buffer.readline())
ports=CompanionExecutionPorts(sys.stdin.buffer,sys.stdout.buffer,allowed={{'model'}})
provider=AssignedCompanionProvider(OpenAiHttp({base!r},headers={{}}),ports,backend='openai',tool_results=lambda:[])
for number in range(2):
    provider.chat({{'model':'synthetic-model','stream':False,'max_tokens':128,
                   'messages':[{{'role':'user','content':incoming['text']+str(number)+{history!r}}}]}})
print(json.dumps({{'kind':'answer','result':'synthetic answer'}}),flush=True)
"""


@pytest.mark.parametrize("history", ["", "x" * 40_000], ids=["ordinary", "large_history"])
def test_each_real_provider_request_has_a_prior_reserved_run_and_exact_completion(http, history):
    calls = []

    @http.app.post("/chat/completions")
    def synthetic_provider():
        with http.database.connect() as connection:
            reserved = connection.execute(select(HubRunEvidenceIdentityDB.__table__).where(
                HubRunEvidenceIdentityDB.state == "reserved")).mappings().all()
        assert len(reserved) == 1, "Hub reserves before actual provider HTTP execution"
        calls.append(request.get_json())
        return jsonify(generated())

    with running_hub(http.app) as endpoint:
        helper = process(http, endpoint)
        base = endpoint.split("/api/meet/v1/")[0]
        execution = CompanionExecutionProcess(command=(sys.executable, "-u", "-c", child(base, history=history)),
            environment=helper.environment, ports={"model": helper})
        ticks = []
        try:
            assert execution.call("answer", "synthetic", cooperate=lambda: ticks.append(True)) == "synthetic answer"
        finally:
            execution.close()
    assert len(calls) == 2 and len(ticks) >= 8
    with http.database.connect() as connection:
        runs = connection.execute(select(HubRunEvidenceIdentityDB.__table__)).mappings().all()
    assert len(runs) == 2 and all(run["state"] == "succeeded" for run in runs)
    assert runs[0]["run_id"] != runs[1]["run_id"] and runs[0]["input_digest"] != runs[1]["input_digest"]


def test_missing_hub_approval_prevents_actual_provider_http(http):
    @http.app.post("/chat/completions")
    def forbidden():
        pytest.fail("provider executed before Hub authorization")

    http.app.config["MEET_COMPANION_ASSIGNED_MODEL_PROFILE"]["auto_execute"] = False
    with running_hub(http.app) as endpoint:
        helper = process(http, endpoint)
        execution = CompanionExecutionProcess(command=(sys.executable, "-u", "-c",
                                                       child(endpoint.split("/api/meet/v1/")[0])),
            environment=helper.environment, ports={"model": helper})
        with pytest.raises(RuntimeError, match="execution_failed"):
            execution.call("answer", "synthetic", cooperate=lambda: None)
        assert execution._process.poll() is not None


def test_withdrawal_during_provider_request_prevents_reply_and_second_inference(http):
    calls = []

    @http.app.post("/chat/completions")
    def withdrawn():
        calls.append(True)
        http.app.config["MEET_COMPANION_ASSIGNED_MODEL_PROFILE"]["auto_execute"] = False
        return jsonify(generated())

    with running_hub(http.app) as endpoint:
        helper = process(http, endpoint)
        execution = CompanionExecutionProcess(command=(sys.executable, "-u", "-c",
                                                       child(endpoint.split("/api/meet/v1/")[0])),
            environment=helper.environment, ports={"model": helper})
        with pytest.raises(RuntimeError, match="execution_failed"):
            execution.call("answer", "synthetic", cooperate=lambda: None)
        assert execution._process.poll() is not None
    assert calls == [True]
    with http.database.connect() as connection:
        runs = connection.execute(select(HubRunEvidenceIdentityDB.__table__)).mappings().all()
    assert len(runs) == 1 and runs[0]["state"] == "failed"
