"""Real Hub SQL Registry reservations; explicit synthetic model result fixtures."""

from copy import deepcopy

import pytest
from sqlalchemy import select

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
from agent.repositories.meet_companion_tool_sources import SqlCompanionToolSources
from agent.services.meet_companion_model_inputs import MeetCompanionModelInputs
from agent.services.meet_companion_model_policy import HubCompanionModelPolicy
from agent.services.meet_companion_model_runs import MeetCompanionModelRuns
from agent.services.meet_companion_task_tools import digest
from agent.services.meet_contract import MeetError
from ananta_contracts.meet_companion_model_input import render_tool_result
from tests.meet.test_meet_companion_task_grants import assigned as assigned
from tests.meet.test_meet_companion_task_tools import hub as hub
from tests.meet.test_meet_companion_task_tools import post


@pytest.fixture
def models(hub):
    hub.app.config["MEET_COMPANION_ASSIGNED_MODEL_PROFILE"] = {
        "backend": "openai", "model": "synthetic-model", "environment_digest": "e" * 64, "auto_execute": True,
    }
    content = SqlCompanionToolSources(hub.database, maximum=128_000)
    service = MeetCompanionModelRuns(hub.f.authority, HubCompanionModelPolicy(hub.app),
        MeetCompanionModelInputs(hub.registry, hub.store, hub.policy), content, hub.registry,
        attempts=hub.attempts, synthetic=True)
    yield hub, service


def value(responses=()):
    return {"backend": "openai", "model": "synthetic-model", "request": {
        "model": "synthetic-model", "stream": False, "max_tokens": 128,
        "messages": [{"role": "user", "content": "synthetic question"},
                     *({"role": "tool", "content": render_tool_result(item)} for item in responses)],
    }, "tool_results": list(responses)}


