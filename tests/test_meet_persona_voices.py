"""Voice Meet adapter checks real policy/receipt and topology; no synthesis or live publication."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import update

from agent.db_models import OrganizationInstanceDB
from agent.models.persona_media import MediaSelection
from agent.services.meet_contract import MeetError
from agent.services.meet_persona_voice_profiles import MeetPersonaVoiceProfiles
from agent.services.meet_persona_voices import MeetPersonaVoices
from ananta_contracts.meet_speech import speech_profile
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_media import profile
from tests.test_persona_profile_service import save
from tests.test_persona_profile_service import system as system
from tests.test_persona_profile_voices import selected
from tests.test_persona_profile_voices import selected_voice as selected_voice
from tests.test_persona_voice_asset_service import voice_assets as voice_assets
from tests.test_persona_voice_tasks import voice_task as voice_task


@pytest.fixture
def bound_voice(request):
    case = request.getfixturevalue("selected_voice")
    save(case.scope, selected(case))
    with case.scope.engine.begin() as connection:
        connection.execute(update(OrganizationInstanceDB).values(lifecycle="active"))
    scope = case.scope
    pin = scope.service.effective(scope.principal, "project", "org", "organization", "org")["selection"]
    voices = MeetPersonaVoices(case.fixture.service)
    return SimpleNamespace(case=case, pin=pin, voices=voices, profiles=MeetPersonaVoiceProfiles(scope.service, voices))


def prepare(fixture, purpose="publish", **kwargs):
    return fixture.profiles.prepare(fixture.case.scope.principal, "project", fixture.pin, purpose, **kwargs)


def test_voice_profile_resolves_exact_descriptor_and_catalog_budget(bound_voice):
    fixture = bound_voice
    result, pin = prepare(fixture, max_seconds=12)
    assert pin == fixture.pin
    assert result == {
        "reference": fixture.case.asset.voice.model_dump(mode="json"),
        "speech_profile": speech_profile(max_seconds=12),
    }
    assert not set(result) & {"grant", "model_path", "model_url", "source_id", "run_id", "descriptor"}


def test_passive_selection_does_not_read_bytes_or_activate_speech(bound_voice):
    fixture = bound_voice
    fixture.case.fixture.service.storage = Mock()
    reference, pin = fixture.profiles.select(fixture.case.scope.principal, "project", fixture.pin, "publish")
    assert reference == fixture.case.asset.voice.model_dump(mode="json") and pin == fixture.pin
    fixture.case.fixture.service.storage.read.assert_not_called()


@pytest.mark.parametrize("change", ["asset", "policy", "profile", "topology", "foreign", "budget"])
def test_revoked_or_changed_selection_cannot_prepare_another_voice(bound_voice, change):
    fixture, case = bound_voice, bound_voice.case
    if change == "asset":
        case.fixture.service.revoke(case.scope.principal, "project", case.asset.voice.artifact_id, expected_revision=2)
    elif change == "policy":
        case.fixture.case.policy.revoke_policy(
            case.scope.principal, "project", case.fixture.case.permission.source.source_id, expected_revision=1
        )
    elif change == "profile":
        save(case.scope, profile(revision=2, voice=MediaSelection(state="disabled")), expected=1)
    elif change == "topology":
        with case.scope.engine.begin() as connection:
            connection.execute(update(OrganizationInstanceDB).values(lifecycle="paused"))
    elif change == "foreign":
        fixture.pin = fixture.pin | {"owner_id": "foreign"}
    with pytest.raises(MeetError):
        prepare(fixture, max_seconds=41 if change == "budget" else 40)


def test_policy_revocation_during_descriptor_read_prevents_release(bound_voice):
    fixture, case = bound_voice, bound_voice.case
    original = case.fixture.storage.store.load_immutable_bytes

    def load(**kwargs):
        result = original(**kwargs)
        case.fixture.case.policy.revoke_policy(
            case.scope.principal, "project", case.fixture.case.permission.source.source_id, expected_revision=1
        )
        return result

    case.fixture.storage.store.load_immutable_bytes = load
    with pytest.raises(MeetError):
        prepare(fixture)


def test_profile_change_during_preparation_prevents_release(bound_voice):
    fixture, case = bound_voice, bound_voice.case
    original = fixture.voices.prepare

    def prepare_changed(*args, **kwargs):
        result = original(*args, **kwargs)
        save(case.scope, profile(revision=2, voice=MediaSelection(state="disabled")), expected=1)
        return result

    fixture.voices.prepare = prepare_changed
    with pytest.raises(MeetError):
        prepare(fixture)


@pytest.mark.parametrize("purpose", ["clone", "download", None])
def test_voice_adapter_does_not_broaden_policy_purposes(bound_voice, purpose):
    with pytest.raises(MeetError):
        prepare(bound_voice, purpose=purpose)


def test_preview_only_voice_is_not_a_meet_publication_grant(request):
    from tests.test_persona_voice_asset_service import admit

    fixture = request.getfixturevalue("voice_assets")
    policy = fixture.case.permission.model_copy(update={"revision": 2, "purposes": ("inspect", "store", "preview")})
    fixture.case.policy.install(fixture.case.principal, policy, expected_revision=1)
    asset = admit(fixture)
    voices = MeetPersonaVoices(fixture.service)
    reference = asset.voice.model_dump(mode="json")
    assert voices.prepare(fixture.case.principal, "project", reference, "preview")["speech_profile"] == speech_profile()
    with pytest.raises(MeetError):
        voices.prepare(fixture.case.principal, "project", reference, "publish")
