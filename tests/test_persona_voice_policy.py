"""Registered test-only voice proofs; neither ASR nor image rights imply speech."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from sqlalchemy import update

from agent.models.persona_asset_policy import PersonaSourcePin, PersonaVoicePolicy
from agent.repositories.persona_voice_policies import create_voice_policy_repository, versions
from agent.services.persona_voice_policy_service import create_voice_policy_service
from ananta_contracts.meet_voice_catalog import DEFAULT_VOICE_ID
from ananta_contracts.persona_voice import inspect_voice_descriptor, voice_descriptor
from tests.test_persona_asset_policy import configured as configured


@pytest.fixture
def voice_policy(request):
    images, principal, image_policy, engine = request.getfixturevalue("configured")
    descriptor = inspect_voice_descriptor(voice_descriptor(DEFAULT_VOICE_ID))
    identity = images.sources.register_source(
        tenant_id="tenant",
        project_id="project",
        origin_type="persona_voice",
        origin_digest=descriptor.source_sha256,
        content_digest=descriptor.source_sha256,
        policy_digest="a" * 64,
        evidence_scope="test",
        synthetic=True,
    )
    policy = PersonaVoicePolicy.model_validate(
        image_policy.model_dump()
        | {
            "media_kind": "voice",
            "origin_kind": "licensed_pack",
            "policy_binding": "voice-policy",
            "source": PersonaSourcePin(source_id=identity.source_id, binding_digest=identity.binding_digest),
        }
    )
    repository = create_voice_policy_repository(engine)
    repository.initialize()
    service = create_voice_policy_service(
        access=images.access,
        policies=repository,
        sources=images.sources,
        inspection_receipts=Mock(),
        clock=lambda: 1000,
    )
    return service, principal, policy, images, image_policy, engine, descriptor


def admit(service, principal, policy, descriptor):
    return service.admit(
        principal,
        "project",
        descriptor.source_sha256,
        origin_binding=policy.source.source_id,
        license_binding=policy.license.source_id,
        consent_binding=policy.consent.source_id,
    )


def test_voice_requires_explicit_policy_domain_and_separate_rows(voice_policy):
    service, principal, policy, images, image_policy, _, descriptor = voice_policy
    images.install(principal, image_policy, expected_revision=0)
    with pytest.raises(ValueError, match="unavailable"):
        admit(service, principal, policy, descriptor)
    for target, value in ((service, image_policy), (images, policy)):
        with pytest.raises(PermissionError, match="media_kind_mismatch"):
            target.install(principal, value, expected_revision=0)
        with pytest.raises(ValueError, match="media_kind_mismatch"):
            target.policies.install(value, expected_revision=0, actor="owner")
    service.require_media_kind("voice")
    for kind in ("image", "video", "asr", None):
        with pytest.raises(PermissionError, match="media_kind_mismatch"):
            service.require_media_kind(kind)
    service.install(principal, policy, expected_revision=0)
    previous = admit(service, principal, policy, descriptor)
    service.require_current(principal, previous, "preview")
    with pytest.raises(PermissionError, match="use_denied"):
        service.require_current(principal, previous, "publish")


@pytest.mark.parametrize("kind", ["persona_image", "persona_video", "speech_correction", "license_document"])
def test_other_source_kinds_cannot_be_relabelled_as_tts(voice_policy, kind):
    service, principal, policy, _, _, _, descriptor = voice_policy
    identity = service.sources.register_source(
        tenant_id="tenant",
        project_id="project",
        origin_type=kind,
        origin_digest=descriptor.source_sha256,
        content_digest=descriptor.source_sha256,
        policy_digest="a" * 64,
        evidence_scope="test",
        synthetic=True,
    )
    changed = PersonaVoicePolicy.model_validate(
        policy.model_dump()
        | {"source": PersonaSourcePin(source_id=identity.source_id, binding_digest=identity.binding_digest)}
    )
    with pytest.raises(PermissionError, match="source_media_kind_mismatch"):
        service.install(principal, changed, expected_revision=0)


@pytest.mark.parametrize("classification", ["production", "synthetic"])
def test_test_proofs_never_promote_voice_assets(voice_policy, classification):
    service, principal, policy, *_ = voice_policy
    changed = PersonaVoicePolicy.model_validate(policy.model_dump() | {"classification": classification})
    with pytest.raises(PermissionError, match="cannot_be_promoted"):
        service.install(principal, changed, expected_revision=0)


@pytest.mark.parametrize(
    "change",
    [
        {"consent": None},
        {"media_kind": "image"},
        {"origin_kind": "upload"},
        {"origin_kind": "generated"},
        {"personal_likeness": "false"},
        {"purposes": ("voice_clone",)},
        {"model_url": "https://example.invalid"},
    ],
)
def test_closed_preset_terms_do_not_authorize_cloning_or_implicit_consent(voice_policy, change):
    with pytest.raises(ValueError):
        PersonaVoicePolicy.model_validate(voice_policy[2].model_dump() | change)


def test_regrant_consumes_revision_and_revokes_old_admission(voice_policy):
    service, principal, policy, _, _, _, descriptor = voice_policy
    service.install(principal, policy, expected_revision=0)
    previous = admit(service, principal, policy, descriptor)
    assert service.revoke_policy(principal, "project", policy.source.source_id, expected_revision=1) == 2
    with pytest.raises(ValueError, match="unavailable"):
        service.require_current(principal, previous, "preview")
    fresh = PersonaVoicePolicy.model_validate(policy.model_dump() | {"revision": 3})
    service.install(principal, fresh, expected_revision=2)
    with pytest.raises(PermissionError, match="revision_changed"):
        service.require_current(principal, previous, "preview")


@pytest.mark.parametrize("role", ["worker", "service"])
def test_execution_credentials_cannot_grant_voice_policy(voice_policy, role):
    service, principal, policy, *_ = voice_policy
    with pytest.raises(PermissionError, match="user_policy_authority"):
        service.install(replace(principal, roles=frozenset({role})), policy, expected_revision=0)


def test_membership_expiry_and_policy_corruption_fail_closed(voice_policy):
    service, principal, policy, _, _, engine, descriptor = voice_policy
    service.install(principal, policy, expected_revision=0)
    previous = admit(service, principal, policy, descriptor)
    service.access.require.side_effect = PermissionError("revoked membership")
    with pytest.raises(PermissionError):
        service.require_current(principal, previous, "store")
    service.access.require.side_effect = None
    service.clock = lambda: 2001
    with pytest.raises(PermissionError, match="use_denied"):
        service.require_current(principal, previous, "store")
    service.clock = lambda: 1000
    with engine.begin() as connection:
        connection.execute(update(versions).values(payload="{}"))
    with pytest.raises(ValueError, match="integrity_failed"):
        service.require_current(principal, previous, "store")
