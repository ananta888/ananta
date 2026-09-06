"""Meet-specific selection adapter; a persona image is never a room grant."""

import base64

from agent.models.persona_media import MediaAssetRef
from agent.services.meet_contract import MeetError
from agent.services.project_access_authority import ProjectAccessError


class MeetPersonaImages:
    def __init__(self, assets):
        self.assets = assets

    def require_current(self, principal, project, reference, purpose):
        try:
            self._require_current(principal, project, reference, purpose)
        except (ValueError, PermissionError, ProjectAccessError):
            raise MeetError("meet_persona_image_denied_or_unavailable", 403) from None

    def _require_current(self, principal, project, reference, purpose):
        if purpose not in ("preview", "publish"):
            raise PermissionError("meet_persona_purpose_denied")
        reference = MediaAssetRef.model_validate(reference)
        if (reference.tenant_id, reference.project_id) != (principal.tenant_id, project):
            raise PermissionError("meet_persona_scope_denied")
        self.assets.policy.require_lookup(principal, project, reference.artifact_id, purpose)
        asset, _ = self.assets.catalog.get_active(principal.tenant_id, project, reference.artifact_id)
        if asset.image != reference:
            raise PermissionError("meet_persona_revision_changed")
        self.assets.policy.require_asset(principal, asset, purpose)

    def prepare(self, principal, project, artifact_id, purpose):
        try:
            return self._prepare(principal, project, artifact_id, purpose)
        except (ValueError, PermissionError, ProjectAccessError):
            raise MeetError("meet_persona_image_denied_or_unavailable", 403) from None

    def _prepare(self, principal, project, artifact_id, purpose):
        self.assets.policy.require_lookup(principal, project, artifact_id, purpose)
        asset, _ = self.assets.catalog.get_active(principal.tenant_id, project, artifact_id)
        reference = asset.image.model_dump(mode="json")
        self.require_current(principal, project, reference, purpose)

        # Use the full normalized image, even for local video preview. The
        # chosen authorization purpose remains preview; it is not publication.
        def checkpoint():
            self.require_current(principal, project, reference, purpose)

        content = self.assets.storage.read(asset, preview=False, checkpoint=checkpoint)
        return {"reference": reference, "png": base64.b64encode(content).decode()}
