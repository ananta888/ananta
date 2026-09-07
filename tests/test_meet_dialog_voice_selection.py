"""Actual Hub Task CAS, with explicitly synthetic profile-admission test doubles."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from agent.models.meet_voice_selection import parse_voice_selection
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_controls import change_controls
from agent.services.meet_dialog_tasks import HubDialogTasks
from agent.services.meet_dialog_voice_selection import MeetDialogVoiceSelection, advance_voice_selection_controls
from ananta_contracts.meet_voice_catalog import DEFAULT_VOICE_ID
from ananta_contracts.persona_voice import inspect_voice_descriptor, voice_descriptor
from tests.test_meet_dialog_avatar_controls import avatar_scope

pytestmark = pytest.mark.timeout(45)
PIN = {"organization_id": "org", "owner_kind": "organization", "owner_id": "org", "selection_digest": "a" * 64}


def voice_selection():
    inspected = inspect_voice_descriptor(voice_descriptor(DEFAULT_VOICE_ID))
    return {
        "mode": "persona-voice-v1",
        "profile": dict(PIN),
        "reference": {
            "tenant_id": "tenant",
            "project_id": "project",
            "artifact_id": "synthetic-voice-reference",
            "revision": 1,
            "kind": "voice",
            "sha256": inspected.source_sha256,
            "classification": "test_only",
        },
    }


def system(*, negotiated=True, speech_enabled=False):
    fixture, _ = avatar_scope()
    fixture.context["controls"]["speech"]["enabled"] = speech_enabled
    fixture.context["avatar_selection"] = {"mode": "neutral-ai-v1"}
    if negotiated:
        fixture.context["voice_selection"] = {"mode": "configured-piper-v1"}
    tasks, task_id = HubDialogTasks(), str(uuid4())
    tasks.start(task_id, "tenant", "project", fixture.context)
    fixture.authority.tasks = tasks
    scope = fixture.authority.current(task_id, "dispatch", "runtime")
    profiles = Mock(select=Mock(return_value=(voice_selection()["reference"], dict(PIN))))
    service = MeetDialogVoiceSelection(fixture.authority, tasks, profiles, clock=lambda: fixture.now + 1)
    principal = SimpleNamespace(tenant_id="tenant", project_id="project", subject_id="owner", roles={"user"})
    return SimpleNamespace(**locals())


def stored(case):
    return case.tasks.get_by_id(case.task_id).worker_execution_context["meet_dialog"]


@pytest.mark.parametrize("enabled", [False, True])
def test_voice_selection_is_passive_once_only_and_preserves_other_sources(app, enabled):
    with app.app_context():
        case = system(speech_enabled=enabled)
        before = stored(case)
        assert (
            case.service.select(case.principal, case.scope, {"expected_revision": 1, "profile": PIN})
            == voice_selection()
        )
        after = stored(case)
        assert after["voice_selection"] == voice_selection()
        assert after["controls"]["speech"] == {"enabled": enabled, "revision": 2, "since": (case.fixture.now + 1) * 1000}
        assert all(after["controls"][key] == before["controls"][key] for key in ("chat", "avatar", "screen", "audio"))
        assert after["avatar_selection"] == before["avatar_selection"]
        case.profiles.select.assert_called_once_with(case.principal, "project", PIN, "publish")
        with pytest.raises(MeetError, match="conflict"):
            case.service.select(case.principal, case.scope, {"expected_revision": 1, "profile": PIN})


def test_unnegotiated_assignment_cannot_silently_gain_voice_selection(app):
    with app.app_context():
        case = system(negotiated=False)
        with pytest.raises(MeetError, match="not_negotiated"):
            case.service.select(case.principal, case.scope, {"expected_revision": 1, "profile": PIN})
        case.profiles.select.assert_not_called()
        assert "voice_selection" not in stored(case)


def test_explicit_configured_voice_return_never_resolves_or_activates_persona(app):
    with app.app_context():
        case = system()
        case.service.select(case.principal, case.scope, {"expected_revision": 1, "configured": True})
        case.profiles.select.assert_not_called()
        assert stored(case)["voice_selection"] == {"mode": "configured-piper-v1"}
        assert stored(case)["controls"]["speech"]["revision"] == 2
        assert stored(case)["controls"]["speech"]["enabled"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("subject_id", "other"),
        ("tenant_id", "other"),
        ("project_id", "other"),
        ("roles", {"worker"}),
        ("roles", {"service"}),
    ],
)
def test_foreign_or_execution_principal_cannot_choose_voice(app, field, value):
    with app.app_context():
        case = system()
        setattr(case.principal, field, value)
        with pytest.raises(MeetError, match="owner_required"):
            case.service.select(case.principal, case.scope, {"expected_revision": 1, "profile": PIN})
        case.profiles.select.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"expected_revision": True, "configured": True},
        {"expected_revision": 1, "configured": False},
        {"expected_revision": 1, "profile": PIN, "configured": True},
        {"expected_revision": 1, "voice_id": DEFAULT_VOICE_ID},
        {"expected_revision": 2, "profile": PIN},
    ],
)
def test_invalid_or_stale_selection_cannot_read_profile_or_modify_task(app, payload):
    with app.app_context():
        case = system()
        with pytest.raises(MeetError):
            case.service.select(case.principal, case.scope, payload)
        case.profiles.select.assert_not_called()
        assert stored(case)["controls"]["revision"] == 1


@pytest.mark.parametrize("change", ["cancel", "controls", "denied"])
def test_task_or_control_changes_during_profile_lookup_prevent_voice_commit(app, change):
    with app.app_context():
        case = system()

        def select(*_):
            if change == "cancel":
                case.tasks.finish_bound(case.task_id, "dispatch", "runtime", "cancelled")
            elif change == "controls":
                controls = change_controls(
                    case.scope,
                    {"expected_revision": 1, "chat": False, "audio": False, "screen": False},
                    case.fixture.now * 1000,
                )
                assert case.tasks.set_controls(case.scope, controls)
            else:
                raise MeetError("synthetic_voice_denied", 403)
            return voice_selection()["reference"], PIN

        case.profiles.select.side_effect = select
        with pytest.raises(MeetError):
            case.service.select(case.principal, case.scope, {"expected_revision": 1, "profile": PIN})
        assert stored(case)["voice_selection"] == {"mode": "configured-piper-v1"}


@pytest.mark.parametrize("change", ["unfenced", "activate", "deactivate", "avatar", "backwards", "extra"])
def test_persistence_rejects_unfenced_selection_and_implicit_control_changes(app, change):
    with app.app_context():
        case = system(speech_enabled=change == "deactivate")
        controls = advance_voice_selection_controls(case.scope, (case.fixture.now + 1) * 1000)
        if change == "unfenced":
            controls = stored(case)["controls"]
        elif change == "activate":
            controls["speech"]["enabled"] = True
        elif change == "deactivate":
            controls["speech"]["enabled"] = False
        elif change == "avatar":
            controls["avatar"]["revision"] += 1
        elif change == "backwards":
            controls["speech"]["since"] = 1
        else:
            controls["extra"] = True
        assert not case.tasks.set_voice_selection(case.scope, voice_selection(), controls)
        assert stored(case)["controls"]["revision"] == 1


def test_actual_voice_cas_checks_runtime_selection_and_terminal_state(app):
    with app.app_context():
        case = system()
        controls = advance_voice_selection_controls(case.scope, (case.fixture.now + 1) * 1000)
        for scope in (
            replace(case.scope, runtime_id="foreign"),
            replace(case.scope, voice_selection=voice_selection()),
            replace(case.scope, voice_selection=None),
        ):
            assert not case.tasks.set_voice_selection(scope, voice_selection(), controls)
        case.tasks.finish_bound(case.task_id, "dispatch", "runtime", "cancelled")
        assert not case.tasks.set_voice_selection(case.scope, voice_selection(), controls)


@pytest.mark.parametrize(
    "change", [{"model_url": "https://caller"}, {"mode": "neutral-ai-v1"}, {"profile": {}}, {"reference": {}}]
)
def test_voice_selection_contract_is_closed(change):
    with pytest.raises(ValueError):
        parse_voice_selection(voice_selection() | change, "tenant", "project")
