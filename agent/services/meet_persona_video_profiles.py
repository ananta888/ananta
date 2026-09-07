"""Bind clip execution to one current Hub profile without granting publication."""

from agent.models.persona_media import PersonaProfileSelection
from agent.services.meet_contract import MeetError
from agent.services.project_access_authority import ProjectAccessError


class MeetPersonaVideoProfiles:
    # for_video_execution owns the closed voice/video output set. A stored clip
    # does not consume the separately configured image output.

    def __init__(self, profiles, videos):
        self.profiles, self.videos = profiles, videos

    def prepare(self, principal, project, selection, purpose, *, repeat_mode):
        try:
            selected = PersonaProfileSelection.model_validate(selection)
            reference = self.profiles.for_video_execution(principal, project, selected)
            assignment = self.videos.prepare(
                principal, project, reference["artifact_id"], purpose, repeat_mode=repeat_mode
            )
            if assignment["reference"] != reference:
                raise PermissionError("persona_execution_reference_changed")
            binding = selected.model_dump(mode="json")
            self.require_current(principal, project, binding, reference)
            return assignment, binding
        except (ValueError, PermissionError, ProjectAccessError):
            raise MeetError("meet_persona_video_profile_denied_or_changed", 403) from None

    def require_current(self, principal, project, binding, reference):
        try:
            current = self.profiles.for_video_execution(
                principal, project, PersonaProfileSelection.model_validate(binding)
            )
            if current != reference:
                raise PermissionError("persona_execution_reference_changed")
        except (ValueError, PermissionError, ProjectAccessError):
            raise MeetError("meet_persona_video_profile_denied_or_changed", 403) from None
