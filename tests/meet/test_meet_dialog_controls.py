"""Independent source revisions fence old work without granting capabilities."""
from dataclasses import replace
import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_controls import change_controls, parse_controls, chat_policy_revision
from tests.test_meet_dialog_authority import fixture


def test_pause_resume_preserves_unrelated_source_revisions_and_fences_old_chat():
    f = fixture(); scope = f.authority.current("task", "dispatch", "runtime")
    paused = change_controls(scope, {"expected_revision": 1, "chat": False, "audio": False, "screen": False}, f.now * 1000 + 1)
    assert paused["revision"] == 2 and paused["chat"]["revision"] == 2
    assert paused["audio"] == f.context["controls"]["audio"] and paused["screen"] == f.context["controls"]["screen"]
    resumed = change_controls(replace(scope, controls=parse_controls(paused)),
        {"expected_revision": 2, "chat": True, "audio": False, "screen": False}, f.now * 1000 + 2)
    assert resumed["chat"]["revision"] == 3 and resumed["chat"]["since"] > paused["chat"]["since"]
    assert chat_policy_revision(4, 1) != chat_policy_revision(4, 3)


@pytest.mark.parametrize("payload", [
    {"expected_revision": 0, "chat": True, "audio": False, "screen": False},
    {"expected_revision": True, "chat": True, "audio": False, "screen": False},
    {"expected_revision": 1, "chat": True, "audio": False, "screen": True},
    {"expected_revision": 1, "chat": True, "audio": True, "screen": False},
    {"expected_revision": 1, "chat": True, "audio": False, "screen": False, "capability": "screen.publish"},
])
def test_control_cas_cannot_escalate_disabled_policy_or_unassigned_source(payload):
    f = fixture()
    with pytest.raises(MeetError): change_controls(f.authority.current("task", "dispatch", "runtime"), payload, f.now * 1000)
