"""Silent avatar clip resolution; no dependency on or authorization for speech."""

from agent.models.persona_media import PersonaProfileSelection
from agent.services.meet_contract import MeetError
from agent.services.project_access_authority import ProjectAccessError


class MeetAvatarVideoProfiles:
    def __init__(self, profiles, videos):
        self.profiles, self.videos = profiles, videos

    def _reference(self, principal, project, selection):
        return self.profiles.for_avatar_video_execution(
            principal, project, PersonaProfileSelection.model_validate(selection)
        )

    def select(self, principal, project, selection, purpose):
        try:
            binding = PersonaProfileSelection.model_validate(selection).model_dump(mode="json")
            reference = self._reference(principal, project, binding)
            self.require_current(principal, project, binding, reference, purpose)
            return reference, binding
        except (ValueError, PermissionError, ProjectAccessError):
            raise MeetError("meet_avatar_video_profile_denied_or_changed", 403) from None

    def require_current(self, principal, project, binding, reference, purpose):
        try:
            if self._reference(principal, project, binding) != reference:
                raise PermissionError()
            self.videos.require_current(principal, project, reference, purpose)
            if self._reference(principal, project, binding) != reference:
                raise PermissionError()
        except (ValueError, PermissionError, ProjectAccessError):
            raise MeetError("meet_avatar_video_profile_denied_or_changed", 403) from None

    def prepare(self, principal, project, selection, purpose, *, repeat_mode):
        reference, binding = self.select(principal, project, selection, purpose)
        video = self.videos.prepare(principal, project, reference["artifact_id"], purpose, repeat_mode=repeat_mode)
        if video["reference"] != reference:
            raise MeetError("meet_avatar_video_profile_denied_or_changed", 403)
        self.require_current(principal, project, binding, reference, purpose)
        return video, binding
