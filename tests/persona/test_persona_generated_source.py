"""SQL-issued synthetic generation receipts, not production generation evidence."""

import hashlib
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select, update

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.models.persona_asset_policy import PersonaSourcePin
from agent.models.persona_generated_source import PersonaGeneratedOutput, PersonaGenerationRunPin
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from agent.services.persona_generated_source import PersonaGeneratedSourceAdmission
from agent.services.project_access_authority import ProjectCapability
from agent.services.source_control_access_policy import HubSourcePrincipal

pytestmark = pytest.mark.timeout(30)


@pytest.fixture
def generated(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'generation.db'}")
    HubSourceEvidenceIdentityDB.__table__.create(engine)
    HubRunEvidenceIdentityDB.__table__.create(engine)
    registry = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(engine))

    def source(kind, number, *, scope="test", synthetic=True):
        digest = hashlib.sha256(str(number).encode()).hexdigest()
        value = registry.register_source(
            tenant_id="tenant",
            project_id="project",
            origin_type=kind,
            origin_digest=digest,
            content_digest=digest,
            policy_digest="a" * 64,
            evidence_scope=scope,
            synthetic=synthetic,
        )
        return PersonaSourcePin(source_id=value.source_id, binding_digest=value.binding_digest)

    content = b"explicit synthetic generated media bytes"
    output = PersonaGeneratedOutput(
        schema_version="ananta.persona-generated-output.v1",
        tenant_id="tenant",
        project_id="project",
        owner_subject="owner",
        media_kind="image",
        media_type="image/png",
        content_sha256=hashlib.sha256(content).hexdigest(),
        content_size=len(content),
        inputs=(source("generation_model", 1),),
        license=source("license_document", 2),
        consent=source("media_consent", 3),
        personal_likeness=True,
        classification="test_only",
    )
    principal = HubSourcePrincipal("owner", "tenant", "project", frozenset({"user"}))
    access = Mock()
    service = PersonaGeneratedSourceAdmission(access=access, registry=registry)

    def reserve(value=output, *, scope="test", synthetic=True):
        run = registry.reserve_run(
            tenant_id=value.tenant_id,
            project_id=value.project_id,
            task_id="generation-task",
            assignment_id="generation-assignment",
            dispatch_lease_id="generation-lease",
            repository_revision="1" * 40,
            input_digest="b" * 64,
            execution_profile_digest="c" * 64,
            environment_digest="d" * 64,
            source_ids=[p.source_id for p in value.source_pins()],
            evidence_scope=scope,
            synthetic=synthetic,
            idempotency_key="test-generation-reservation",
        )
        pin = PersonaGenerationRunPin(
            run_id=run.run_id,
            task_id=run.task_id,
            assignment_id=run.assignment_id,
            dispatch_lease_id=run.dispatch_lease_id,
            input_digest=run.input_digest,
            expected_binding_digest=run.binding_digest,
        )
        return pin

    def complete(pin, value=output, *, state="succeeded"):
        registry.record_result(
            tenant_id=value.tenant_id,
            project_id=value.project_id,
            run_id=pin.run_id,
            assignment_id=pin.assignment_id,
            dispatch_lease_id=pin.dispatch_lease_id,
            terminal_state=state,
            result_digest=value.digest(),
        )

    yield SimpleNamespace(
        engine=engine,
        registry=registry,
        service=service,
        principal=principal,
        output=output,
        content=content,
        reserve=reserve,
        complete=complete,
        access=access,
        source=source,
    )
    engine.dispose()


def admit(case, pin, *, output=None, content=None, principal=None, project="project"):
    return case.service.admit(
        principal or case.principal,
        project,
        output=output or case.output,
        run_pin=pin,
        content=case.content if content is None else content,
    )


def sources(case):
    with case.engine.connect() as connection:
        return connection.execute(select(HubSourceEvidenceIdentityDB.__table__)).mappings().all()


