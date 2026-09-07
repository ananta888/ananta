"""Real profile topology and private video receipts, with structural decoder double."""

from types import SimpleNamespace

import pytest
from sqlalchemy import update

from agent.db_models import OrganizationInstanceDB
from agent.models.persona_media import MediaSelection, PersonaProfileSelection
from agent.services.persona_profile_videos import PersonaProfileVideos
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_media import profile
from tests.test_persona_media_routes import HEADERS
from tests.test_persona_media_routes import client as client
from tests.test_persona_profile_service import save
from tests.test_persona_profile_service import system as system
from tests.test_persona_video_asset_service import admit
from tests.test_persona_video_asset_service import video_assets as video_assets
from tests.test_persona_video_tasks import video_task as video_task


@pytest.fixture
def selected_video(request):
    scope = request.getfixturevalue("system")
    fixture = request.getfixturevalue("video_assets")
    fixture.case.policy.access = scope.service.access
    asset = admit(fixture)
    videos = PersonaProfileVideos(fixture.service)
    scope.service.videos = videos
    scope.service.images = None  # Video-only composition does not fabricate image availability.
    return SimpleNamespace(scope=scope, fixture=fixture, asset=asset, videos=videos)


def selected(case, kind="organization", **changes):
    return profile(kind, video=MediaSelection(state="asset", asset=case.asset.video), **changes)


@pytest.mark.parametrize("kind", ["organization", "team", "agent"])
def test_video_profile_roundtrip_uses_actual_owner_scope_and_registered_receipt(selected_video, kind):
    case = selected_video
    value = selected(case, kind)
    save(case.scope, value)
    current = case.scope.service.current(case.scope.principal, "project", "org", kind, value.owner_id)
    assert current["profile"] == value.model_dump(mode="json") and current["media_available"]
    assert case.videos.reference(case.scope.principal, "project", case.asset.video.artifact_id) == case.asset.video
    case.videos.require_reference(case.scope.principal, case.asset.video)


def test_video_inheritance_disabled_override_and_preview_are_separate_from_publication(selected_video):
    case = selected_video
    scope = case.scope
    save(scope, selected(case))
    save(scope, profile("team", video=MediaSelection(state="inherit")))
    save(scope, profile("agent", video=MediaSelection(state="inherit")))
    result = scope.service.effective(scope.principal, "project", "org", "agent", "agent")
    video = next(item for item in result["media"] if item["kind"] == "video")
    assert video["asset"] == case.asset.video.model_dump(mode="json")
    assert [item["owner_kind"] for item in video["origins"]] == ["agent", "team", "organization"]
    assert video["preview_allowed"] and not video["publication_checked"] and not result["runtime_bound"]
    save(scope, profile("team", revision=2, video=MediaSelection(state="disabled")), expected=1)
    result = scope.service.effective(scope.principal, "project", "org", "agent", "agent")
    video = next(item for item in result["media"] if item["kind"] == "video")
    assert video["state"] == "disabled" and video["asset"] is None and not video["preview_allowed"]


def test_revoked_video_hides_reference_but_leaves_profile_revision_editable(selected_video):
    case = selected_video
    scope = case.scope
    save(scope, selected(case))
    case.fixture.service.revoke(
        case.fixture.case.principal, "project", case.asset.video.artifact_id, expected_revision=2
    )
    result = scope.service.current(scope.principal, "project", "org", "organization", "org")
    assert result["profile"] is None and result["revision"] == 1 and not result["media_available"]
    result = scope.service.effective(scope.principal, "project", "org", "organization", "org")
    video = next(item for item in result["media"] if item["kind"] == "video")
    assert video["state"] == "asset" and video["asset"] is None and not video["available"]
    save(scope, profile(revision=2, video=MediaSelection(state="disabled")), expected=1)
    assert scope.service.current(scope.principal, "project", "org", "organization", "org")["revision"] == 2


