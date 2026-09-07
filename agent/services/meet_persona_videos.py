"""Authorize exact private clips for a Meet turn, independently of room grants."""

from agent.models.persona_media import MediaAssetRef
from agent.services.meet_contract import MeetError
from agent.services.project_access_authority import ProjectAccessError
from ananta_contracts.meet_persona_video import decode_assignment, validate_reference
from ananta_contracts.persona_video import SanitizedPersonaVideo, encode_video


class MeetPersonaVideos:
    def __init__(self, assets):
        self.assets = assets

    def _current(self, principal, project, reference, purpose):
        if purpose not in ("preview", "publish"):
            raise PermissionError("meet_persona_purpose_denied")
        reference = MediaAssetRef.model_validate(validate_reference(reference))
        if (reference.tenant_id, reference.project_id) != (principal.tenant_id, project):
            raise PermissionError("meet_persona_scope_denied")
        self.assets.policy.require_media_kind("video")
        self.assets.policy.require_lookup(principal, project, reference.artifact_id, purpose)
        current = self.assets.catalog.get_active(principal.tenant_id, project, reference.artifact_id)
        asset, _revision = current
        if asset.video != reference:
            raise PermissionError("meet_persona_revision_changed")
        self.assets.policy.require_asset(principal, asset, purpose)
        if self.assets.catalog.get_active(principal.tenant_id, project, reference.artifact_id) != current:
            raise PermissionError("meet_persona_video_changed")
        return current

    def require_current(self, principal, project, reference, purpose):
        try:
            self._current(principal, project, reference, purpose)
        except (ValueError, PermissionError, ProjectAccessError):
            raise MeetError("meet_persona_video_denied_or_unavailable", 403) from None

    def prepare(self, principal, project, artifact_id, purpose, *, repeat_mode):
        try:
            return self._prepare(principal, project, artifact_id, purpose, repeat_mode=repeat_mode)
        except (ValueError, PermissionError, ProjectAccessError):
            raise MeetError("meet_persona_video_denied_or_unavailable", 403) from None

    def _prepare(self, principal, project, artifact_id, purpose, *, repeat_mode):
        if purpose not in ("preview", "publish") or repeat_mode not in ("loop", "hold_last"):
            raise PermissionError("meet_persona_video_selection_invalid")
        self.assets.policy.require_media_kind("video")
        self.assets.policy.require_lookup(principal, project, artifact_id, purpose)
        original = self.assets.catalog.get_active(principal.tenant_id, project, artifact_id)
        asset, _revision = original
        reference = asset.video.model_dump(mode="json")

        def checkpoint():
            if self._current(principal, project, reference, purpose) != original:
                raise PermissionError("meet_persona_video_changed")

        checkpoint()
        video = self.assets.storage.read(asset, preview=False, checkpoint=checkpoint)
        preview = self.assets.storage.read(asset, preview=True, checkpoint=checkpoint)
        inspected = SanitizedPersonaVideo(
            asset.admission.source_sha256, asset.video.sha256, asset.preview.sha256, asset.frames, video, preview
        )
        assignment = {
            "reference": reference,
            "clip": encode_video(inspected),
            "origin_kind": asset.admission.origin_kind,
            "repeat_mode": repeat_mode,
        }
        decode_assignment(assignment, tenant_id=principal.tenant_id, project_id=project)
        checkpoint()
        return assignment
