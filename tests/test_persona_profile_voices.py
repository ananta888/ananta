"""Passive organization/team/agent voice selection with actual test-only asset receipts."""

from types import SimpleNamespace

import pytest
from sqlalchemy import update

from agent.db_models import OrganizationInstanceDB
from agent.models.persona_media import MediaSelection, PersonaProfileSelection
from agent.services.persona_profile_voices import PersonaProfileVoices
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_media import profile
from tests.test_persona_profile_service import save
from tests.test_persona_profile_service import system as system
from tests.test_persona_voice_asset_service import admit
from tests.test_persona_voice_asset_service import voice_assets as voice_assets
from tests.test_persona_voice_tasks import voice_task as voice_task


@pytest.fixture
def selected_voice(request):
    scope = request.getfixturevalue("system")
    fixture = request.getfixturevalue("voice_assets")
    fixture.case.policy.access = scope.service.access
    asset = admit(fixture)
    voices = PersonaProfileVoices(fixture.service)
    scope.service.voices = voices
    scope.service.images = None
    return SimpleNamespace(scope=scope, fixture=fixture, asset=asset, voices=voices)


def selected(case, kind="organization", **changes):
    return profile(kind, voice=MediaSelection(state="asset", asset=case.asset.voice), **changes)


@pytest.mark.parametrize("kind", ["organization", "team", "agent"])
def test_voice_profile_roundtrips_actual_owner_scope_and_private_reference(selected_voice, kind):
    case = selected_voice
    value = selected(case, kind)
    save(case.scope, value)
    current = case.scope.service.current(case.scope.principal, "project", "org", kind, value.owner_id)
    assert current["profile"] == value.model_dump(mode="json") and current["media_available"]
    case.voices.require_reference(case.scope.principal, case.asset.voice)


def test_voice_inheritance_and_disabled_override_do_not_grant_publication(selected_voice):
    case, scope = selected_voice, selected_voice.scope
    save(scope, selected(case))
    save(scope, profile("team", voice=MediaSelection(state="inherit")))
    save(scope, profile("agent", voice=MediaSelection(state="inherit")))
    result = scope.service.effective(scope.principal, "project", "org", "agent", "agent")
    voice = next(item for item in result["media"] if item["kind"] == "voice")
    assert voice["asset"] == case.asset.voice.model_dump(mode="json")
    assert [item["owner_kind"] for item in voice["origins"]] == ["agent", "team", "organization"]
    assert voice["preview_allowed"] and not voice["publication_checked"] and not result["runtime_bound"]
    save(scope, profile("team", revision=2, voice=MediaSelection(state="disabled")), expected=1)
    result = scope.service.effective(scope.principal, "project", "org", "agent", "agent")
    voice = next(item for item in result["media"] if item["kind"] == "voice")
    assert voice["state"] == "disabled" and voice["asset"] is None


def test_revoked_voice_hides_reference_without_trapping_profile_cas(selected_voice):
    case, scope = selected_voice, selected_voice.scope
    save(scope, selected(case))
    case.fixture.service.revoke(
        case.fixture.case.principal, "project", case.asset.voice.artifact_id, expected_revision=2
    )
    result = scope.service.current(scope.principal, "project", "org", "organization", "org")
    assert result["profile"] is None and result["revision"] == 1 and not result["media_available"]
    save(scope, profile(revision=2, voice=MediaSelection(state="disabled")), expected=1)


@pytest.mark.parametrize(
    "changes", [{"tenant_id": "foreign"}, {"kind": "image"}, {"revision": 2}, {"sha256": "f" * 64}]
)
def test_voice_reference_cannot_replace_scope_kind_or_immutable_pin(selected_voice, changes):
    with pytest.raises(PermissionError):
        selected_voice.voices.require_reference(
            selected_voice.scope.principal, selected_voice.asset.voice.model_copy(update=changes)
        )


def test_voice_lookup_rechecks_catalog_after_policy_io(selected_voice):
    case = selected_voice
    original = case.fixture.case.policy.require_asset

    def require(principal, asset, purpose):
        original(principal, asset, purpose)
        case.fixture.service.revoke(principal, "project", asset.voice.artifact_id, expected_revision=2)

    case.fixture.case.policy.require_asset = require
    with pytest.raises((ValueError, PermissionError)):
        case.voices.reference(case.scope.principal, "project", case.asset.voice.artifact_id)


@pytest.mark.parametrize("state", ["asset", "disabled", "missing"])
def test_voice_execution_resolves_exact_pin_without_silent_fallback(selected_voice, state):
    case, scope = selected_voice, selected_voice.scope
    save(scope, selected(case) if state == "asset" else profile(voice=MediaSelection(state=state)))
    with scope.engine.begin() as connection:
        connection.execute(update(OrganizationInstanceDB).values(lifecycle="active"))
    result = scope.service.effective(scope.principal, "project", "org", "organization", "org")
    pin = PersonaProfileSelection.model_validate(result["selection"])
    if state == "asset":
        assert scope.service.for_voice_execution(scope.principal, "project", pin) == case.asset.voice.model_dump(
            mode="json"
        )
        with pytest.raises(PermissionError, match="profile_changed"):
            scope.service.for_voice_execution(
                scope.principal, "project", pin.model_copy(update={"selection_digest": "f" * 64})
            )
    else:
        with pytest.raises(PermissionError):
            scope.service.for_voice_execution(scope.principal, "project", pin)


def test_unconfigured_voice_port_cannot_save_voice_profile(selected_voice):
    selected_voice.scope.service.voices = None
    with pytest.raises(ValueError, match="not_supported"):
        save(selected_voice.scope, selected(selected_voice))