@pytest.mark.parametrize(
    "changes", [{"tenant_id": "foreign"}, {"kind": "image"}, {"revision": 2}, {"sha256": "f" * 64}]
)
def test_video_reference_never_accepts_replaced_scope_kind_revision_or_hash(selected_video, changes):
    case = selected_video
    with pytest.raises(PermissionError):
        case.videos.require_reference(case.scope.principal, case.asset.video.model_copy(update=changes))


def test_catalog_revocation_during_reference_lookup_is_detected(selected_video):
    case = selected_video
    original = case.fixture.case.policy.require_asset

    def require(principal, asset, purpose):
        original(principal, asset, purpose)
        case.fixture.service.revoke(principal, "project", asset.video.artifact_id, expected_revision=2)

    case.fixture.case.policy.require_asset = require
    with pytest.raises((PermissionError, ValueError)):
        case.videos.reference(case.scope.principal, "project", case.asset.video.artifact_id)


def test_legacy_image_turn_cannot_ignore_explicit_clip_selection(selected_video):
    case = selected_video
    scope = case.scope
    save(scope, selected(case))
    with scope.engine.begin() as connection:
        connection.execute(update(OrganizationInstanceDB).values(lifecycle="active"))
    result = scope.service.effective(scope.principal, "project", "org", "organization", "org")
    pin = PersonaProfileSelection.model_validate(result["selection"])
    with pytest.raises(PermissionError, match="output_unsupported"):
        scope.service.for_execution(scope.principal, "project", pin, required_outputs=("image", "voice", "video"))


def test_disabled_video_port_does_not_silently_accept_profile_metadata(selected_video):
    case = selected_video
    case.scope.service.videos = None
    with pytest.raises(ValueError, match="not_supported"):
        save(case.scope, selected(case))


@pytest.mark.parametrize("disabled", [None, "voice", "video"])
def test_explicit_video_execution_rechecks_profile_pin_and_declared_outputs(selected_video, disabled):
    case = selected_video
    scope = case.scope
    value = selected(case)
    if disabled:
        value = value.model_copy(update={disabled: MediaSelection(state="disabled")})
    save(scope, value)
    with scope.engine.begin() as connection:
        connection.execute(update(OrganizationInstanceDB).values(lifecycle="active"))
    result = scope.service.effective(scope.principal, "project", "org", "organization", "org")
    pin = PersonaProfileSelection.model_validate(result["selection"])
    if disabled:
        with pytest.raises(PermissionError, match="output_disabled"):
            scope.service.for_video_execution(scope.principal, "project", pin)
    else:
        assert scope.service.for_video_execution(scope.principal, "project", pin) == case.asset.video.model_dump(
            mode="json"
        )
        with pytest.raises(PermissionError, match="profile_changed"):
            scope.service.for_video_execution(
                scope.principal, "project", pin.model_copy(update={"selection_digest": "f" * 64})
            )


def test_headless_reference_and_profile_api_return_no_video_bytes_or_storage_paths(selected_video, request):
    case = selected_video
    http, app = request.getfixturevalue("client")
    app.extensions["persona_profile_videos"] = case.videos
    app.extensions["persona_profiles"] = case.scope.service
    path = "/api/persona-media/v1/projects/project/videos/" + case.asset.video.artifact_id + "/reference"
    assert http.get(path).status_code == 401
    response = http.get(path, headers=HEADERS)
    assert response.json == {"reference": case.asset.video.model_dump(mode="json")}
    assert response.headers["Cache-Control"] == "no-store"
    target = "/api/persona-media/v1/projects/project/organizations/org/profiles/organization/org"
    value = selected(case).model_dump(mode="json")
    assert http.put(target, headers=HEADERS, json={"profile": value, "expected_revision": 0}).status_code == 200
    response = http.get(target, headers=HEADERS)
    assert response.json["profile"] == value and response.json["media_available"]
    assert b"storage_path" not in response.data and b"base64" not in response.data
