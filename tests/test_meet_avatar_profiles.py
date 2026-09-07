"""Independent avatar outputs with real SQL profile/asset lifecycle fixtures."""

from dataclasses import FrozenInstanceError
from unittest.mock import Mock

import pytest
from sqlalchemy import update

from agent.db_models import OrganizationInstanceDB, OrganizationMembershipDB, ProjectMembershipDB
from agent.models.persona_media import MediaSelection
from agent.services.meet_avatar_profiles import MeetAvatarProfiles
from agent.services.meet_image_profile_binding import MeetImageProfileBinding
from tests.test_meet_persona_profiles import bound as bound
from tests.test_meet_persona_profiles import selection
from tests.test_persona_assets import setup as setup
from tests.test_persona_media import profile
from tests.test_persona_profile_service import system as system

pytestmark = pytest.mark.timeout(45)


@pytest.fixture
def fixture(request):
    return request.getfixturevalue("bound")


def revised(fixture, **changes):
    value = profile(revision=2, image=MediaSelection(state="asset", asset=fixture.asset.image), **changes)
    fixture.repository.append(value, expected_revision=1, actor="actor")
    return selection(fixture)


def test_disabled_voice_allows_image_avatar_but_still_denies_legacy_mp4(fixture):
    pin = revised(fixture, voice=MediaSelection(state="disabled"))
    adapter = MeetAvatarProfiles(fixture.service, fixture.images)
    assignment, binding = adapter.prepare(fixture.principal, "project", pin, "publish")
    assert binding == pin and assignment["reference"] == fixture.asset.image.model_dump(mode="json")
    assert set(assignment) == {"reference", "png"}
    adapter.require_current(fixture.principal, "project", binding, assignment["reference"], "publish")
    with pytest.raises(ValueError, match="profile_denied_or_changed"):
        fixture.adapter.prepare(fixture.principal, "project", pin, "publish")


@pytest.mark.parametrize("kind", ["image", "video"])
def test_disabled_visual_output_denies_before_loading_image_and_never_selects_neutral(fixture, kind):
    value = profile(revision=2, **{kind: MediaSelection(state="disabled")})
    fixture.repository.append(value, expected_revision=1, actor="actor")
    fixture.images.prepare = Mock(wraps=fixture.images.prepare)
    with pytest.raises(ValueError, match="profile_denied_or_changed"):
        MeetAvatarProfiles(fixture.service, fixture.images).prepare(
            fixture.principal, "project", selection(fixture), "publish"
        )
    fixture.images.prepare.assert_not_called()


def test_avatar_rechecks_current_publish_policy_without_treating_preview_as_authority(fixture):
    adapter = MeetAvatarProfiles(fixture.service, fixture.images)
    assignment, pin = adapter.prepare(fixture.principal, "project", selection(fixture), "preview")

    def policy(_, __, purpose):
        if purpose == "publish":
            raise PermissionError("synthetic-publication-revoked")

    fixture.assets.policy.require_asset.side_effect = policy
    with pytest.raises(ValueError, match="image_denied_or_unavailable"):
        adapter.require_current(fixture.principal, "project", pin, assignment["reference"], "publish")


@pytest.mark.parametrize(
    "model,changes",
    [
        (OrganizationInstanceDB, {"lifecycle": "archived"}),
        (OrganizationMembershipDB, {"expires_at": 1}),
        (ProjectMembershipDB, {"state": "revoked"}),
    ],
)
def test_current_avatar_binding_loses_revoked_sql_authority(fixture, model, changes):
    adapter = MeetAvatarProfiles(fixture.service, fixture.images)
    assignment, pin = adapter.prepare(fixture.principal, "project", selection(fixture), "publish")
    with fixture.engine.begin() as connection:
        connection.execute(update(model).values(**changes))
    with pytest.raises(ValueError, match="profile_denied_or_changed"):
        adapter.require_current(fixture.principal, "project", pin, assignment["reference"], "publish")


def test_avatar_old_profile_pin_cannot_follow_a_new_image_revision(fixture):
    adapter = MeetAvatarProfiles(fixture.service, fixture.images)
    assignment, pin = adapter.prepare(fixture.principal, "project", selection(fixture), "publish")
    revised(fixture, voice=MediaSelection(state="disabled"))
    with pytest.raises(ValueError, match="profile_denied_or_changed"):
        adapter.require_current(fixture.principal, "project", pin, assignment["reference"], "publish")
    assert adapter.prepare(fixture.principal, "project", selection(fixture), "publish")[1] != pin


def test_avatar_asset_tombstone_invalidates_an_otherwise_current_profile(fixture):
    adapter = MeetAvatarProfiles(fixture.service, fixture.images)
    assignment, pin = adapter.prepare(fixture.principal, "project", selection(fixture), "publish")
    fixture.assets.revoke(fixture.principal, "project", fixture.asset.image.artifact_id, expected_revision=2)
    with pytest.raises(ValueError, match="denied_or"):
        adapter.require_current(fixture.principal, "project", pin, assignment["reference"], "publish")


def test_profile_mutation_during_final_asset_policy_check_cannot_release_old_binding(fixture):
    adapter = MeetAvatarProfiles(fixture.service, fixture.images)
    assignment, pin = adapter.prepare(fixture.principal, "project", selection(fixture), "publish")
    original = fixture.images.require_current

    def mutate(*args):
        original(*args)
        revised(fixture, voice=MediaSelection(state="disabled"))

    fixture.images.require_current = mutate
    with pytest.raises(ValueError, match="profile_denied_or_changed"):
        adapter.require_current(fixture.principal, "project", pin, assignment["reference"], "publish")


@pytest.mark.parametrize("outputs", [(), ("voice",), ("image", "image"), ("image", "tool"), ["image"]])
def test_shared_binding_rejects_invalid_or_mutable_output_declarations(outputs):
    with pytest.raises(ValueError, match="outputs_invalid"):
        MeetImageProfileBinding(Mock(), Mock(), outputs)


def test_shared_binding_output_declaration_cannot_change_after_composition():
    binding = MeetImageProfileBinding(Mock(), Mock(), ("image", "video"))
    with pytest.raises(FrozenInstanceError):
        binding.required_outputs = ("image",)
