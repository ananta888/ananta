"""Synthetic metadata only: no files, grants, worker identity or provider setup."""

from itertools import permutations

import pytest
from pydantic import ValidationError

from agent.models.persona_media import (
    MediaAssetRef,
    MediaSelection,
    PersonaMediaProfile,
    PersonaMembership,
)
from agent.services.persona_media_resolution import resolve_media


def membership(**changes):
    return PersonaMembership(
        **(
            dict(
                tenant_id="tenant",
                project_id="project",
                organization_id="org",
                team_id="team",
                agent_id="agent",
                assignment_id="assignment",
                membership_revision=1,
            )
            | changes
        )
    )


def profile(kind="organization", **changes):
    owners = {"organization": "org", "team": "team", "agent": "agent"}
    return PersonaMediaProfile(
        **(
            dict(
                tenant_id="tenant",
                project_id="project",
                owner_kind=kind,
                owner_id=owners[kind],
                persona_id="presentation",
                revision=1,
            )
            | changes
        )
    )


def asset(**changes):
    return MediaAssetRef(
        **(
            dict(
                tenant_id="tenant",
                project_id="project",
                artifact_id="artifact",
                revision=1,
                sha256="a" * 64,
                kind="image",
                classification="test_only",
            )
            | changes
        )
    )


def test_missing_inherit_disabled_and_asset_are_distinct_and_immutable():
    org = profile(image=MediaSelection(state="asset", asset=asset()))
    team = profile("team", image=MediaSelection(state="inherit"))
    agent = profile("agent")
    expected = resolve_media(membership(), (org, team, agent))
    for layers in permutations((org, team, agent)):
        assert resolve_media(membership(), layers) == expected
    image = expected.media[0]
    assert image.asset == asset() and image.state == "asset"
    assert [origin.selection_state for origin in image.origins] == ["missing", "inherit", "asset"]
    assert [origin.owner_kind for origin in image.origins] == ["agent", "team", "organization"]
    disabled = profile("team", image=MediaSelection(state="disabled"))
    image = resolve_media(membership(), (org, disabled, agent)).media[0]
    assert image.state == "disabled" and image.asset is None and len(image.origins) == 2
    with pytest.raises(ValidationError):
        org.revision = 2
    with pytest.raises(ValidationError):
        org.image.state = "disabled"


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": "foreign"},
        {"project_id": "foreign"},
        {"organization_id": "foreign"},
        {"team_id": "foreign"},
        {"agent_id": "foreign"},
    ],
)
def test_membership_boundary_rejects_foreign_layer(changes):
    with pytest.raises(ValueError, match="membership_mismatch"):
        resolve_media(membership(**changes), tuple(profile(kind) for kind in ("organization", "team", "agent")))


def test_duplicate_layer_and_implicit_identity_fields_are_rejected():
    with pytest.raises(ValueError, match="membership_mismatch"):
        resolve_media(membership(), (profile(), profile(revision=2)))
    for key in ("agent_id", "role_slot_id", "permissions", "provider_secret", "image_bytes"):
        with pytest.raises(ValidationError):
            profile(**{key: "forbidden"})


@pytest.mark.parametrize("changes", [{"tenant_id": "foreign"}, {"project_id": "foreign"}, {"kind": "video"}])
def test_asset_cannot_cross_scope_or_media_kind(changes):
    with pytest.raises(ValidationError, match="scope_or_kind_mismatch"):
        profile(image=MediaSelection(state="asset", asset=asset(**changes)))


@pytest.mark.parametrize(
    "selection", [{"state": "asset"}, {"state": "inherit", "asset": asset()}, {"state": "disabled", "asset": asset()}]
)
def test_selection_cannot_hide_asset_under_disabled_or_inherited_state(selection):
    with pytest.raises(ValidationError):
        MediaSelection(**selection)


@pytest.mark.parametrize(
    "changes",
    [
        {"revision": True},
        {"revision": 0},
        {"revision": "1"},
        {"persona_id": "../private"},
        {"requested_usage": ("publish", "publish")},
    ],
)
def test_closed_bounded_profile_fields(changes):
    with pytest.raises(ValidationError):
        profile(**changes)


def test_no_defaults_invent_assets_or_promote_test_classification():
    assert all(item.state == "missing" and item.asset is None for item in resolve_media(membership(), ()).media)
    selected = profile(image=MediaSelection(state="asset", asset=asset()), requested_usage=("publish",))
    result = resolve_media(membership(), (selected,))
    assert result.media[0].asset.classification == "test_only"
    assert "permissions" not in result.model_dump()
