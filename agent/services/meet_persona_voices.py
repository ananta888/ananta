"""Bridge private voice descriptors to bounded speech profiles under current Hub policy."""

from agent.models.persona_media import MediaAssetRef
from agent.services.meet_contract import MeetError
from agent.services.project_access_authority import ProjectAccessError
from ananta_contracts.meet_speech import speech_profile
from ananta_contracts.persona_voice import inspect_voice_descriptor


class MeetPersonaVoices:
    def __init__(self, assets):
        self.assets = assets

    def _current(self, principal, project, reference, purpose):
        if purpose not in ("preview", "publish"):
            raise PermissionError("meet_persona_voice_purpose_denied")
        reference = MediaAssetRef.model_validate(reference)
        if (reference.tenant_id, reference.project_id, reference.kind) != (principal.tenant_id, project, "voice"):
            raise PermissionError("meet_persona_voice_scope_denied")
        self.assets.policy.require_media_kind("voice")
        self.assets.policy.require_lookup(principal, project, reference.artifact_id, purpose)
        asset, revision = self.assets.catalog.get_active(principal.tenant_id, project, reference.artifact_id)
        if asset.voice != reference:
            raise PermissionError("meet_persona_voice_revision_changed")
        self.assets.policy.require_asset(principal, asset, purpose)
        if self.assets.catalog.get_active(principal.tenant_id, project, reference.artifact_id) != (asset, revision):
            raise PermissionError("meet_persona_voice_revision_changed")
        return asset

    def require_current(self, principal, project, reference, purpose):
        try:
            self._current(principal, project, reference, purpose)
        except (ValueError, PermissionError, ProjectAccessError):
            raise MeetError("meet_persona_voice_denied_or_unavailable", 403) from None

    def prepare(self, principal, project, reference, purpose, *, max_seconds=40):
        try:
            asset = self._current(principal, project, reference, purpose)
            reference = asset.voice.model_dump(mode="json")

            def checkpoint():
                self.require_current(principal, project, reference, purpose)

            content = self.assets.storage.read(asset, preview=purpose == "preview", checkpoint=checkpoint)
            inspected = inspect_voice_descriptor(content)
            if inspected.source_sha256 != asset.voice.sha256 or inspected.voice_id != asset.voice_id:
                raise PermissionError("meet_persona_voice_descriptor_changed")
            result = {
                "reference": asset.voice.model_dump(mode="json"),
                "speech_profile": speech_profile(voice_id=inspected.voice_id, max_seconds=max_seconds),
            }
            checkpoint()
            return result
        except (ValueError, PermissionError, ProjectAccessError):
            raise MeetError("meet_persona_voice_denied_or_unavailable", 403) from None
