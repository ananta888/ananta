"""Hub image permissions from project authority and pinned registered evidence."""

import time
from typing import Protocol

from agent.models.persona_asset_policy import PersonaImagePolicy
from agent.models.persona_assets import PersonaAssetAdmission
from agent.services.project_access_authority import ProjectCapability


class PinnedPersonaSourcePort(Protocol):
    def require_source_identity(self, *, tenant_id, project_id, source_id, expected_binding_digest): ...


class PersonaInspectionReceiptPort(Protocol):
    def require_completed(self, principal, admission, result) -> None: ...
    def require_asset(self, principal, asset) -> None: ...


class PersonaAssetPolicyService:
    def __init__(
        self,
        *,
        access,
        policies,
        sources: PinnedPersonaSourcePort,
        inspection_receipts: PersonaInspectionReceiptPort,
        clock=time.time,
    ):
        self.access, self.policies, self.sources = access, policies, sources
        self.inspection_receipts, self.clock = inspection_receipts, clock

    def _project(self, principal, project, capability):
        if principal.roles & {"worker", "service"}:
            raise PermissionError("persona_user_policy_authority_required")
        if (
            not principal.tenant_id
            or not principal.subject_id
            or (principal.project_id and principal.project_id != project)
        ):
            raise PermissionError("persona_project_context_denied")
        self.access.require(
            tenant_id=principal.tenant_id,
            project_id=project,
            subject_id=principal.subject_id,
            capability=capability,
            tenant_admin=principal.is_admin,
        )

    def _proofs(self, policy):
        pins = {"source": policy.source, "license": policy.license}
        if policy.consent:
            pins["consent"] = policy.consent
        proofs = {}
        for kind, pin in pins.items():
            proof = self.sources.require_source_identity(
                tenant_id=policy.tenant_id,
                project_id=policy.project_id,
                source_id=pin.source_id,
                expected_binding_digest=pin.binding_digest,
            )
            if (proof.synthetic or proof.evidence_scope == "test") and policy.classification != "test_only":
                raise PermissionError("persona_test_proof_cannot_be_promoted")
            if (
                kind in ("license", "consent")
                and proof.origin_type != {"license": "license_document", "consent": "media_consent"}[kind]
            ):
                raise PermissionError("persona_proof_kind_mismatch")
            proofs[kind] = proof
        return proofs

    def install(self, principal, policy: PersonaImagePolicy, *, expected_revision):
        if principal.tenant_id != policy.tenant_id:
            raise PermissionError("persona_policy_tenant_mismatch")
        self._project(principal, policy.project_id, ProjectCapability.MANAGE)
        if not self.clock() * 1000 < policy.expires_at_ms <= (self.clock() + 366 * 86400) * 1000:
            raise PermissionError("persona_policy_expiry_invalid")
        self._proofs(policy)
        self._project(principal, policy.project_id, ProjectCapability.MANAGE)
        self.policies.install(policy, expected_revision=expected_revision, actor=principal.subject_id)

    def revoke_policy(self, principal, project, source_id, *, expected_revision):
        self._project(principal, project, ProjectCapability.MANAGE)
        return self.policies.revoke(
            principal.tenant_id, project, source_id, expected_revision=expected_revision, actor=principal.subject_id
        )

    def _require(self, principal, policy, purpose):
        capability = ProjectCapability.READ if purpose == "preview" else ProjectCapability.WRITE
        self._project(principal, policy.project_id, capability)
        if (
            principal.tenant_id != policy.tenant_id
            or principal.subject_id not in policy.subjects
            or purpose not in policy.purposes
            or self.clock() * 1000 >= policy.expires_at_ms
        ):
            raise PermissionError("persona_policy_use_denied")
        return self._proofs(policy)

    @staticmethod
    def _admission(policy, proofs):
        return PersonaAssetAdmission(
            tenant_id=policy.tenant_id,
            project_id=policy.project_id,
            source_sha256=proofs["source"].content_digest,
            origin_kind=policy.origin_kind,
            origin_binding=policy.source.source_id,
            license_binding=policy.license.source_id,
            consent_binding=policy.consent.source_id if policy.consent else None,
            policy_binding=policy.policy_binding,
            policy_revision=policy.revision,
            classification=policy.classification,
        )

    def admit(self, principal, project, source_sha256, *, origin_binding, license_binding, consent_binding):
        self._project(principal, project, ProjectCapability.WRITE)
        policy = self.policies.for_source(principal.tenant_id, project, origin_binding)
        proofs = self._require(principal, policy, "inspect")
        admission = self._admission(policy, proofs)
        if (admission.source_sha256, admission.license_binding, admission.consent_binding) != (
            source_sha256,
            license_binding,
            consent_binding,
        ):
            raise PermissionError("persona_policy_source_mismatch")
        return admission

    def require_current(self, principal, admission, purpose):
        self._project(
            principal, admission.project_id, ProjectCapability.READ if purpose == "preview" else ProjectCapability.WRITE
        )
        policy = self.policies.for_source(principal.tenant_id, admission.project_id, admission.origin_binding)
        proofs = self._require(principal, policy, purpose)
        if admission != self._admission(policy, proofs):
            raise PermissionError("persona_policy_revision_changed")

    def require_completed_inspection(self, principal, admission, result):
        self.require_current(principal, admission, "inspect")
        self.inspection_receipts.require_completed(principal, admission, result)

    def require_lookup(self, principal, project, artifact_id, purpose):
        self._project(principal, project, ProjectCapability.READ if purpose == "preview" else ProjectCapability.WRITE)

    def require_list(self, principal, project):
        self._project(principal, project, ProjectCapability.READ)

    def require_asset(self, principal, asset, purpose):
        admission = PersonaAssetAdmission(
            tenant_id=asset.image.tenant_id,
            project_id=asset.image.project_id,
            source_sha256=asset.source_sha256,
            origin_kind=asset.origin_kind,
            origin_binding=asset.origin_binding,
            license_binding=asset.license_binding,
            consent_binding=asset.consent_binding,
            policy_binding=asset.policy_binding,
            policy_revision=asset.policy_revision,
            classification=asset.image.classification,
        )
        self.require_current(principal, admission, purpose)
        self.inspection_receipts.require_asset(principal, asset)

    def require_revoke(self, principal, project, artifact_id):
        self._project(principal, project, ProjectCapability.MANAGE)
