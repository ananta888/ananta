"""Assigned model child cannot inherit legacy Hub tool/retrieval configuration."""

import os
import sys

import pytest

from tests.meet.test_meet_companion_task_grant_client import assignment
from tests.meet.test_meet_dialog_transport import assignment as legacy_assignment
from worker.meet_media.companion_execution_process import CompanionExecutionProcess
from worker.meet_media.companion_task_environment import assigned_model_environment


def test_environment_preserves_provider_settings_without_mutating_legacy_parent():
    parent = {"MEET_HUB_ASSIST_URL": "http://synthetic/legacy", "MEET_WORKER_KEY_FILE": "/synthetic/key",
              "MEET_COMPANION_TOOLS": "1", "ANANTA_MEET_AVATAR_CODECOMPASS": "1",
              "MEET_COMPANION_RAG_PREFIX": "1", "MEET_LLM_URL": "http://synthetic-model", "PIPER_MODEL": "voice"}
    before = dict(parent)
    child = assigned_model_environment(assignment(), environment=parent)
    assert parent == before and child is not parent
    assert child["MEET_LLM_URL"] == parent["MEET_LLM_URL"] and child["PIPER_MODEL"] == "voice"
    assert "MEET_WORKER_KEY_FILE" not in child and "MEET_HUB_ASSIST_URL" not in child
    assert child["MEET_COMPANION_PROJECT"] == assignment()["project_id"]
    assert child["MEET_COMPANION_TOOLS"] == child["ANANTA_MEET_AVATAR_CODECOMPASS"] == "0"
    assert child["MEET_LLM_TOOL_RESULT_CHARS"] == "6000"


def test_unnegotiated_assignment_cannot_construct_assigned_model_environment():
    with pytest.raises(ValueError, match="profile_invalid"):
        assigned_model_environment(legacy_assignment(), environment={})


@pytest.mark.timeout(10)
def test_actual_model_child_builds_dialog_without_tools_or_retrieval_even_when_legacy_flags_are_on(tmp_path):
    parent = os.environ | {"MEET_COMPANION_TOOLS": "1", "ANANTA_MEET_AVATAR_CODECOMPASS": "1",
                           "MEET_COMPANION_RAG_PREFIX": "1", "MEET_HUB_ASSIST_URL": "http://127.0.0.1:9",
                           "MEET_WORKER_KEY_FILE": "/synthetic/private-key",
                           "MEET_COMPANION_LOG": str(tmp_path / "synthetic.log"), "MEET_ROUTE_DECISION": "off"}
    script = """
import json,os,sys
request=json.loads(sys.stdin.readline())
assert 'MEET_WORKER_KEY_FILE' not in os.environ
assert not any(k.startswith('MEET_HUB_') for k in os.environ)
from worker.meet_media import companion,llm,assist
def forbidden(*args,**kwargs):
    raise AssertionError('legacy Hub call attempted')
assist.retrieve_snippets=assist.fetch_snippets=assist.call_tool=forbidden
llm.answer=lambda text,**kwargs: 'Synthetic answer'
dialog=companion.dialog()
assert dialog._tools is None and dialog._retriever is None and not dialog._codecompass_enabled
companion.generate_reply(request['text'])
print(json.dumps({'kind':request['kind'],'result':'Synthetic closed-profile answer'}),flush=True)
"""
    execution = CompanionExecutionProcess(command=(sys.executable, "-u", "-c", script),
        environment=assigned_model_environment(assignment(), environment=parent))
    try:
        assert execution.call("answer", "Wie ist die Architektur aufgebaut?", cooperate=lambda: None) == (
            "Synthetic closed-profile answer")
    finally:
        execution.close()
