"""Bind a bounded Meet turn to a current, explicitly selected Hub profile."""

from agent.models.persona_media import PersonaProfileSelection
from agent.services.meet_contract import MeetError
from agent.services.project_access_authority import ProjectAccessError


class MeetPersonaProfiles:
    def __init__(self, profiles, images):
        self.profiles, self.images = profiles, images

    def prepare(self, principal, project, selection, purpose):
        try:
            selection = PersonaProfileSelection.model_validate(selection)
            reference = self.profiles.for_execution(principal, project, selection)
            assignment = self.images.prepare(principal, project, reference["artifact_id"], purpose)
            if assignment["reference"] != reference:
                raise PermissionError("persona_execution_reference_changed")
            binding = selection.model_dump(mode="json")
            self.require_current(principal, project, binding, reference)
            return assignment, binding
        except (ValueError, PermissionError, ProjectAccessError):
            raise MeetError("meet_persona_profile_denied_or_changed", 403) from None

    def require_current(self, principal, project, binding, reference):
        try:
            selected = self.profiles.for_execution(principal, project, PersonaProfileSelection.model_validate(binding))
            if selected != reference:
                raise PermissionError("persona_execution_reference_changed")
        except (ValueError, PermissionError, ProjectAccessError):
            raise MeetError("meet_persona_profile_denied_or_changed", 403) from None
