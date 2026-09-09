"""Admit exact completed generation output; never generate, decode or grant use."""

import hashlib
from typing import Protocol

from agent.models.persona_asset_policy import PersonaSourcePin
from agent.models.persona_generated_source import PersonaGeneratedOutput, PersonaGenerationRunPin, receipt_digest
from agent.services.project_access_authority import ProjectCapability


class GenerationRegistryPort(Protocol):
    def require_source_identity(self, *, tenant_id, project_id, source_id, expected_binding_digest): ...
    def require_run_result(self, **bindings): ...
    def register_source(self, **bindings): ...


class PersonaGeneratedSourceAdmission:
    def __init__(self, *, access, registry: GenerationRegistryPort):
        self.access, self.registry = access, registry

    def _authorize(self, principal, project):
        if (
            principal.roles & {"worker", "service"}
            or not principal.tenant_id
            or not principal.subject_id
            or (principal.project_id and principal.project_id != project)
        ):
            raise PermissionError("persona_generation_authority_denied")
        self.access.require(
            tenant_id=principal.tenant_id,
            project_id=project,
            subject_id=principal.subject_id,
            capability=ProjectCapability.MANAGE,
            tenant_admin=principal.is_admin,
        )

    def _verify(self, output, pin):
        run = self.registry.require_run_result(
            tenant_id=output.tenant_id,
            project_id=output.project_id,
            source_ids=tuple(source.source_id for source in output.source_pins()),
            result_digest=output.digest(),
            **pin.model_dump(),
        )
        test = output.classification == "test_only"
        if (run.evidence_scope, run.synthetic) != (("test", True) if test else ("local", False)):
            raise PermissionError("persona_generation_evidence_classification_mismatch")
        for source in output.source_pins():
            proof = self.registry.require_source_identity(
                tenant_id=output.tenant_id,
                project_id=output.project_id,
                source_id=source.source_id,
                expected_binding_digest=source.binding_digest,
            )
            if not test and (proof.synthetic or proof.evidence_scope == "test"):
                raise PermissionError("persona_generation_test_input_not_promotable")
            required_kind = (
                "license_document"
                if source == output.license
                else "media_consent"
                if source == output.consent
                else None
            )
            if required_kind is not None and proof.origin_type != required_kind:
                raise PermissionError("persona_generation_proof_kind_mismatch")
        return run

    def admit(self, principal, project, *, output, run_pin, content):
        self._authorize(principal, project)
        # Reparse also rejects model_construct/model_copy values bypassing validators.
        output = PersonaGeneratedOutput.model_validate_json(output.model_dump_json())
        pin = PersonaGenerationRunPin.model_validate_json(run_pin.model_dump_json())
        if (output.tenant_id, output.project_id, output.owner_subject) != (
            principal.tenant_id,
            project,
            principal.subject_id,
        ):
            raise PermissionError("persona_generation_output_scope_mismatch")
        if (
            type(content) is not bytes
            or len(content) != output.content_size
            or hashlib.sha256(content).hexdigest() != output.content_sha256
        ):
            raise ValueError("persona_generation_content_mismatch")
        self._verify(output, pin)
        self._authorize(principal, project)
        source = self.registry.register_source(
            tenant_id=output.tenant_id,
            project_id=output.project_id,
            origin_type=f"persona_{output.media_kind}",
            origin_digest=receipt_digest(
                {"run": pin.model_dump(mode="json"), "output": output.model_dump(mode="json")}
            ),
            content_digest=output.content_sha256,
            policy_digest=output.digest(),
            evidence_scope="test" if output.classification == "test_only" else "local",
            synthetic=output.classification == "test_only",
        )
        # An immutable source fact may remain registered after a concurrent
        # revocation. It is not a publication grant; never return a stale pin.
        self._verify(output, pin)
        self._authorize(principal, project)
        return PersonaSourcePin(source_id=source.source_id, binding_digest=source.binding_digest)
