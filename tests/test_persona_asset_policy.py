"""Real Hub-issued test identities; explicit policy fixtures, never release evidence."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select, update

from agent.db_models.evidence_identity import HubSourceEvidenceIdentityDB
from agent.models.persona_asset_policy import PersonaImagePolicy, PersonaSourcePin
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.repositories.persona_asset_policy import SqlPersonaImagePolicies, versions
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from agent.services.persona_asset_policy_service import PersonaAssetPolicyService
from agent.services.project_access_authority import ProjectCapability
from agent.services.source_control_access_policy import HubSourcePrincipal

pytestmark = pytest.mark.timeout(30)


@pytest.fixture
def configured(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'policies.db'}")
    HubSourceEvidenceIdentityDB.__table__.create(engine)
    sources = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(engine), clock=lambda: 1000)

    def register(kind, digest):
        identity = sources.register_source(
            tenant_id="tenant",
            project_id="project",
            origin_type=kind,
            origin_digest=digest,
            content_digest=digest,
            policy_digest="a" * 64,
            evidence_scope="test",
            synthetic=True,
        )
        return PersonaSourcePin(source_id=identity.source_id, binding_digest=identity.binding_digest)

    source, license, consent = (
        register("persona_image", "b" * 64),
        register("license_document", "c" * 64),
        register("media_consent", "d" * 64),
    )
    policy = PersonaImagePolicy(
        tenant_id="tenant",
        project_id="project",
        policy_binding="image-policy",
        revision=1,
        source=source,
        license=license,
        consent=consent,
        origin_kind="upload",
        personal_likeness=True,
        classification="test_only",
        subjects=("owner",),
        purposes=("inspect", "store", "preview"),
        expires_at_ms=2_000_000,
    )
    policies = SqlPersonaImagePolicies(engine)
    policies.initialize()
    service = PersonaAssetPolicyService(
        access=Mock(), policies=policies, sources=sources, inspection_receipts=Mock(), clock=lambda: 1000
    )
    principal = HubSourcePrincipal("owner", "tenant", "project", frozenset({"user"}))
    yield service, principal, policy, engine
    engine.dispose()


def admit(service, principal, policy):
    return service.admit(
        principal,
        "project",
        "b" * 64,
        origin_binding=policy.source.source_id,
        license_binding=policy.license.source_id,
        consent_binding=policy.consent.source_id,
    )


def test_managed_install_and_registered_proofs_do_not_grant_implicit_publication(configured):
    service, principal, policy, _ = configured
    service.install(principal, policy, expected_revision=0)
    assert service.access.require.call_args.kwargs["capability"] == ProjectCapability.MANAGE
    admission = admit(service, principal, policy)
    service.require_current(principal, admission, "preview")
    assert admission.classification == "test_only" and admission.origin_binding == policy.source.source_id
    with pytest.raises(PermissionError, match="use_denied"):
        service.require_current(principal, admission, "publish")


def test_unknown_policy_and_unknown_source_pin_are_not_admitted(configured):
    service, principal, policy, _ = configured
    with pytest.raises(ValueError, match="unavailable"):
        admit(service, principal, policy)
    unknown = policy.model_dump(mode="json")
    # This malformed lookup candidate is not an issued identity and must fail.
    unknown["license"]["source_id"] = "SRC_unknown_lookup_candidate"
    with pytest.raises(ValueError, match="unavailable"):
        service.install(principal, PersonaImagePolicy.model_validate(unknown), expected_revision=0)


def test_registered_test_license_cannot_be_promoted_to_production_asset(configured):
    service, principal, policy, _ = configured
    payload = policy.model_dump(mode="json") | {"classification": "production"}
    with pytest.raises(PermissionError, match="cannot_be_promoted"):
        service.install(principal, PersonaImagePolicy.model_validate(payload), expected_revision=0)


def test_registered_image_cannot_stand_in_for_a_license_document(configured):
    service, principal, policy, _ = configured
    payload = policy.model_dump(mode="json")
    payload["source"], payload["license"] = payload["license"], payload["source"]
    with pytest.raises(PermissionError, match="proof_kind_mismatch"):
        service.install(principal, PersonaImagePolicy.model_validate(payload), expected_revision=0)


def test_managed_revision_change_invalidates_prior_admission(configured):
    service, principal, policy, _ = configured
    service.install(principal, policy, expected_revision=0)
    admission = admit(service, principal, policy)
    revised = PersonaImagePolicy.model_validate(policy.model_dump() | {"revision": 2, "purposes": ("inspect", "store")})
    service.install(principal, revised, expected_revision=1)
    with pytest.raises(PermissionError, match="revision_changed"):
        service.require_current(principal, admission, "store")
    with pytest.raises(ValueError, match="conflict"):
        service.install(principal, revised, expected_revision=1)


def test_revocation_is_durable_and_stale_revoke_does_not_hide_new_revision(configured):
    service, principal, policy, engine = configured
    service.install(principal, policy, expected_revision=0)
    assert service.revoke_policy(principal, "project", policy.source.source_id, expected_revision=1) == 2
    with pytest.raises(ValueError, match="unavailable"):
        admit(service, principal, policy)
    with engine.connect() as connection:
        row = connection.execute(select(versions)).mappings().one()
        assert row["created_by"] == row["revoked_by"] == "owner"
    stale = PersonaImagePolicy.model_validate(policy.model_dump() | {"revision": 2})
    with pytest.raises(ValueError, match="conflict"):
        service.install(principal, stale, expected_revision=1)
    revised = PersonaImagePolicy.model_validate(policy.model_dump() | {"revision": 3})
    service.install(principal, revised, expected_revision=2)
    with pytest.raises(ValueError, match="conflict"):
        service.revoke_policy(principal, "project", policy.source.source_id, expected_revision=1)
    assert admit(service, principal, revised).policy_revision == 3


def test_project_membership_or_subject_change_revokes_use(configured):
    service, principal, policy, _ = configured
    service.install(principal, policy, expected_revision=0)
    with pytest.raises(PermissionError, match="use_denied"):
        admit(service, replace(principal, subject_id="not-listed"), policy)
    service.access.require.side_effect = PermissionError("project revoked")
    with pytest.raises(PermissionError, match="project revoked"):
        admit(service, principal, policy)


def test_concurrent_revoke_and_install_have_exactly_one_winner(configured):
    service, principal, policy, _ = configured
    service.install(principal, policy, expected_revision=0)
    barrier = Barrier(2)
    revised = PersonaImagePolicy.model_validate(policy.model_dump() | {"revision": 2})

    def change(operation):
        barrier.wait(timeout=3)
        try:
            if operation == "install":
                service.install(principal, revised, expected_revision=1)
            else:
                service.revoke_policy(principal, "project", policy.source.source_id, expected_revision=1)
            return operation
        except ValueError as error:
            assert str(error) == "persona_policy_conflict"
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(change, ("install", "revoke")))
    assert outcomes.count("conflict") == 1
    if "revoke" in outcomes:
        with pytest.raises(ValueError, match="unavailable"):
            admit(service, principal, policy)
    else:
        assert admit(service, principal, revised).policy_revision == 2


def test_modified_evidence_or_policy_payload_fails_closed(configured):
    service, principal, policy, engine = configured
    service.install(principal, policy, expected_revision=0)
    with engine.begin() as connection:
        connection.execute(
            update(HubSourceEvidenceIdentityDB)
            .where(HubSourceEvidenceIdentityDB.source_id == policy.license.source_id)
            .values(content_digest="e" * 64)
        )
    with pytest.raises(ValueError, match="mutated"):
        admit(service, principal, policy)
    with engine.begin() as connection:
        connection.execute(update(versions).values(payload="{}"))
    with pytest.raises(ValueError, match="integrity_failed"):
        admit(service, principal, policy)


def test_expired_policy_and_different_input_bytes_are_denied(configured):
    service, principal, policy, _ = configured
    service.install(principal, policy, expected_revision=0)
    with pytest.raises(PermissionError, match="source_mismatch"):
        service.admit(
            principal,
            "project",
            "f" * 64,
            origin_binding=policy.source.source_id,
            license_binding=policy.license.source_id,
            consent_binding=policy.consent.source_id,
        )
    service.clock = lambda: 2001
    with pytest.raises(PermissionError, match="use_denied"):
        admit(service, principal, policy)


@pytest.mark.parametrize("role", ["worker", "service"])
def test_execution_credentials_cannot_install_user_media_policy(configured, role):
    service, principal, policy, _ = configured
    with pytest.raises(PermissionError, match="user_policy_authority"):
        service.install(replace(principal, roles=frozenset({role})), policy, expected_revision=0)
    service.access.require.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"consent": None},
        {"personal_likeness": False, "consent": None},
        {"subjects": ("*",)},
        {"purposes": ("preview", "preview")},
        {"origin_kind": "generated", "classification": "production"},
    ],
)
def test_closed_policy_does_not_silently_broaden_consent_or_audience(configured, changes):
    _, _, policy, _ = configured
    with pytest.raises(ValueError):
        PersonaImagePolicy.model_validate(policy.model_dump() | changes)
