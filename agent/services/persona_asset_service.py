"""Hub-owned asset lifecycle; decoding remains delegated through a task port."""

from dataclasses import dataclass, field
from typing import Protocol

from agent.models.persona_assets import PersonaAssetAdmission, PersonaImageAsset
from agent.services.persona_asset_admission_formats import PersonaImageAdmissionFormat
from agent.services.persona_asset_lifecycle import PersonaAssetLifecycle
from agent.services.persona_asset_storage import InspectedPersonaImagePort


@dataclass(frozen=True)
class PersonaInspectionResult:
    task_id: str
    lease_id: str
    image: InspectedPersonaImagePort = field(repr=False)
    run_id: str | None = None
    assignment_id: str | None = None
    run_binding_digest: str | None = None


class PersonaAssetPolicyPort(Protocol):
    def require_lookup(self, principal, project: str, artifact_id: str, purpose: str) -> None: ...
    def admit(
        self, principal, project, source_sha256, *, origin_binding, license_binding, consent_binding
    ) -> PersonaAssetAdmission: ...
    def require_current(self, principal, admission: PersonaAssetAdmission, purpose: str) -> None: ...
    def require_completed_inspection(
        self, principal, admission: PersonaAssetAdmission, result: PersonaInspectionResult
    ) -> None: ...
    def require_asset(self, principal, asset: PersonaImageAsset, purpose: str) -> None: ...
    def require_revoke(self, principal, project: str, artifact_id: str) -> None: ...


class PersonaInspectionTaskPort(Protocol):
    def execute(
        self, principal, admission: PersonaAssetAdmission, content: bytes, media_type: str
    ) -> PersonaInspectionResult:
        """Create/delegate/verify a normal Hub task, never decode inside the Hub."""
        ...


class PersonaAssetService:
    def __init__(self, *, policy: PersonaAssetPolicyPort, tasks: PersonaInspectionTaskPort, catalog, storage):
        self.policy, self.tasks, self.catalog, self.storage = policy, tasks, catalog, storage

    def _lifecycle(self):
        return PersonaAssetLifecycle(
            policy=self.policy,
            tasks=self.tasks,
            catalog=self.catalog,
            storage=self.storage,
            format=PersonaImageAdmissionFormat(),
        )

    def admit_image(
        self, principal, project, *, content, media_type, origin_binding, license_binding, consent_binding=None
    ):
        return self._lifecycle().admit(
            principal,
            project,
            content=content,
            media_type=media_type,
            origin_binding=origin_binding,
            license_binding=license_binding,
            consent_binding=consent_binding,
        )

    def read_image(self, principal, project, artifact_id, *, purpose="preview"):
        return self._lifecycle().read(principal, project, artifact_id, purpose=purpose)

    def revoke(self, principal, project, artifact_id, *, expected_revision):
        return self._lifecycle().revoke(principal, project, artifact_id, expected_revision=expected_revision)
