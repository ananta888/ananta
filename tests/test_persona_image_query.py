"""Bounded discovery with real scoped catalog/cursor SQL and synthetic policies."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.models.persona_assets import PersonaImageAsset
from agent.repositories.persona_image_cursors import SqlPersonaImageCursors
from agent.services.persona_image_query import PersonaImageQuery
from agent.services.persona_profile_images import PersonaProfileImages
from tests.test_persona_assets import setup as setup
from tests.test_persona_media_routes import HEADERS
from tests.test_persona_media_routes import client as client

pytestmark = pytest.mark.timeout(30)


@pytest.fixture
def listing(request):
    catalog, storage, template, inspected = request.getfixturevalue("setup")
    for identity in ("a-denied", "b-allowed", "c-allowed", "d-revoked", "e-pending"):
        payload = template.model_dump(mode="json")
        payload["image"]["artifact_id"] = identity
        payload["preview"]["artifact_id"] = identity + "-preview"
        asset = PersonaImageAsset.model_validate(payload)
        catalog.reserve(asset, actor="actor")
        if identity == "e-pending":
            continue
        paths = storage.write(asset, inspected, checkpoint=Mock())
        catalog.transition(
            "tenant", "project", identity, actor="actor", expected_revision=1, state="active", stored_paths=paths
        )
        if identity == "d-revoked":
            catalog.transition("tenant", "project", identity, actor="actor", expected_revision=2, state="revoked")
    policy = Mock()

    def require_asset(_principal, asset, purpose):
        assert purpose == "preview"
        if asset.image.artifact_id == "a-denied":
            raise PermissionError("synthetic-denial")

    policy.require_asset.side_effect = require_asset
    images = PersonaProfileImages(SimpleNamespace(policy=policy, catalog=catalog))
    now = [1000.0]
    cursors = SqlPersonaImageCursors(catalog.engine, clock=lambda: now[0])
    cursors.initialize()
    return SimpleNamespace(
        query=PersonaImageQuery(policy=policy, catalog=catalog, images=images, cursors=cursors),
        principal=SimpleNamespace(tenant_id="tenant", subject_id="actor"),
        policy=policy,
        catalog=catalog,
        cursors=cursors,
        now=now,
    )


def page(listing, cursor=None, limit=1):
    return listing.query.query(listing.principal, "project", cursor=cursor, limit=limit)


def test_pages_only_disclose_current_previewable_assets_with_opaque_cursor(listing):
    first = page(listing)
    assert [item["artifact_id"] for item in first["items"]] == ["b-allowed"]
    assert first["purpose"] == "preview"
    assert first["next_cursor"] and "allowed" not in first["next_cursor"]
    second = page(listing, first["next_cursor"])
    assert [item["artifact_id"] for item in second["items"]] == ["c-allowed"]
    assert second["next_cursor"] is None


def test_revocation_after_first_page_is_rechecked_and_foreign_scope_stays_closed(listing):
    cursor = page(listing)["next_cursor"]
    listing.policy.require_asset.side_effect = PermissionError("revoked")
    assert page(listing, cursor)["items"] == []
    for change in ({"subject_id": "other"}, {"tenant_id": "other"}, {"project_id": "other"}):
        with pytest.raises(ValueError, match="unavailable"):
            listing.cursors.resolve(
                **({"tenant_id": "tenant", "project_id": "project", "subject_id": "actor", "token": cursor} | change)
            )


def test_expired_and_unknown_handles_are_not_reusable(listing):
    cursor = page(listing)["next_cursor"]
    listing.now[0] += 301
    with pytest.raises(ValueError, match="unavailable"):
        page(listing, cursor)
    with pytest.raises(ValueError, match="unavailable"):
        page(listing, "x" * 43)


@pytest.mark.parametrize("limit", [True, "1", 0, 21])
def test_page_size_is_strict_and_bounded(listing, limit):
    with pytest.raises(ValueError, match="limit_invalid"):
        page(listing, limit=limit)


def test_project_revocation_prevents_listing_before_cursor_lookup(listing):
    listing.policy.require_list.side_effect = PermissionError("project-revoked")
    with pytest.raises(PermissionError):
        page(listing, "invalid-handle")


def test_cursor_quota_is_scoped_bounded_and_recovers_after_expiry(listing):
    scope = dict(tenant_id="tenant", project_id="project", subject_id="actor")
    for _ in range(128):
        listing.cursors.issue(**scope, position="b-allowed")
    with pytest.raises(ValueError, match="budget_exceeded"):
        listing.cursors.issue(**scope, position="b-allowed")
    listing.now[0] += 301
    token = listing.cursors.issue(**scope, position="c-allowed")
    assert listing.cursors.resolve(**scope, token=token) == "c-allowed"


def test_parallel_hub_cursor_issuers_cannot_overrun_the_reader_quota(listing):
    scope = dict(tenant_id="tenant", project_id="project", subject_id="actor")
    for _ in range(127):
        listing.cursors.issue(**scope, position="b-allowed")

    def issue(_):
        try:
            return listing.cursors.issue(**scope, position="c-allowed")
        except ValueError as error:
            assert str(error) == "persona_image_cursor_budget_exceeded"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(value is not None for value in pool.map(issue, range(2))) == 1


def test_scan_budget_produces_an_opaque_continuation_without_exposing_denied_ids(listing):
    listing.query.catalog = Mock()
    listing.query.catalog.scan_active_ids.return_value = tuple(f"denied-{i:03}" for i in range(65))
    listing.query.images = Mock()
    listing.query.images.reference.side_effect = PermissionError("denied")
    result = page(listing, limit=20)
    assert result["items"] == [] and result["next_cursor"]
    assert listing.query.images.reference.call_count == 64
    assert (
        listing.cursors.resolve(
            tenant_id="tenant", project_id="project", subject_id="actor", token=result["next_cursor"]
        )
        == "denied-063"
    )


def test_headless_query_api_has_closed_body_and_user_auth(listing, request):
    http, app = request.getfixturevalue("client")
    app.extensions["persona_image_query"] = listing.query
    path = "/api/persona-media/v1/projects/project/images/query"
    body = {"cursor": None, "limit": 1}
    assert http.post(path, json=body).status_code == 401
    response = http.post(path, json=body, headers=HEADERS)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert response.json["items"][0]["artifact_id"] == "b-allowed"
    assert http.post(path, json=body | {"purpose": "publish"}, headers=HEADERS).status_code == 409
