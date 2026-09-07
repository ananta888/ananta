"""Actual Hub task CAS for passive avatar pins; automatic synthetic admission."""

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from agent.models.meet_avatar_selection import parse_avatar_selection
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_avatar_controls import advance_avatar_selection_controls
from agent.services.meet_dialog_avatar_selection import MeetDialogAvatarSelection
from agent.services.meet_dialog_controls import change_controls
from agent.services.meet_dialog_tasks import HubDialogTasks
from tests.test_meet_avatar_image_bridge import image_assignment
from tests.test_meet_dialog_avatar_controls import avatar_scope

pytestmark = pytest.mark.timeout(45)

PIN = {"organization_id": "org", "owner_kind": "organization", "owner_id": "org", "selection_digest": "a" * 64}


def image_selection():
    reference = image_assignment()["reference"] | {"tenant_id": "tenant", "project_id": "project"}
    return {"mode": "persona-image-v1", "reference": reference, "profile": PIN}


def system(*, negotiated=True):
    f, _ = avatar_scope()
    if negotiated:
        f.context["avatar_selection"] = {"mode": "neutral-ai-v1"}
    tasks, task_id = HubDialogTasks(), str(uuid4())
    tasks.start(task_id, "tenant", "project", f.context)
    f.authority.tasks = tasks
    scope = f.authority.current(task_id, "dispatch", "runtime")
    profiles = Mock()
    value = image_selection()
    profiles.select.return_value = (value["reference"], value["profile"])
    service = MeetDialogAvatarSelection(f.authority, tasks, profiles, clock=lambda: f.now + 1)
    principal = SimpleNamespace(tenant_id="tenant", project_id="project", subject_id="owner", roles={"user"})
    return SimpleNamespace(**locals())


def stored(f):
    return f.tasks.get_by_id(f.task_id).worker_execution_context["meet_dialog"]


def test_actual_selection_cas_is_content_free_once_only_and_preserves_independent_controls(app):
    with app.app_context():
        f = system()
        before = stored(f)
        assert f.service.select(f.principal, f.scope, {"expected_revision": 1, "profile": PIN}) == image_selection()
        after = stored(f)
        assert after["avatar_selection"] == image_selection()
        assert after["controls"]["avatar"] == {"enabled": False, "revision": 2, "since": (f.f.now + 1) * 1000}
        assert all(after["controls"][key] == before["controls"][key] for key in ("chat", "speech", "screen", "audio"))
        assert "png" not in json.dumps(after) and image_assignment()["png"] not in json.dumps(after)
        f.profiles.select.assert_called_once_with(f.principal, "project", PIN, "publish")
        with pytest.raises(MeetError, match="conflict"):
            f.service.select(f.principal, f.scope, {"expected_revision": 1, "profile": PIN})
        assert stored(f)["controls"]["revision"] == 2


def test_old_assignment_cannot_silently_upgrade_to_image_mode(app):
    with app.app_context():
        f = system(negotiated=False)
        with pytest.raises(MeetError, match="not_negotiated"):
            f.service.select(f.principal, f.scope, {"expected_revision": 1, "profile": PIN})
        f.profiles.select.assert_not_called()
        assert "avatar_selection" not in stored(f)


def test_explicit_neutral_selection_needs_no_image_lookup_and_still_fences_generation(app):
    with app.app_context():
        f = system()
        f.service.select(f.principal, f.scope, {"expected_revision": 1, "neutral": True})
        f.profiles.select.assert_not_called()
        assert stored(f)["avatar_selection"] == {"mode": "neutral-ai-v1"}
        assert stored(f)["controls"]["avatar"]["revision"] == 2
        assert stored(f)["controls"]["avatar"]["enabled"] is False


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
def test_foreign_or_worker_principal_cannot_change_presentation(app, field, value):
    with app.app_context():
        f = system()
        setattr(f.principal, field, value)
        with pytest.raises(MeetError, match="owner_required"):
            f.service.select(f.principal, f.scope, {"expected_revision": 1, "profile": PIN})
        f.profiles.select.assert_not_called()
        assert stored(f)["controls"]["revision"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"expected_revision": True, "neutral": True},
        {"expected_revision": 1, "neutral": False},
        {"expected_revision": 1, "profile": PIN, "neutral": True},
        {"expected_revision": 1, "url": "https://image"},
        {"expected_revision": 2, "profile": PIN},
    ],
)
def test_invalid_or_stale_selection_never_reads_assets_or_writes_task(app, payload):
    with app.app_context():
        f = system()
        with pytest.raises(MeetError):
            f.service.select(f.principal, f.scope, payload)
        f.profiles.select.assert_not_called()
        assert stored(f)["controls"]["revision"] == 1