def generated():
    return {"model": "synthetic-model", "choices": [{"message": {"role": "assistant", "content": "synthetic answer"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3}}


def reserve(models, request=None, nonce="b" * 32):
    f, service = models
    with f.app.app_context():
        return service.reserve(f.request["assignment"], value() if request is None else request, nonce=nonce)


def finish(models, reserved, result=None):
    f, service = models
    with f.app.app_context():
        packet = {"run": reserved["run"], "input_source": reserved["input_source"],
                  "result": generated() if result is None else result}
        return service.finish(f.request["assignment"], packet)


def state(f, run_id):
    with f.database.connect() as connection:
        return connection.execute(select(HubRunEvidenceIdentityDB.__table__).where(
            HubRunEvidenceIdentityDB.run_id == run_id)).mappings().one()


def test_separate_model_run_is_reserved_over_exact_request_and_tool_sources_before_execution(models):
    f, _service = models
    tool = post(f)[1].get_json()
    request = value([tool])
    reserved = reserve(models, request)
    run_id = reserved["run"]["run_id"]
    assert run_id != tool["run"]["run_id"]
    run = state(f, run_id)
    assert run["state"] == "reserved" and run["result_digest"] is None
    assert run["source_ids"] == sorted([reserved["input_source"]["source_id"], tool["sources"][0]["source_id"]])
    completed = finish(models, reserved)
    run = state(f, run_id)
    assert run["state"] == "succeeded" and run["result_digest"] == digest(generated())
    assert completed["run"] == reserved["run"] and completed["result_digest"] == digest(generated())
    output = f.registry.require_source_identity(tenant_id="tenant", project_id="project",
        source_id=completed["sources"][0]["source_id"],
        expected_binding_digest=completed["sources"][0]["binding_digest"])
    assert output.origin_type == "meet_companion_model" and output.synthetic and output.evidence_scope == "test"


@pytest.mark.parametrize("bad", ["model", "backend", "no_auto_policy", "environment", "nonce", "request_model",
                                 "stream", "budget", "unregistered_tool_message"])
def test_unapproved_or_unbound_request_never_reserves_run(models, bad):
    f, _service = models
    request, nonce = value(), "b" * 32
    if bad in {"model", "backend"}:
        request[bad] = "foreign"
    elif bad == "no_auto_policy":
        f.app.config["MEET_COMPANION_ASSIGNED_MODEL_PROFILE"]["auto_execute"] = False
    elif bad == "environment":
        f.app.config["MEET_COMPANION_ASSIGNED_MODEL_PROFILE"]["environment_digest"] = "unbound"
    elif bad == "nonce":
        nonce = "invalid"
    elif bad == "request_model":
        request["request"]["model"] = "foreign"
    elif bad == "stream":
        request["request"]["stream"] = True
    elif bad == "budget":
        request["request"]["max_tokens"] = 129
    else:
        request["request"]["messages"].append({"role": "tool", "content": "unregistered claim"})
    with pytest.raises(MeetError):
        reserve(models, request, nonce)
    with f.database.connect() as connection:
        assert not connection.execute(select(HubRunEvidenceIdentityDB.__table__)).all()


def test_model_cannot_omit_or_replace_verified_tool_message(models):
    f, _service = models
    tool = post(f)[1].get_json()
    request = value([tool])
    request["request"]["messages"][-1]["content"] = "model replacement"
    with pytest.raises(MeetError, match="tool_messages_unverified"):
        reserve(models, request)


@pytest.mark.parametrize("withdrawal", ["policy", "source", "chat_revision", "task_cancel"])
def test_withdrawal_after_reservation_never_accepts_model_result(models, withdrawal):
    f, _service = models
    reserved = reserve(models)
    if withdrawal == "policy":
        f.app.config["MEET_COMPANION_ASSIGNED_MODEL_PROFILE"]["auto_execute"] = False
    elif withdrawal == "source":
        capability = dict(f.capability) | {"revision": "2" * 40}
        f.resolver.resolve.side_effect = lambda **kwargs: capability
    elif withdrawal == "chat_revision":
        f.f.context["controls"]["chat"]["revision"] += 1
    else:
        f.f.task.status = "cancelled"
    with pytest.raises(MeetError):
        finish(models, reserved)
    assert state(f, reserved["run"]["run_id"])["state"] != "succeeded"


def test_unrelated_source_revision_does_not_revoke_current_chat_model_run(models):
    f, _service = models
    reserved = reserve(models)
    f.f.context["controls"]["revision"] += 1
    f.f.context["controls"]["audio"]["revision"] += 1
    assert finish(models, reserved)["result_digest"] == digest(generated())


@pytest.mark.parametrize("bad", ["foreign_model", "missing_usage", "too_many_tokens", "empty", "oversize"])
def test_invalid_model_result_fails_reserved_run(models, bad):
    f, _service = models
    reserved, result = reserve(models), generated()
    if bad == "foreign_model":
        result["model"] = "foreign"
    elif bad == "missing_usage":
        result.pop("usage")
    elif bad == "too_many_tokens":
        result["usage"]["completion_tokens"] = 129
    elif bad == "empty":
        result["choices"][0]["message"]["content"] = ""
    else:
        result["choices"][0]["message"]["content"] = "x" * 32_000
    with pytest.raises(MeetError, match="result_invalid"):
        finish(models, reserved, result)
    assert state(f, reserved["run"]["run_id"])["state"] == "failed"


def test_nonce_replay_and_duplicate_completion_cannot_execute_again(models):
    reserved = reserve(models)
    with pytest.raises(MeetError, match="replayed"):
        reserve(models)
    finish(models, reserved)
    with pytest.raises(MeetError, match="run_unavailable"):
        finish(models, reserved)


def test_tool_run_cannot_be_repurposed_as_model_run(models):
    f, _service = models
    tool = post(f)[1].get_json()
    reserved = reserve(models, value([tool]))
    forged = deepcopy(reserved) | {"run": tool["run"]}
    with pytest.raises(MeetError, match="run_unavailable"):
        finish(models, forged)
    assert state(f, reserved["run"]["run_id"])["state"] == "reserved"


def test_ollama_model_request_and_result_have_their_own_protocol_validation(models):
    f, _service = models
    f.app.config["MEET_COMPANION_ASSIGNED_MODEL_PROFILE"]["backend"] = "ollama"
    request = value()
    request["backend"] = "ollama"
    request["request"].pop("max_tokens")
    request["request"]["options"] = {"num_predict": 128}
    reserved = reserve(models, request)
    result = {"model": "synthetic-model", "done": True, "message": {"role": "assistant", "content": "synthetic"},
              "prompt_eval_count": 10, "eval_count": 3}
    assert finish(models, reserved, result)["result_digest"] == digest(result)


@pytest.mark.parametrize("valid", [True, False])
def test_model_tool_proposal_is_validated_without_executing_a_tool(models, valid):
    f, _service = models
    reserved, result = reserve(models), generated()
    calls = [{"function": {"name": "codecompass_search", "arguments": '{"query":"Meet"}'}}] if valid else [{}]
    result["choices"][0]["message"] = {"role": "assistant", "content": None, "tool_calls": calls}
    if valid:
        assert finish(models, reserved, result)["result_digest"] == digest(result)
    else:
        with pytest.raises(MeetError, match="result_invalid"):
            finish(models, reserved, result)
    assert not f.calls
