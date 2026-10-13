"""Compare Pi deployment facts before Hub-owned source identity issuance."""

from dataclasses import dataclass

from agent.ports.evidence_identity import SourceEvidenceIdentity
from agent.ports.pi_runtime_deployment import PiRuntimeDeploymentPort
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from ananta_contracts.pi_runtime_manifest import PiRuntimeManifest


@dataclass(frozen=True, slots=True)
class AdmittedPiRuntimeManifest:
    manifest: PiRuntimeManifest
    source: SourceEvidenceIdentity


class PiRuntimeManifestAdmissionService:
    def __init__(self, *, deployments: PiRuntimeDeploymentPort, registry: HubEvidenceRegistryService):
        self._deployments, self._registry = deployments, registry

    def admit(self, *, manifest: PiRuntimeManifest, policy_digest: str) -> AdmittedPiRuntimeManifest:
        expected = self._require_deployment(manifest)
        source = self._registry.register_source(
            tenant_id=expected.tenant_id, project_id=expected.project_id, origin_type="pi_runtime_deployment",
            origin_digest=expected.digest, content_digest=expected.digest, policy_digest=policy_digest,
            evidence_scope=expected.evidence_scope, synthetic=expected.synthetic,
        )
        return AdmittedPiRuntimeManifest(expected, source)

    def require_admitted(
        self, *, manifest: PiRuntimeManifest, source_id: str, source_binding_digest: str, policy_digest: str,
    ) -> AdmittedPiRuntimeManifest:
        expected = self._require_deployment(manifest)
        source = self._registry.require_source_identity(
            tenant_id=expected.tenant_id, project_id=expected.project_id, source_id=source_id,
            expected_binding_digest=source_binding_digest,
        )
        if (
            source.origin_type != "pi_runtime_deployment" or source.origin_digest != expected.digest
            or source.content_digest != expected.digest or source.policy_digest != policy_digest
            or source.evidence_scope != expected.evidence_scope or source.synthetic is not expected.synthetic
        ):
            raise ValueError("pi_runtime_source_binding_mismatch")
        return AdmittedPiRuntimeManifest(expected, source)

    def _require_deployment(self, manifest: PiRuntimeManifest) -> PiRuntimeManifest:
        expected = PiRuntimeManifest.from_mapping(manifest.to_dict())
        observed = self._deployments.require_manifest(
            tenant_id=expected.tenant_id, project_id=expected.project_id,
            worker_id=expected.worker_id, worker_url=expected.worker_url,
        )
        if not isinstance(observed, PiRuntimeManifest) or observed != expected:
            raise ValueError("pi_runtime_deployment_binding_mismatch")
        return expected
