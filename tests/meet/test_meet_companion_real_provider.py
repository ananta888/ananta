"""Actual local GPU inference with prior Hub reservations; test evidence only."""

import json
import os
import sys
from contextlib import ExitStack
from uuid import uuid4

import pytest
from flask import jsonify, request
from sqlalchemy import select

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
from tests.meet.test_meet_companion_model_runs import models as models
from tests.meet.test_meet_companion_task_control_http import running_hub
from tests.meet.test_meet_companion_task_grants import assigned as assigned
from tests.meet.test_meet_companion_task_model_http import http as http
from tests.meet.test_meet_companion_task_model_process import process
from tests.meet.test_meet_companion_task_tools import hub as hub
from tests.meet_companion_ollama_fixture import CompanionOllamaFixture
from tests.meet_gpu_source_fixture import docker
from tests.meet_ollama_model_spec import MODEL_DIGEST
from worker.meet_media.companion_execution_process import CompanionExecutionProcess


@pytest.mark.timeout(150)
@pytest.mark.skipif(os.environ.get("MEET_COMPANION_REAL_PROVIDER_GATE") != "1", reason="explicit local GPU gate")
def test_actual_gpu_provider_runs_only_after_hub_reservation(http, record_property):
    network = "meet-test-inference-" + str(uuid4()) + "-network"
    counts = []
    http.app.config["MEET_COMPANION_ASSIGNED_MODEL_PROFILE"]["model"] = "qwen2.5:1.5b"
    with ExitStack() as cleanup:
        docker("network", "create", "--internal", network)
        cleanup.callback(docker, "network", "rm", network)
        provider = cleanup.enter_context(CompanionOllamaFixture(network,
            os.environ["MEET_TEST_OLLAMA_MODELS"], os.environ["MEET_TEST_WSL_DRIVER_STORE"]))

        @http.app.post("/chat/completions")
        def forward():
            with http.database.connect() as connection:
                reserved = connection.execute(select(HubRunEvidenceIdentityDB.__table__).where(
                    HubRunEvidenceIdentityDB.state == "reserved")).mappings().one()
            assert reserved["synthetic"] and reserved["evidence_scope"] == "test"
            counts.append(reserved["run_id"])
            return jsonify(provider.request("/v1/chat/completions", request.get_json()))

        with running_hub(http.app) as endpoint:
            helper = process(http, endpoint)
            base = endpoint.split("/api/meet/v1/")[0]
            code = f'''
import json,sys
from worker.meet_media.companion_assigned_provider import AssignedCompanionProvider
from worker.meet_media.companion_execution_ports import CompanionExecutionPorts
from worker.meet_media.openai_http import OpenAiHttp
incoming=json.loads(sys.stdin.buffer.readline())
ports=CompanionExecutionPorts(sys.stdin.buffer,sys.stdout.buffer,allowed={{'model'}})
provider=AssignedCompanionProvider(OpenAiHttp({base!r},headers={{}}),ports,backend='openai',tool_results=lambda:[])
for number in range(2):
 result=provider.chat({{'model':'qwen2.5:1.5b','stream':False,'max_tokens':32,
  'messages':[{{'role':'user','content':incoming['text']}}]}})
 assert result['choices'][0]['message']['content'].strip()
print(json.dumps({{'kind':'answer','result':'actual GPU inference completed'}}),flush=True)
'''
            execution = CompanionExecutionProcess(command=(sys.executable, "-u", "-c", code),
                environment=helper.environment, ports={"model": helper})
            try:
                assert execution.call("answer", "Antworte nur mit einem kurzen Gruß.", cooperate=lambda: None) == (
                    "actual GPU inference completed")
            finally:
                execution.close()
        resident = provider.request("/api/ps")["models"]
        assert len(resident) == 1 and resident[0]["digest"] == MODEL_DIGEST and resident[0]["size_vram"] > 0
        with http.database.connect() as connection:
            runs = connection.execute(select(HubRunEvidenceIdentityDB.__table__)).mappings().all()
        assert len(counts) == len(set(counts)) == len(runs) == 2
        assert all(run["state"] == "succeeded" and run["synthetic"] for run in runs)
        record_property("actual_local_llm", json.dumps({"model_digest": MODEL_DIGEST,
            "completed_hub_test_runs": 2, "size_vram": resident[0]["size_vram"],
            "production_release_evidence": False}, sort_keys=True))
