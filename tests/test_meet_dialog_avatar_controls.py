"""Neutral avatar activation is independent, default-off and owned by Hub CAS."""

from dataclasses import replace
from uuid import uuid4

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_controls import change_controls, controls_projection, initial_controls, parse_controls
from agent.services.meet_dialog_tasks import HubDialogTasks
from ananta_contracts.meet_dialog import validate_controls
from tests.test_meet_dialog_authority import fixture


def avatar_scope():
    f = fixture()
    f.context["capabilities"].extend(["avatar.publish", "speech.publish"])
    f.authority.policies[("tenant", "project")] = frozenset(f.context["capabilities"])
    f.context["controls"] = initial_controls(f.context["capabilities"], "mention", "off", f.now * 1000)
    return f, f.authority.current("task", "dispatch", "runtime")


def test_avatar_grant_does_not_activate_source_or_change_legacy_controls():
    legacy = fixture().context["controls"]
    assert "avatar" not in controls_projection(parse_controls(legacy))
    f, scope = avatar_scope()
    assert scope.controls.avatar.enabled is False
    updated = change_controls(
        scope, {"expected_revision": 1, "chat": False, "audio": False, "screen": False}, f.now * 1000 + 1
    )
    assert updated["avatar"] == f.context["controls"]["avatar"]
    assert updated["speech"] == f.context["controls"]["speech"]


def test_explicit_avatar_changes_only_its_source_revision_and_can_run_without_chat():
    f, scope = avatar_scope()
    body = {"expected_revision": 1, "chat": True, "audio": False, "screen": False, "avatar": True}
    activated = change_controls(scope, body, f.now * 1000 + 1)
    assert activated["avatar"] == {"enabled": True, "revision": 2, "since": f.now * 1000 + 1}
    assert all(activated[name] == f.context["controls"][name] for name in ("chat", "audio", "screen", "speech"))
    paused = change_controls(
        replace(scope, controls=parse_controls(activated)),
        body | {"expected_revision": 2, "avatar": False},
        f.now * 1000 + 2,
    )
    assert paused["avatar"]["revision"] == 3 and paused["avatar"]["enabled"] is False
    assert validate_controls(paused) is paused
    alone = replace(
        scope,
        capabilities=("avatar.publish",),
        chat_mode="off",
        controls=parse_controls(initial_controls(["avatar.publish"], "off", "off", f.now * 1000)),
    )
    assert change_controls(alone, body | {"chat": False}, f.now * 1000 + 1)["avatar"]["enabled"] is True


@pytest.mark.parametrize("enabled", [False, True])
def test_missing_avatar_grant_cannot_be_injected_even_when_paused(enabled):
    f = fixture()
    with pytest.raises(MeetError, match="capability_denied"):
        change_controls(
            f.authority.current("task", "dispatch", "runtime"),
            {"expected_revision": 1, "chat": True, "audio": False, "screen": False, "avatar": enabled},
            f.now * 1000,
        )
    f.context["controls"]["avatar"] = {"enabled": enabled, "revision": 1, "since": f.now * 1000}
    with pytest.raises(MeetError, match="capability_denied"):
        f.authority.current("task", "dispatch", "runtime")


@pytest.mark.parametrize(
    "avatar",
    [
        None,
        True,
        {},
        {"enabled": 1, "revision": 1, "since": 1},
        {"enabled": False, "revision": 2, "since": 1},
        {"enabled": False, "revision": 1, "since": 1, "profile": "url"},
    ],
)
def test_avatar_control_is_closed_and_has_no_implicit_asset_or_profile(avatar):
    with pytest.raises(ValueError):
        validate_controls(fixture().context["controls"] | {"avatar": avatar})


def test_revocation_stale_cas_and_legacy_update_cannot_reactivate_avatar():
    f, scope = avatar_scope()
    body = {"expected_revision": 1, "chat": True, "audio": False, "screen": False, "avatar": True}
    with pytest.raises(MeetError, match="conflict"):
        change_controls(scope, body | {"expected_revision": 2}, f.now * 1000)
    with pytest.raises(MeetError, match="capability_denied"):
        change_controls(replace(scope, capabilities=("chat.read", "chat.send", "speech.publish")), body, f.now * 1000)
    f.authority.policies[("tenant", "project")] = frozenset({"chat.read", "chat.send", "speech.publish"})
    with pytest.raises(MeetError, match="policy_denied"):
        f.authority.current("task", "dispatch", "runtime")


def test_actual_hub_cas_persists_avatar_once_without_changing_speech(app):
    f, scope = avatar_scope()
    scope = replace(scope, task_id=str(uuid4()))
    tasks = HubDialogTasks()
    with app.app_context():
        tasks.start(scope.task_id, scope.tenant_id, scope.project_id, f.context)
        activated = change_controls(
            scope,
            {"expected_revision": 1, "chat": True, "audio": False, "screen": False, "avatar": True},
            f.now * 1000 + 1,
        )
        assert tasks.set_controls(scope, activated) is True
        assert tasks.set_controls(scope, activated) is False
        stored = tasks.get_by_id(scope.task_id).worker_execution_context["meet_dialog"]["controls"]
        assert stored == activated and stored["speech"] == f.context["controls"]["speech"]
        assert tasks.finish_bound(scope.task_id, scope.lease_id, scope.runtime_id, "cancelled") is True
