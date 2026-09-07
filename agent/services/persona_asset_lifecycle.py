"""Hub admission/read/revocation lifecycle; small formats own media differences."""

import hashlib
import re

from agent.services.persona_asset_admission_formats import PersonaAssetAdmissionFormat


class PersonaAssetLifecycle:
    def __init__(self, *, policy, tasks, catalog, storage, format: PersonaAssetAdmissionFormat):
        self.policy, self.tasks, self.catalog, self.storage = policy, tasks, catalog, storage
        self.format = format

    def _require_kind(self):
        # Legacy image-only policy ports remain compatible for image use only.
        if self.format.kind != "image":
            require = getattr(self.policy, "require_media_kind", None)
            if not callable(require):
                raise PermissionError(f"persona_{self.format.kind}_policy_kind_required")
            require(self.format.kind)

    def admit(self, principal, project, *, content, media_type, origin_binding, license_binding, consent_binding=None):
        self._require_kind()
        if (
            not isinstance(content, bytes)
            or not 0 < len(content) <= self.format.maximum
            or media_type not in self.format.media_types
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
        inspected = self.format.payload(result)
        if inspected.source_sha256 != source_sha256:
            raise ValueError("persona_asset_inspection_mismatch")

        def checkpoint():
            self._require_kind()
            self.policy.require_current(principal, admission, "store")

        checkpoint()
        asset = self.format.build(admission, result)
        self._store(principal, project, asset, inspected, checkpoint)
        return asset

    def _store(self, principal, project, asset, inspected, checkpoint):
        artifact_id = self.format.primary(asset).artifact_id
        self.catalog.reserve(asset, actor=principal.subject_id)
        revision = 1
        try:
            with self.catalog.storage_guard(
                principal.tenant_id, project, artifact_id, expected_revision=revision, state="pending"
            ):
                paths = self.storage.write(asset, inspected, checkpoint=checkpoint)
            checkpoint()
            revision = self.catalog.transition(
                principal.tenant_id,
                project,
                artifact_id,
                actor=principal.subject_id,
                expected_revision=revision,
                state="active",
                stored_paths=paths,
            )
            checkpoint()
        except Exception:
            # Interrupted writes remain visible to the private retirement ledger,
            # never generally visible orphan artifacts. A competing terminal CAS wins.
            try:
                self.catalog.transition(
                    principal.tenant_id,
                    project,
                    artifact_id,
                    actor=principal.subject_id,
                    expected_revision=revision,
                    state="revoked",
                )
            except ValueError:
                pass
            raise

    def read(self, principal, project, artifact_id, *, purpose="preview"):
        self._require_kind()
        if purpose not in ("preview", "publish"):
            raise ValueError("persona_asset_purpose_invalid")
        self.policy.require_lookup(principal, project, artifact_id, purpose)
        asset, revision = self.catalog.get_active(principal.tenant_id, project, artifact_id)

        def checkpoint():
            self._require_kind()
            self.policy.require_asset(principal, asset, purpose)
            if self.catalog.get_active(principal.tenant_id, project, artifact_id) != (asset, revision):
                raise ValueError("persona_asset_changed")

        return self.storage.read(asset, preview=purpose == "preview", checkpoint=checkpoint)

    def revoke(self, principal, project, artifact_id, *, expected_revision):
        self._require_kind()
        self.policy.require_revoke(principal, project, artifact_id)
        return self.catalog.transition(
            principal.tenant_id,
            project,
            artifact_id,
            actor=principal.subject_id,
            expected_revision=expected_revision,
            state="revoked",
        )
