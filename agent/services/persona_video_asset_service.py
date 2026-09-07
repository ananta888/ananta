"""Video-only composition facade; decoding stays delegated to Hub tasks."""

from agent.services.persona_asset_admission_formats import PersonaVideoAdmissionFormat
from agent.services.persona_asset_lifecycle import PersonaAssetLifecycle


class PersonaVideoAssetService:
    def __init__(self, *, policy, tasks, catalog, storage):
        self.policy, self.tasks, self.catalog, self.storage = policy, tasks, catalog, storage

    def _lifecycle(self):
        return PersonaAssetLifecycle(
            policy=self.policy,
            tasks=self.tasks,
            catalog=self.catalog,
            storage=self.storage,
            format=PersonaVideoAdmissionFormat(),
        )

    def admit_video(
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

    def read_video(self, principal, project, artifact_id, *, purpose="preview"):
        return self._lifecycle().read(principal, project, artifact_id, purpose=purpose)

    def revoke(self, principal, project, artifact_id, *, expected_revision):
        return self._lifecycle().revoke(principal, project, artifact_id, expected_revision=expected_revision)
