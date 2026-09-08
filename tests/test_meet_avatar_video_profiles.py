"""Actual Hub topology, private assets and Registry test receipts for silent video."""

from unittest.mock import Mock

import pytest
from sqlalchemy import update

from agent.db_models import OrganizationInstanceDB, OrganizationMembershipDB, ProjectMembershipDB
from agent.models.persona_media import MediaSelection
from agent.services.meet_avatar_video_profiles import MeetAvatarVideoProfiles
from agent.services.meet_contract import MeetError
from tests.test_meet_video_profiles import clip_profile as clip_profile
from tests.test_meet_video_profiles import selection
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_profile_service import save
from tests.test_persona_profile_service import system as system
from tests.test_persona_profile_videos import selected
from tests.test_persona_profile_videos import selected_video as selected_video
from tests.test_persona_video_asset_service import video_assets as video_assets
from tests.test_persona_video_tasks import video_task as video_task

pytestmark = pytest.mark.timeout(45)


def test_silent_clip_can_publish_with_voice_disabled_without_changing_legacy_audio_video_policy(request):
    c = request.getfixturevalue("clip_profile")
    save(c.scope, selected(c, revision=2, voice=MediaSelection(state="disabled")), expected=1)
    pin = selection(c)
    adapter = MeetAvatarVideoProfiles(c.scope.service, c.port.videos)
    c.port.videos.prepare = Mock(wraps=c.port.videos.prepare)
    reference, binding = adapter.select(c.scope.principal, "project", pin, "publish")
    c.port.videos.prepare.assert_not_called()
    assert binding == pin and reference == c.asset.video.model_dump(mode="json")
    value, bound = adapter.prepare(c.scope.principal, "project", pin, "publish", repeat_mode="hold_last")
    assert value["reference"] == reference and bound == pin and value["repeat_mode"] == "hold_last"
    with pytest.raises(MeetError):
        c.port.prepare(c.scope.principal, "project", pin, "publish", repeat_mode="hold_last")


@pytest.mark.parametrize("change", ["profile", "asset", "organization", "membership", "project", "during_policy"])
def test_current_scope_and_clip_policy_are_rechecked_before_hydration(request, change):
    c = request.getfixturevalue("clip_profile")
    adapter = MeetAvatarVideoProfiles(c.scope.service, c.port.videos)
    pin = selection(c)
    reference, _ = adapter.select(c.scope.principal, "project", pin, "publish")
    c.port.videos.prepare = Mock(wraps=c.port.videos.prepare)

    def revoke():
        save(c.scope, selected(c, revision=2), expected=1)

    if change == "profile":
        revoke()
    elif change == "asset":
        c.fixture.service.revoke(c.scope.principal, "project", c.asset.video.artifact_id, expected_revision=2)
    elif change == "organization":
        with c.scope.engine.begin() as connection:
            connection.execute(update(OrganizationInstanceDB).values(lifecycle="archived"))
    elif change in {"membership", "project"}:
        model, fields = (
            (OrganizationMembershipDB, {"expires_at": 1})
            if change == "membership"
            else (ProjectMembershipDB, {"state": "revoked"})
        )
        with c.scope.engine.begin() as connection:
            connection.execute(update(model).values(**fields))
    else:
        original = c.port.videos.require_current

        def changed(*args):
            original(*args)
            revoke()

        c.port.videos.require_current = changed
    with pytest.raises(MeetError):
        adapter.require_current(c.scope.principal, "project", pin, reference, "publish")
    c.port.videos.prepare.assert_not_called()
