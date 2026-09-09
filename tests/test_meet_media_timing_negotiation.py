"""Hub-owned additive timing mode binds the original dispatch, not browser claims."""

import hashlib
import json
from dataclasses import asdict, replace

import pytest

from agent.bootstrap.meet_media_timing import configured_media_timing
from agent.models.meet_dialog_phase import phase_binding
from agent.models.meet_preauthorization_binding import assignment_projection
from agent.models.meet_recovery_binding import recovery_owner
from agent.services.meet_contract import MeetError
from agent.services.meet_media_timing_policy import negotiated_media_timing, require_media_timing_mode
from ananta_contracts.meet_dialog import validate_assignment
from tests.test_meet_dialog_authority import fixture
from tests.test_meet_dialog_avatar_negotiation import system
from tests.test_meet_dialog_transport import assignment


@pytest.mark.parametrize("value,expected", [(None, False), ("0", False), ("1", True)])
def test_explicit_operator_config_preserves_disabled_default(monkeypatch, value, expected):
    monkeypatch.delenv("ANANTA_MEET_MEDIA_TIMING", raising=False)
    if value is not None:
        monkeypatch.setenv("ANANTA_MEET_MEDIA_TIMING", value)
    assert configured_media_timing() is expected
    assert negotiated_media_timing(expected) == ({"media_timing": True} if expected else {})


@pytest.mark.parametrize("value", ["true", "false", "", "1 ", "2"])
def test_config_never_coerces_unknown_operator_values(monkeypatch, value):
    monkeypatch.setenv("ANANTA_MEET_MEDIA_TIMING", value)
    with pytest.raises(ValueError, match="config_invalid"):
        configured_media_timing()


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_present_mode_must_be_exact_true_at_every_immutable_boundary(value):
    f = fixture()
    context = f.context | {"binding_task_id": "parent", "media_timing": value}
    with pytest.raises(MeetError, match="binding_invalid"):
        assignment_projection("task", "tenant", "project", f.binding.profile.origin, context)
    f.context["media_timing"] = value
    with pytest.raises(MeetError, match="negotiation_invalid"):
        f.authority.current("task", "dispatch", "runtime")
    with pytest.raises(ValueError, match="negotiation_invalid"):
        validate_assignment(assignment() | {"media_timing": value}, f.now)


def test_profile_is_bound_without_changing_legacy_projection_or_recovery_digest():
    f = fixture()
    scope = replace(f.authority.current("task", "dispatch", "runtime"), reconnect=True)
    context = f.context | {"binding_task_id": "parent"}
    args = ("task", "tenant", "project", f.binding.profile.origin)
    assert "media_timing" not in assignment_projection(*args, context)
    assert assignment_projection(*args, context | {"media_timing": True})["media_timing"] is True
    assert phase_binding(*args[:3], context) != phase_binding(*args[:3], context | {"media_timing": True})
    assert phase_binding(*args[:3], context | {"reconnect": True}) != phase_binding(
        *args[:3], context | {"media_timing": True}
    )
    old = asdict(scope)
    del old["controls"], old["media_timing"]
    for name in ("avatar_selection", "voice_selection"):
        old[name] = old[name] is not None
    legacy_digest = hashlib.sha256(
        json.dumps(
            {"schema": "ananta.meet-recovery-binding.v1", "authority": old},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    assert recovery_owner(scope).assignment_digest == legacy_digest
    assert recovery_owner(replace(scope, media_timing=True)).assignment_digest != legacy_digest
    require_media_timing_mode(scope, False)
    with pytest.raises(MeetError, match="mode_changed"):
        require_media_timing_mode(scope, True)


@pytest.mark.parametrize("enabled", [False, True])
def test_actual_hub_task_and_worker_envelope_inherit_only_hub_option(app, enabled):
    with app.app_context():
        f = system()
        f.service.media_timing = enabled
        with pytest.raises(MeetError):
            f.service.start(f.principal, "project", f.payload | {"media_timing": True})
        f.worker.start_dialog.assert_not_called()
        result = f.service.start(f.principal, "project", f.payload)
        task = f.tasks.get_by_id(result["task_id"])
        context = task.worker_execution_context["meet_dialog"]
        wire = f.worker.start_dialog.call_args.args[0]
        assert (context.get("media_timing") is True) is enabled
        assert (wire.get("media_timing") is True) is enabled
        assert validate_assignment(wire, f.f.now) == wire
        scope = f.service.authority.current(task.id, context["lease_id"], context["runtime_id"])
        assert scope.media_timing is enabled
        f.service.media_timing = not enabled
        with pytest.raises(MeetError, match="mode_changed"):
            f.service.exchange(
                {
                    "task_id": task.id,
                    "lease_id": scope.lease_id,
                    "runtime_id": scope.runtime_id,
                    "meet_session_id": "ms_" + "a" * 32,
                }
            )
        f.meet.inspect.assert_not_called()