@pytest.mark.parametrize("change", ["cancel", "controls", "image_denied"])
def test_concurrent_task_control_or_asset_denial_prevents_selection_commit(app, change):
    with app.app_context():
        f = system()

        def prepare(*_):
            if change == "cancel":
                f.tasks.finish_bound(f.task_id, "dispatch", "runtime", "cancelled")
            elif change == "controls":
                controls = change_controls(
                    f.scope, {"expected_revision": 1, "chat": False, "audio": False, "screen": False}, f.f.now * 1000
                )
                assert f.tasks.set_controls(f.scope, controls)
            else:
                raise MeetError("synthetic_image_denied", 403)
            return image_selection()["reference"], PIN

        f.profiles.select.side_effect = prepare
        with pytest.raises(MeetError):
            f.service.select(f.principal, f.scope, {"expected_revision": 1, "profile": PIN})
        assert stored(f)["avatar_selection"] == {"mode": "neutral-ai-v1"}


def test_task_cas_rechecks_runtime_selection_and_full_context(app):
    with app.app_context():
        f = system()
        controls = advance_avatar_selection_controls(f.scope, (f.f.now + 1) * 1000)
        assert not f.tasks.set_avatar_selection(replace(f.scope, runtime_id="foreign"), image_selection(), controls)
        assert not f.tasks.set_avatar_selection(
            replace(f.scope, avatar_selection=image_selection()), image_selection(), controls
        )
        assert not f.tasks.set_avatar_selection(replace(f.scope, avatar_selection=None), image_selection(), controls)
        f.tasks.finish_bound(f.task_id, "dispatch", "runtime", "cancelled")
        assert not f.tasks.set_avatar_selection(f.scope, image_selection(), controls)


@pytest.mark.parametrize("change", ["unfenced", "activate", "chat", "speech", "backwards", "extra"])
def test_persistence_cannot_change_image_without_fencing_or_toggle_independent_sources(app, change):
    with app.app_context():
        f = system()
        controls = advance_avatar_selection_controls(f.scope, (f.f.now + 1) * 1000)
        if change == "unfenced":
            controls = stored(f)["controls"]
        elif change == "activate":
            controls["avatar"]["enabled"] = True
        elif change in ("chat", "speech"):
            controls[change]["enabled"] = not controls[change]["enabled"]
        elif change == "backwards":
            controls["avatar"]["since"] = f.f.now * 1000 - 1
        else:
            controls["profile"] = PIN
        assert not f.tasks.set_avatar_selection(f.scope, image_selection(), controls)
        assert stored(f)["controls"]["revision"] == 1


@pytest.mark.parametrize("change", ["foreign", "voice", "revision", "png", "profile", "unknown"])
def test_stored_selection_contract_rejects_foreign_content_or_unclosed_metadata(change):
    value = image_selection()
    if change == "foreign":
        value["reference"]["tenant_id"] = "foreign"
    elif change == "voice":
        value["reference"]["kind"] = "voice"
    elif change == "revision":
        value["reference"]["revision"] = 2
    elif change == "png":
        value["png"] = "private"
    elif change == "profile":
        value["profile"] = PIN | {"publish": True}
    else:
        value["mode"] = "url"
    with pytest.raises(ValueError):
        parse_avatar_selection(value, "tenant", "project")


def test_authority_rejects_unassigned_or_malformed_avatar_selection():
    f, _ = avatar_scope()
    for value in [
        None,
        {},
        {"mode": "neutral-ai-v1", "png": "private"},
        image_selection() | {"permissions": ["publish"]},
    ]:
        f.context["avatar_selection"] = value
        with pytest.raises(MeetError, match="selection_invalid"):
            f.authority.current("task", "dispatch", "runtime")
    f.context["avatar_selection"] = {"mode": "neutral-ai-v1"}
    f.context["capabilities"].remove("avatar.publish")
    f.context["controls"].pop("avatar")
    with pytest.raises(MeetError, match="selection_invalid"):
        f.authority.current("task", "dispatch", "runtime")