def test_only_exact_completed_pre_reserved_result_issues_idempotent_scoped_source(generated):
    c = generated
    pin = c.reserve()
    with pytest.raises(ValueError, match="unavailable"):
        admit(c, pin)
    assert len(sources(c)) == 3
    c.complete(pin)
    source = admit(c, pin)
    assert admit(c, pin) == source
    assert len(sources(c)) == 4
    proof = c.registry.require_source_identity(
        tenant_id="tenant",
        project_id="project",
        source_id=source.source_id,
        expected_binding_digest=source.binding_digest,
    )
    assert (proof.origin_type, proof.content_digest, proof.evidence_scope, proof.synthetic) == (
        "persona_image",
        c.output.content_sha256,
        "test",
        True,
    )
    assert proof.policy_digest == c.output.digest()
    assert all(call.kwargs["capability"] == ProjectCapability.MANAGE for call in c.access.require.call_args_list)


@pytest.mark.parametrize("state", ["failed", "cancelled"])
def test_terminal_failed_or_cancelled_run_never_admits(generated, state):
    pin = generated.reserve()
    generated.complete(pin, state=state)
    with pytest.raises(ValueError):
        admit(generated, pin)
    assert len(sources(generated)) == 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "RUN_unknown"),
        ("task_id", "foreign"),
        ("assignment_id", "old"),
        ("dispatch_lease_id", "stale"),
        ("input_digest", "0" * 64),
        ("expected_binding_digest", "0" * 64),
    ],
)
def test_any_run_binding_change_fails_before_source_issuance(generated, field, value):
    pin = generated.reserve()
    generated.complete(pin)
    with pytest.raises(ValueError):
        admit(generated, pin.model_copy(update={field: value}))
    assert len(sources(generated)) == 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_subject", "foreign"),
        ("tenant_id", "foreign"),
        ("project_id", "foreign"),
        ("media_type", "image/jpeg"),
        ("personal_likeness", False),
        ("classification", "synthetic"),
        ("content_sha256", "0" * 64),
        ("content_size", 1),
    ],
)
def test_any_manifest_mutation_is_not_the_completed_result(generated, field, value):
    pin = generated.reserve()
    generated.complete(pin)
    with pytest.raises((ValueError, PermissionError)):
        admit(generated, pin, output=generated.output.model_copy(update={field: value}))
    assert len(sources(generated)) == 3


@pytest.mark.parametrize("content", [b"", b"changed", "not-bytes", bytearray(b"synthetic")])
def test_wrong_bytes_never_register_a_source(generated, content):
    pin = generated.reserve()
    generated.complete(pin)
    with pytest.raises(ValueError):
        admit(generated, pin, content=content)
    assert len(sources(generated)) == 3


@pytest.mark.parametrize("role", ["worker", "service"])
def test_worker_or_service_cannot_invoke_management_admission(generated, role):
    principal = replace(generated.principal, roles=frozenset({"user", role}))
    with pytest.raises(PermissionError):
        admit(generated, generated.reserve(), principal=principal)
    generated.access.require.assert_not_called()


@pytest.mark.parametrize("step", [1, 2, 3])
def test_concurrent_authority_revocation_never_returns_pin(generated, step):
    pin = generated.reserve()
    generated.complete(pin)
    generated.access.require.side_effect = [None] * (step - 1) + [PermissionError("revoked")]
    with pytest.raises(PermissionError, match="revoked"):
        admit(generated, pin)
    # A final-stage race can leave an immutable fact, never a publication grant.
    assert len(sources(generated)) == (4 if step == 3 else 3)


@pytest.mark.parametrize(
    "table,field,value",
    [
        (HubRunEvidenceIdentityDB, "repository_revision", "0" * 40),
        (HubRunEvidenceIdentityDB, "environment_digest", "0" * 64),
        (HubRunEvidenceIdentityDB, "state", "failed"),
        (HubSourceEvidenceIdentityDB, "state", "revoked"),
        (HubSourceEvidenceIdentityDB, "content_digest", "0" * 64),
    ],
)
def test_changed_registry_rows_are_not_trusted(generated, table, field, value):
    pin = generated.reserve()
    generated.complete(pin)
    with generated.engine.begin() as connection:
        connection.execute(update(table.__table__).values(**{field: value}))
    with pytest.raises(ValueError):
        admit(generated, pin)
    assert len(sources(generated)) == 3


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "other"},
        {"extra": "secret"},
        {"media_kind": "voice"},
        {"media_kind": "video"},
        {"media_type": "image/svg+xml"},
        {"content_size": True},
        {"content_size": 0},
        {"content_size": 5 * 1024 * 1024 + 1},
        {"consent": None},
        {"classification": "production"},
        {"inputs": []},
        {"personal_likeness": 1},
    ],
)
def test_closed_manifest_rejects_unsafe_or_coerced_fields(generated, change):
    with pytest.raises(ValueError):
        PersonaGeneratedOutput.model_validate(generated.output.model_dump(mode="json") | change)


