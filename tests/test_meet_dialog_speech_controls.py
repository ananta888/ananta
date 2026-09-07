"""Optional independently revisioned speech control never widens a task grant."""

from dataclasses import replace
from uuid import uuid4

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_controls import change_controls, controls_projection, initial_controls, parse_controls
from agent.services.meet_dialog_tasks import HubDialogTasks
from ananta_contracts.meet_dialog import validate_controls
from tests.test_meet_dialog_authority import fixture


def speech_scope():
    f = fixture()
    f.context["capabilities"].append("speech.publish")
    f.authority.policies[("tenant", "project")] = frozenset(f.context["capabilities"])
    f.context["controls"] = initial_controls(f.context["capabilities"], "mention", "off", f.now * 1000)
    return f, f.authority.current("task", "dispatch", "runtime")


def test_legacy_wire_shape_and_old_control_bodies_remain_unchanged():
    f = fixture()
    value = f.context["controls"]
    assert set(value) == {"revision", "chat", "audio", "screen"}
    assert controls_projection(parse_controls(value)) == value
    assert parse_controls(value).speech is None
    updated = change_controls(
        f.authority.current("task", "dispatch", "runtime"),
        {"expected_revision": 1, "chat": False, "audio": False, "screen": False},
        f.now * 1000 + 1,
    )
    assert "speech" not in updated


def test_pause_and_resume_only_advance_the_speech_revision():
    f, scope = speech_scope()
    assert scope.controls.speech.enabled is True
    body = {"expected_revision": 1, "chat": True, "audio": False, "screen": False, "speech": False}
    paused = change_controls(scope, body, f.now * 1000 + 1)
    assert paused["speech"] == {"enabled": False, "revision": 2, "since": f.now * 1000 + 1}
    assert all(paused[name] == f.context["controls"][name] for name in ("chat", "audio", "screen"))
    resumed = change_controls(
        replace(scope, controls=parse_controls(paused)),
        body | {"expected_revision": 2, "speech": True},
        f.now * 1000 + 2,
    )
    assert resumed["speech"]["revision"] == 3 and resumed["speech"]["enabled"] is True
    assert validate_controls(resumed) is resumed


def test_old_client_update_preserves_new_speech_state_without_implicit_activation():
    f, scope = speech_scope()
    paused = replace(scope, controls=replace(scope.controls, speech=replace(scope.controls.speech, enabled=False)))
    updated = change_controls(
        paused, {"expected_revision": 1, "chat": False, "audio": False, "screen": False}, f.now * 1000 + 1
    )
    assert updated["speech"] == controls_projection(paused.controls)["speech"]
    assert updated["chat"]["enabled"] is False


@pytest.mark.parametrize("enabled", [False, True])
def test_payload_cannot_create_an_unassigned_source_even_when_disabled(enabled):
    f = fixture()
    with pytest.raises(MeetError, match="capability_denied"):
        change_controls(
            f.authority.current("task", "dispatch", "runtime"),
            {"expected_revision": 1, "chat": True, "audio": False, "screen": False, "speech": enabled},
            f.now * 1000,
        )
    f.context["controls"]["speech"] = {"enabled": enabled, "revision": 1, "since": f.now * 1000}
    with pytest.raises(MeetError, match="capability_denied"):
        f.authority.current("task", "dispatch", "runtime")


@pytest.mark.parametrize(
    "speech",
    [
        None,
        True,
        {},
        {"enabled": "yes", "revision": 1, "since": 1},
        {"enabled": False, "revision": 2, "since": 1},
        {"enabled": False, "revision": 1, "since": 1, "extra": True},
    ],
)
def test_speech_control_shape_is_closed_and_bounded(speech):
    f = fixture()
    with pytest.raises(ValueError):
        validate_controls(f.context["controls"] | {"speech": speech})


def test_revoked_capability_off_policy_and_stale_cas_never_authorize_speech():
    f, scope = speech_scope()
    body = {"expected_revision": 1, "chat": True, "audio": False, "screen": False, "speech": True}
    with pytest.raises(MeetError, match="conflict"):
        change_controls(scope, body | {"expected_revision": 2}, f.now * 1000)
    with pytest.raises(MeetError, match="capability_denied"):
        change_controls(replace(scope, capabilities=("chat.read", "chat.send")), body, f.now * 1000)
    f.context["chat_mode"] = "off"
    with pytest.raises(MeetError, match="capability_denied"):
        f.authority.current("task", "dispatch", "runtime")


def test_real_hub_compare_and_set_preserves_the_optional_projection(app):
    f, scope = speech_scope()
    task_id = str(uuid4())
    scope = replace(scope, task_id=task_id)
    tasks = HubDialogTasks()
    with app.app_context():
        tasks.start(task_id, scope.tenant_id, scope.project_id, f.context)
        paused = change_controls(
            scope,
            {"expected_revision": 1, "chat": True, "audio": False, "screen": False, "speech": False},
            f.now * 1000 + 1,
        )
        assert tasks.set_controls(scope, paused) is True
        assert tasks.set_controls(scope, paused) is False
        stored = tasks.get_by_id(task_id).worker_execution_context["meet_dialog"]["controls"]
        assert stored == paused
        assert stored["chat"] == f.context["controls"]["chat"]
        assert tasks.finish_bound(task_id, scope.lease_id, scope.runtime_id, "cancelled") is True
