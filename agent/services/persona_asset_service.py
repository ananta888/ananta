"""Hub-owned asset lifecycle; decoding remains delegated through a task port."""

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from agent.models.persona_assets import PersonaAssetAdmission, PersonaImageAsset
from agent.models.persona_media import MediaAssetRef
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

    def admit_image(
        self, principal, project, *, content, media_type, origin_binding, license_binding, consent_binding=None
    ):
        if (
            not isinstance(content, bytes)
            or not 0 < len(content) <= 5 * 1024 * 1024
            or media_type not in ("image/png", "image/jpeg")
        ):
            raise ValueError("persona_asset_input_invalid")
        source_sha256 = hashlib.sha256(content).hexdigest()
        admission = self.policy.admit(
            principal,
            project,
            source_sha256,
            origin_binding=origin_binding,
            license_binding=license_binding,
            consent_binding=consent_binding,
        )
        if (
            admission.tenant_id != principal.tenant_id
            or admission.project_id != project
            or admission.source_sha256 != source_sha256
        ):
            raise ValueError("persona_asset_admission_mismatch")
        self.policy.require_current(principal, admission, "inspect")
        result = self.tasks.execute(principal, admission, content, media_type)
        if any(
            not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value)
            for value in (result.task_id, result.lease_id)
        ):
            raise ValueError("persona_asset_inspection_identity_invalid")
        self.policy.require_completed_inspection(principal, admission, result)
        if result.image.source_sha256 != source_sha256:
            raise ValueError("persona_asset_inspection_mismatch")

        def checkpoint():
            self.policy.require_current(principal, admission, "store")

        checkpoint()
        common = dict(
            tenant_id=admission.tenant_id,
            project_id=project,
            revision=1,
            kind="image",
            classification=admission.classification,
        )
        asset = PersonaImageAsset(
            image=MediaAssetRef(**common, artifact_id=str(uuid.uuid4()), sha256=result.image.image_sha256),
            preview=MediaAssetRef(**common, artifact_id=str(uuid.uuid4()), sha256=result.image.preview_sha256),
            source_sha256=source_sha256,
            origin_kind=admission.origin_kind,
            origin_binding=admission.origin_binding,
            license_binding=admission.license_binding,
            consent_binding=admission.consent_binding,
            policy_binding=admission.policy_binding,
            policy_revision=admission.policy_revision,
            inspection_task_id=result.task_id,
            inspection_lease_id=result.lease_id,
            inspection_run_id=result.run_id,
            inspection_assignment_id=result.assignment_id,
            inspection_run_binding_digest=result.run_binding_digest,
            image_size=len(result.image.png),
            preview_size=len(result.image.preview),
        )
        self.catalog.reserve(asset, actor=principal.subject_id)
        revision = 1
        try:
            paths = self.storage.write(asset, result.image, checkpoint=checkpoint)
            checkpoint()
            revision = self.catalog.transition(
                principal.tenant_id,
                project,
                asset.image.artifact_id,
                actor=principal.subject_id,
                expected_revision=revision,
                state="active",
                stored_paths=paths,
            )
            checkpoint()
            return asset
        except Exception:
            # Keep the durable reservation/tombstone: interrupted writes never
            # disappear into an untracked, generally visible artifact state.
            try:
                self.catalog.transition(
                    principal.tenant_id,
                    project,
                    asset.image.artifact_id,
                    actor=principal.subject_id,
                    expected_revision=revision,
                    state="revoked",
                )
            except ValueError:
                pass  # A competing terminal transition already denies use.
            raise

    def read_image(self, principal, project, artifact_id, *, purpose="preview"):
        if purpose not in ("preview", "publish"):
            raise ValueError("persona_asset_purpose_invalid")
        self.policy.require_lookup(principal, project, artifact_id, purpose)
        asset, revision = self.catalog.get_active(principal.tenant_id, project, artifact_id)

        def checkpoint():
            self.policy.require_asset(principal, asset, purpose)
            if self.catalog.get_active(principal.tenant_id, project, artifact_id) != (asset, revision):
                raise ValueError("persona_asset_changed")

        content = self.storage.read(asset, preview=purpose == "preview", checkpoint=checkpoint)
        return content

    def revoke(self, principal, project, artifact_id, *, expected_revision):
        self.policy.require_revoke(principal, project, artifact_id)
        return self.catalog.transition(
            principal.tenant_id,
            project,
            artifact_id,
            actor=principal.subject_id,
            expected_revision=expected_revision,
            state="revoked",
        )