def test_duplicate_proofs_and_bypassed_model_validation_rejected(generated):
    output = generated.output
    with pytest.raises(ValueError):
        PersonaGeneratedOutput.model_validate(output.model_dump() | {"license": output.inputs[0]})
    pin = generated.reserve()
    generated.complete(pin)
    with pytest.raises(ValueError):
        admit(generated, pin, output=output.model_copy(update={"content_size": True}))


def test_video_result_uses_video_origin_and_same_receipt_boundary(generated):
    c = generated
    output = PersonaGeneratedOutput.model_validate(
        c.output.model_dump() | {"media_kind": "video", "media_type": "video/mp4"}
    )
    pin = c.reserve(output)
    c.complete(pin, output)
    source = admit(c, pin, output=output)
    proof = c.registry.require_source_identity(
        tenant_id="tenant",
        project_id="project",
        source_id=source.source_id,
        expected_binding_digest=source.binding_digest,
    )
    assert proof.origin_type == "persona_video"


def test_real_generated_content_classification_does_not_mean_synthetic_test_evidence(generated):
    c = generated
    # Explicit local-scope policy fixtures exercise the non-production path.
    # No release gate is invoked and no real generator execution is claimed.
    output = PersonaGeneratedOutput.model_validate(
        c.output.model_dump()
        | {
            "classification": "synthetic",
            "inputs": (c.source("generation_model", 4, scope="local", synthetic=False),),
            "license": c.source("license_document", 5, scope="local", synthetic=False),
            "consent": c.source("media_consent", 6, scope="local", synthetic=False),
        }
    )
    pin = c.reserve(output, scope="local", synthetic=False)
    c.complete(pin, output)
    source = admit(c, pin, output=output)
    proof = c.registry.require_source_identity(
        tenant_id="tenant",
        project_id="project",
        source_id=source.source_id,
        expected_binding_digest=source.binding_digest,
    )
    assert proof.evidence_scope == "local" and proof.synthetic is False
    assert output.classification == "synthetic"
    release = c.registry.verify_release_binding(
        tenant_id="tenant",
        project_id="project",
        run_id=pin.run_id,
        required_scope="production",
        task_id=pin.task_id,
        repository_revision="1" * 40,
        source_ids=[p.source_id for p in output.source_pins()],
    )
    assert not release.verified


@pytest.mark.parametrize("classification,synthetic", [("synthetic", True), ("test_only", False)])
def test_successful_run_cannot_cross_the_declared_evidence_classification(generated, classification, synthetic):
    c = generated
    output = PersonaGeneratedOutput.model_validate(c.output.model_dump() | {"classification": classification})
    pin = c.reserve(output, synthetic=synthetic)
    c.complete(pin, output)
    with pytest.raises(PermissionError, match="classification_mismatch"):
        admit(c, pin, output=output)
    assert len(sources(c)) == 3


@pytest.mark.parametrize("kind", ["license", "consent"])
def test_registered_unrelated_input_cannot_replace_a_rights_document(generated, kind):
    c = generated
    output = PersonaGeneratedOutput.model_validate(
        c.output.model_dump()
        | {
            kind: c.output.inputs[0],
            "inputs": (getattr(c.output, kind),),
        }
    )
    pin = c.reserve(output)
    c.complete(pin, output)
    with pytest.raises(PermissionError, match="proof_kind_mismatch"):
        admit(c, pin, output=output)
    assert len(sources(c)) == 3


def test_input_revocation_during_registration_never_returns_a_pin(generated, monkeypatch):
    c = generated
    pin = c.reserve()
    c.complete(pin)
    original = c.registry.register_source

    def register(**kwargs):
        result = original(**kwargs)
        table = HubSourceEvidenceIdentityDB.__table__
        with c.engine.begin() as connection:
            connection.execute(
                update(table).where(table.c.source_id == c.output.license.source_id).values(state="revoked")
            )
        return result

    monkeypatch.setattr(c.registry, "register_source", register)
    with pytest.raises(ValueError, match="unavailable"):
        admit(c, pin)
