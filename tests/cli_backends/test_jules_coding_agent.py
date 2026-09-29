from threading import Event

from agent.cli_backends.jules import JulesCloudAgent, JulesRunRequest


class FakeJulesHttp:
    def __init__(self, states):
        self.states = iter(states)
        self.calls = []

    def request(self, method, path, *, payload=None):
        self.calls.append((method, path, payload))
        if path == "/v1alpha/sessions":
            return {"name": "sessions/session-1"}
        if path.endswith(":approvePlan"):
            return {}
        return next(self.states)


def _request(**overrides):
    values = {
        "prompt": "fix tests",
        "source": "sources/github/example",
        "starting_branch": "main",
        "timeout_seconds": 2,
    }
    values.update(overrides)
    return JulesRunRequest(**values)


def test_jules_completes_without_human_plan_approval() -> None:
    http = FakeJulesHttp([{"state": "AWAITING_PLAN_APPROVAL"}, {"state": "COMPLETED", "outputs": []}])

    result = JulesCloudAgent(http, poll_seconds=0.05).run(_request())

    assert result.status == "completed"
    assert any(path.endswith(":approvePlan") for _method, path, _payload in http.calls)
    create_payload = http.calls[0][2]
    assert create_payload["requirePlanApproval"] is False


def test_jules_fails_closed_when_auto_approval_is_not_authorized() -> None:
    http = FakeJulesHttp([{"state": "AWAITING_PLAN_APPROVAL"}])

    result = JulesCloudAgent(http, poll_seconds=0.05).run(_request(allow_plan_auto_approval=False))

    assert result.status == "blocked"
    assert result.reason_code == "plan_auto_approval_not_authorized"
    assert not any(path.endswith(":approvePlan") for _method, path, _payload in http.calls)


def test_jules_never_waits_for_user_feedback() -> None:
    http = FakeJulesHttp([{"state": "AWAITING_USER_FEEDBACK"}])

    result = JulesCloudAgent(http, poll_seconds=0.05).run(_request())

    assert result.status == "blocked"
    assert result.reason_code == "cloud_agent_input_required"


def test_jules_honors_cancellation_without_polling() -> None:
    cancellation = Event()
    cancellation.set()
    http = FakeJulesHttp([])

    result = JulesCloudAgent(http, poll_seconds=0.05).run(_request(), cancellation=cancellation)

    assert result.status == "cancelled"
    assert len(http.calls) == 1
