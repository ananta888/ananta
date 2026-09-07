"""Private voice discovery rechecks current receipts and never loads audio or models."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import update

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
from agent.repositories.persona_image_cursors import SqlPersonaImageCursors
from agent.repositories.persona_video_cursors import SqlPersonaVideoCursors
from agent.repositories.persona_voice_cursors import create_voice_cursors
from agent.services.persona_asset_query import PersonaAssetQuery
from agent.services.persona_profile_voices import PersonaProfileVoices
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_voice_asset_service import admit
from tests.test_persona_voice_asset_service import voice_assets as voice_assets
from tests.test_persona_voice_tasks import voice_task as voice_task


@pytest.fixture
def listing(request):
    fixture = request.getfixturevalue("voice_assets")
    asset = admit(fixture)
    cursors = create_voice_cursors(fixture.catalog.engine, clock=lambda: 1000)
    cursors.initialize()
    query = PersonaAssetQuery(
        policy=fixture.case.policy,
        catalog=fixture.catalog,
        references=PersonaProfileVoices(fixture.service),
        cursors=cursors,
        kind="voice",
    )
    return SimpleNamespace(fixture=fixture, asset=asset, cursors=cursors, query=query)


def page(listing):
    return listing.query.query(listing.fixture.case.principal, "project", cursor=None, limit=20)


def test_voice_discovery_is_reference_metadata_only(listing):
    listing.fixture.service.storage = Mock()
    assert page(listing) == {
        "items": [listing.asset.voice.model_dump(mode="json")],
        "next_cursor": None,
        "purpose": "preview",
    }
    listing.fixture.service.storage.read.assert_not_called()


@pytest.mark.parametrize("change", ["asset", "policy", "receipt"])
def test_revoked_voice_or_mutated_receipt_is_not_listed(listing, change):
    fixture = listing.fixture
    if change == "asset":
        fixture.service.revoke(fixture.case.principal, "project", listing.asset.voice.artifact_id, expected_revision=2)
    elif change == "policy":
        fixture.case.policy.revoke_policy(
            fixture.case.principal, "project", fixture.case.permission.source.source_id, expected_revision=1
        )
    else:
        with fixture.catalog.engine.begin() as connection:
            connection.execute(update(HubRunEvidenceIdentityDB).values(result_digest="f" * 64))
    assert page(listing)["items"] == []


def test_voice_paging_handles_are_scoped_and_never_image_or_video_handles(listing):
    scope = dict(tenant_id="tenant", project_id="project", subject_id=listing.fixture.case.principal.subject_id)
    token = listing.cursors.issue(**scope, position="voice-position")
    for other in (
        SqlPersonaImageCursors(listing.fixture.catalog.engine),
        SqlPersonaVideoCursors(listing.fixture.catalog.engine),
    ):
        other.initialize()
        with pytest.raises(ValueError, match="unavailable"):
            other.resolve(**scope, token=token)
        foreign = other.issue(**scope, position="other-position")
        with pytest.raises(ValueError, match="unavailable"):
            listing.cursors.resolve(**scope, token=foreign)
    for field in scope:
        with pytest.raises(ValueError, match="unavailable"):
            listing.cursors.resolve(**(scope | {field: "foreign"}), token=token)
    assert listing.cursors.resolve(**scope, token=token) == "voice-position"


def test_denied_voice_project_is_not_scanned(listing):
    listing.fixture.case.policy.access.require.side_effect = PermissionError("revoked membership")
    listing.query.catalog, listing.query.cursors = Mock(), Mock()
    with pytest.raises(PermissionError):
        page(listing)
    listing.query.catalog.scan_active_ids.assert_not_called()
    listing.query.cursors.resolve.assert_not_called()


@pytest.mark.parametrize("kind", ["image", "video"])
def test_wrong_reference_port_cannot_return_another_media_kind(listing, kind):
    listing.query.references = Mock(reference=Mock(return_value=listing.asset.voice.model_copy(update={"kind": kind})))
    assert page(listing)["items"] == []
