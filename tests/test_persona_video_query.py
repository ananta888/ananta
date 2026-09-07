"""Video discovery uses actual admitted private assets and registered test receipts."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import update

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
from agent.repositories.persona_image_cursors import SqlPersonaImageCursors
from agent.repositories.persona_video_cursors import SqlPersonaVideoCursors
from agent.services.persona_asset_query import PersonaAssetQuery
from agent.services.persona_profile_videos import PersonaProfileVideos
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_media_routes import HEADERS
from tests.test_persona_media_routes import client as client
from tests.test_persona_video_asset_service import admit
from tests.test_persona_video_asset_service import video_assets as video_assets
from tests.test_persona_video_tasks import video_task as video_task


@pytest.fixture
def listing(request):
    f = request.getfixturevalue("video_assets")
    asset = admit(f)
    cursors = SqlPersonaVideoCursors(f.catalog.engine, clock=lambda: 1000)
    cursors.initialize()
    query = PersonaAssetQuery(
        policy=f.case.policy,
        catalog=f.catalog,
        references=PersonaProfileVideos(f.service),
        cursors=cursors,
        kind="video",
    )
    return SimpleNamespace(f=f, asset=asset, cursors=cursors, query=query)


def page(listing, **kwargs):
    return listing.query.query(listing.f.case.principal, "project", **({"cursor": None, "limit": 20} | kwargs))


def test_only_the_closed_reference_is_listed_and_no_media_bytes_are_loaded(listing):
    listing.f.service.storage = Mock()
    assert page(listing) == {
        "items": [listing.asset.video.model_dump(mode="json")],
        "next_cursor": None,
        "purpose": "preview",
    }
    listing.f.service.storage.read.assert_not_called()


@pytest.mark.parametrize("change", ["asset", "policy", "receipt"])
def test_revoked_or_changed_registry_backed_clip_is_not_discoverable(listing, change):
    f = listing.f
    if change == "asset":
        f.service.revoke(f.case.principal, "project", listing.asset.video.artifact_id, expected_revision=2)
    elif change == "policy":
        f.case.policy.revoke_policy(
            f.case.principal, "project", f.case.permission.source.source_id, expected_revision=1
        )
    else:
        with f.catalog.engine.begin() as connection:
            connection.execute(update(HubRunEvidenceIdentityDB).values(result_digest="f" * 64))
    assert page(listing)["items"] == []


def test_project_denial_precedes_scanning_or_cursor_resolution(listing):
    listing.f.case.policy.access.require.side_effect = PermissionError("synthetic denied membership")
    listing.query.catalog = Mock()
    listing.query.cursors = Mock()
    with pytest.raises(PermissionError):
        page(listing)
    listing.query.catalog.scan_active_ids.assert_not_called()
    listing.query.cursors.resolve.assert_not_called()


@pytest.mark.parametrize("field,value", [("kind", "image"), ("project_id", "foreign"), ("tenant_id", "other")])
def test_wrong_reference_adapter_cannot_cross_media_or_scope_boundary(listing, field, value):
    listing.query.references = Mock()
    listing.query.references.reference.return_value = listing.asset.video.model_copy(update={field: value})
    assert page(listing)["items"] == []


def test_image_and_video_paging_handles_are_not_interchangeable(listing):
    images = SqlPersonaImageCursors(listing.f.catalog.engine, clock=lambda: 1000)
    images.initialize()
    scope = dict(tenant_id="tenant", project_id="project", subject_id=listing.f.case.principal.subject_id)
    image = images.issue(**scope, position="private-image-position")
    video = listing.cursors.issue(**scope, position="private-video-position")
    for store, token in ((images, video), (listing.cursors, image)):
        with pytest.raises(ValueError, match="unavailable"):
            store.resolve(**scope, token=token)
    assert listing.cursors.resolve(**scope, token=video) == "private-video-position"
    for field in scope:
        with pytest.raises(ValueError, match="unavailable"):
            listing.cursors.resolve(**(scope | {field: "foreign"}), token=video)


def test_scanning_is_bounded_and_continuation_contains_no_denied_identifier(listing):
    listing.query.catalog = Mock()
    listing.query.catalog.scan_active_ids.return_value = tuple(f"denied-{i:03}" for i in range(65))
    listing.query.references = Mock()
    listing.query.references.reference.side_effect = PermissionError("denied")
    result = page(listing)
    assert result["items"] == [] and "denied" not in result["next_cursor"]
    assert listing.query.references.reference.call_count == 64


def test_video_query_route_requires_user_auth_and_rejects_publication_fields(listing, request):
    http, app = request.getfixturevalue("client")
    app.extensions["persona_video_query"] = listing.query
    path = "/api/persona-media/v1/projects/project/videos/query"
    body = {"cursor": None, "limit": 20}
    assert http.post(path, json=body).status_code == 401
    response = http.post(path, json=body, headers=HEADERS)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert response.json["items"] == [listing.asset.video.model_dump(mode="json")]
    assert http.post(path, json=body | {"purpose": "publish"}, headers=HEADERS).status_code == 409
    app.extensions.pop("persona_video_query")
    assert http.post(path, json=body, headers=HEADERS).status_code == 409
