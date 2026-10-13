"""Actual model HTTP helpers; synthetic Task/model fixtures and bounded cancellation."""

import threading

import pytest

from tests.meet.test_meet_companion_model_runs import generated, state, value
from tests.meet.test_meet_companion_model_runs import models as models
from tests.meet.test_meet_companion_task_control_http import running_hub
from tests.meet.test_meet_companion_task_grant_client import assignment
from tests.meet.test_meet_companion_task_grants import assigned as assigned
from tests.meet.test_meet_companion_task_model_http import http as http
from tests.meet.test_meet_companion_task_tools import KEY
from tests.meet.test_meet_companion_task_tools import hub as hub
from worker.meet_media.companion_task_grant_client import AssignedCompanionGrantClient
from worker.meet_media.companion_task_model_process import AssignedCompanionModelProcess

pytestmark = pytest.mark.timeout(15)


def process(f, endpoint):
    full = assignment() | {"session_id": "hub-session", "deadline": f.f.context["deadline"]}
    return AssignedCompanionModelProcess(AssignedCompanionGrantClient(full, endpoint, KEY))


def test_actual_owned_helper_reserves_and_finishes_under_cooperative_owner(http):
    ticks = []
    with running_hub(http.app) as endpoint:
        helper = process(http, endpoint)
        reserved = helper({"action": "reserve", "payload": value()}, cooperate=lambda: ticks.append(True))
        assert state(http, reserved["run"]["run_id"])["state"] == "reserved"
        completed = helper({"action": "finish", "payload": {"run": reserved["run"],
            "input_source": reserved["input_source"], "result": generated()}}, cooperate=lambda: ticks.append(True))
        assert completed["run"] == reserved["run"] and len(ticks) >= 4


def test_pending_http_cancellation_kills_owned_helper_without_accepting_result(http, monkeypatch):
    import worker.meet_media.companion_task_model_process as module

    entered, release = threading.Event(), threading.Event()
    transports = []
    actual_transport = module.CompanionProcessTransport

    def tracked(*args, **kwargs):
        instance = actual_transport(*args, **kwargs)
        transports.append(instance)
        return instance

    monkeypatch.setattr(module, "CompanionProcessTransport", tracked)

    @http.app.before_request
    def hold():
        entered.set()
        assert release.wait(2), "bounded synthetic HTTP delay"

    def cooperate():
        if entered.is_set():
            http.f.task.status = "cancelled"
            release.set()
            raise PermissionError("synthetic_task_cancelled")

    with running_hub(http.app) as endpoint:
        helper = process(http, endpoint)
        try:
            with pytest.raises(PermissionError, match="task_cancelled"):
                helper({"action": "reserve", "payload": value()}, cooperate=cooperate)
        finally:
            release.set()
        assert len(transports) == 1 and transports[0].process.poll() is not None


def test_model_arguments_cannot_replace_helper_transport_authority(http):
    with running_hub(http.app) as endpoint:
        helper = process(http, endpoint)
        with pytest.raises(ValueError, match="model_port_invalid"):
            helper({"action": "reserve", "payload": value(), "endpoint": "foreign"}, cooperate=lambda: None)
