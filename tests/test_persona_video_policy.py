"""Separate video permissions with actual registered test-only source proofs."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from unittest.mock import Mock

import pytest
from sqlalchemy import select, update

from agent.models.persona_asset_policy import PersonaImagePolicy, PersonaSourcePin, PersonaVideoPolicy
from agent.repositories.persona_video_policies import create_video_policy_repository, versions
from agent.services.persona_video_policy_service import create_video_policy_service
from agent.services.project_access_authority import ProjectCapability
from tests.test_persona_asset_policy import configured as configured


@pytest.fixture
def video_policy(request):
    images, principal, image_policy, engine = request.getfixturevalue("configured")
    identity = images.sources.register_source(
        tenant_id="tenant",
        project_id="project",
        origin_type="persona_video",
        origin_digest="b" * 64,
        content_digest="b" * 64,
        policy_digest="a" * 64,
        evidence_scope="test",
        synthetic=True,
    )
    policy = PersonaVideoPolicy.model_validate(
        image_policy.model_dump()
        | {
            "media_kind": "video",
            "policy_binding": "video-policy",
            "source": PersonaSourcePin(source_id=identity.source_id, binding_digest=identity.binding_digest),
        }
    )
    repository = create_video_policy_repository(engine)
    repository.initialize()
    service = create_video_policy_service(
        access=images.access,
        policies=repository,
        sources=images.sources,
        inspection_receipts=Mock(),
        clock=lambda: 1000,
    )
    return service, principal, policy, images, image_policy, engine


def admit(service, principal, policy):
    return service.admit(
        principal,
        "project",
        "b" * 64,
        origin_binding=policy.source.source_id,
        license_binding=policy.license.source_id,
        consent_binding=policy.consent.source_id,
    )


def test_video_requires_its_own_policy_and_never_reads_image_rows(video_policy):
    service, principal, policy, images, image_policy, _ = video_policy
    images.install(principal, image_policy, expected_revision=0)
    with pytest.raises(ValueError, match="unavailable"):
        admit(service, principal, image_policy)
    for target, value in ((service, image_policy), (images, policy)):
        with pytest.raises(PermissionError, match="media_kind_mismatch"):
            target.install(principal, value, expected_revision=0)
        with pytest.raises(ValueError, match="media_kind_mismatch"):
            target.policies.install(value, expected_revision=0, actor="owner")
    service.install(principal, policy, expected_revision=0)
    assert service.access.require.call_args.kwargs["capability"] == ProjectCapability.MANAGE
    current = admit(service, principal, policy)
    service.require_current(principal, current, "preview")
    with pytest.raises(PermissionError, match="use_denied"):
        service.require_current(principal, current, "publish")


def test_video_kind_is_explicit_and_image_wire_shape_is_unchanged(video_policy):
    _, _, policy, _, image_policy, _ = video_policy
    assert set(policy.model_dump()) == set(image_policy.model_dump()) | {"media_kind"}
    assert "generated_error" not in image_policy.model_dump()
    assert PersonaImagePolicy.model_validate_json(image_policy.model_dump_json()) == image_policy
    with pytest.raises(ValueError):
        PersonaVideoPolicy.model_validate(image_policy.model_dump())
    with pytest.raises(ValueError):
        PersonaImagePolicy.model_validate(policy.model_dump())


def test_video_policy_cannot_relabel_an_image_origin_as_a_clip(video_policy):
    service, principal, policy, _, image_policy, _ = video_policy
    mismatch = PersonaVideoPolicy.model_validate(policy.model_dump() | {"source": image_policy.source})
    with pytest.raises(PermissionError, match="source_media_kind_mismatch"):
        service.install(principal, mismatch, expected_revision=0)
    with pytest.raises(ValueError, match="unavailable"):
        service.policies.for_source("tenant", "project", image_policy.source.source_id)


def test_registered_test_proofs_never_promote_to_synthetic_or_production(video_policy):
    service, principal, policy, _, _, _ = video_policy
    for classification in ("synthetic", "production"):
        promoted = PersonaVideoPolicy.model_validate(policy.model_dump() | {"classification": classification})
        with pytest.raises(PermissionError, match="cannot_be_promoted"):
            service.install(principal, promoted, expected_revision=0)


def test_revocation_and_regrant_consume_revision_and_invalidate_old_admission(video_policy):
    service, principal, policy, _, _, engine = video_policy
    service.install(principal, policy, expected_revision=0)
    previous = admit(service, principal, policy)
    assert service.revoke_policy(principal, "project", policy.source.source_id, expected_revision=1) == 2
    with pytest.raises(ValueError, match="unavailable"):
        service.require_current(principal, previous, "preview")
    fresh = PersonaVideoPolicy.model_validate(policy.model_dump() | {"revision": 3})
    service.install(principal, fresh, expected_revision=2)
    with pytest.raises(PermissionError, match="revision_changed"):
        service.require_current(principal, previous, "preview")
    assert admit(service, principal, fresh).policy_revision == 3
    with engine.connect() as connection:
        first = connection.execute(select(versions).where(versions.c.revision == 1)).mappings().one()
        assert first["state"] == "revoked" and first["created_by"] == first["revoked_by"] == "owner"


def test_changed_policy_bytes_or_current_membership_deny_use(video_policy):
    service, principal, policy, _, _, engine = video_policy
    service.install(principal, policy, expected_revision=0)
    previous = admit(service, principal, policy)
    service.access.require.side_effect = PermissionError("membership revoked")
    with pytest.raises(PermissionError):
        service.require_current(principal, previous, "store")
    service.access.require.side_effect = None
    with engine.begin() as connection:
        connection.execute(update(versions).values(payload="{}"))
    with pytest.raises(ValueError, match="integrity_failed"):
        service.require_current(principal, previous, "store")


@pytest.mark.parametrize("role", ["worker", "service"])
def test_execution_credentials_cannot_grant_video_rights(video_policy, role):
    service, principal, policy, _, _, _ = video_policy
    with pytest.raises(PermissionError, match="user_policy_authority"):
        service.install(replace(principal, roles=frozenset({role})), policy, expected_revision=0)


@pytest.mark.parametrize(
    "change",
    [
        {"consent": None},
        {"media_kind": "image"},
        {"personal_likeness": "false"},
        {"purposes": ("inspect", "clone")},
        {"subjects": ("owner", "owner")},
        {"automatic_face_clone": True},
    ],
)
def test_closed_video_terms_do_not_infer_consent_or_additional_uses(video_policy, change):
    _, _, policy, _, _, _ = video_policy
    with pytest.raises(ValueError):
        PersonaVideoPolicy.model_validate(policy.model_dump() | change)


def test_concurrent_initial_policy_install_has_one_audited_winner(video_policy):
    service, principal, policy, _, _, engine = video_policy
    barrier = Barrier(2)

    def install():
        barrier.wait(timeout=3)
        try:
            service.install(principal, policy, expected_revision=0)
            return True
        except ValueError as error:
            assert str(error) == "persona_policy_conflict"
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(lambda _: install(), range(2))) == 1
    with engine.connect() as connection:
        assert len(connection.execute(select(versions)).all()) == 1
