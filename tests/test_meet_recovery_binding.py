"""Negotiation and immutable original scope stay separate from source CAS."""

from dataclasses import replace

import pytest

from agent.models.meet_machine_principal import MeetMachinePrincipal
from agent.models.meet_preauthorization_binding import assignment_projection
from agent.models.meet_recovery_binding import recovery_owner
from agent.services.meet_contract import MeetError
from tests.test_meet_dialog_authority import fixture
from tests.test_meet_observation_transport import transport


def scope():
    f = fixture()
    f.context["reconnect"] = True
    return f, f.authority.current("task", "dispatch", "runtime")


def test_recovery_flag_is_immutable_in_original_preauthorization_projection():
    f, original = scope()
    context = f.context | {"binding_task_id": "parent"}
    args = (original.task_id, original.tenant_id, original.project_id, original.origin)
    assert assignment_projection(*args, context)["reconnect"] is True
    legacy = dict(context)
    del legacy["reconnect"]
    assert "reconnect" not in assignment_projection(*args, legacy)
    for flag in (False, 1, "true", None):
        with pytest.raises(MeetError):
            assignment_projection(*args, context | {"reconnect": flag})
        f.context["reconnect"] = flag
        with pytest.raises(MeetError, match="negotiation_invalid"):
            f.authority.current("task", "dispatch", "runtime")


def test_recovery_does_not_infer_negotiation_from_the_existing_task():
    f = fixture()
    with pytest.raises(MeetError, match="not_negotiated"):
        recovery_owner(f.authority.current("task", "dispatch", "runtime"))


@pytest.mark.parametrize(
    "field",
    [
        "task_id",
        "lease_id",
        "tenant_id",
        "project_id",
        "runtime_id",
        "session_id",
        "room_id",
        "origin",
        "owner_subject",
        "binding_task_id",
        "deadline",
        "capabilities",
        "chat_mode",
        "audio_mode",
        "browser_workspace",
    ],
)
def test_every_original_identity_or_mode_change_produces_a_different_recovery_binding(field):
    _, original = scope()
    value = (
        ()
        if field == "capabilities"
        else original.deadline + 1
        if field == "deadline"
        else True
        if field == "browser_workspace"
        else "foreign"
    )
    changed = replace(original, **{field: value})
    assert recovery_owner(changed) != recovery_owner(original)


def test_control_cas_and_asset_selection_do_not_rewrite_recovery_identity_but_negotiation_does():
    _, original = scope()
    original = replace(
        original, avatar_selection={"mode": "neutral-ai-v1"}, voice_selection={"mode": "configured-piper-v1"}
    )
    owner = recovery_owner(original)
    updated = replace(
        original,
        controls=replace(original.controls, revision=2),
        avatar_selection={"mode": "image", "asset": "synthetic"},
        voice_selection={"mode": "other"},
    )
    assert recovery_owner(updated) == owner
    assert recovery_owner(replace(original, avatar_selection=None)) != owner
    assert recovery_owner(replace(original, voice_selection=None)) != owner


def test_verified_role_principal_is_bound_and_its_change_cannot_reuse_old_attempts():
    _, original = scope()
    principal = MeetMachinePrincipal("tenant", "project", "org", "role", "assignment", "a" * 64)
    first = recovery_owner(replace(original, machine_principal=principal))
    assert first != recovery_owner(original)
    assert first != recovery_owner(replace(original, machine_principal=replace(principal, assignment_id="next")))


def test_negotiated_membership_cannot_silently_use_legacy_transport_without_coordinator(monkeypatch):
    client, authority, opener, _, _, args = transport(monkeypatch)
    authority.current.return_value = replace(args[0], reconnect=True)
    with pytest.raises(MeetError, match="coordinator_required"):
        client.observe("task", "dispatch", "runtime", args[2])
    opener.open.assert_not_called()
    client.issuer.issue_dialog.assert_not_called()
