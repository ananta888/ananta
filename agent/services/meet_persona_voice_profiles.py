"""Pin an explicit voice independently of image/video outputs; never grants publication."""

from agent.models.persona_media import MediaAssetRef, PersonaProfileSelection
from agent.services.meet_contract import MeetError
from agent.services.project_access_authority import ProjectAccessError


class MeetPersonaVoiceProfiles:
    def __init__(self, profiles, voices):
        self.profiles, self.voices = profiles, voices

    def select(self, principal, project, selection, purpose):
        try:
            selected = PersonaProfileSelection.model_validate(selection)
            reference = self.profiles.for_voice_execution(principal, project, selected)
            binding = selected.model_dump(mode="json")
            self.require_current(principal, project, binding, reference, purpose)
            return reference, binding
        except (ValueError, PermissionError, ProjectAccessError):
            raise MeetError("meet_persona_voice_profile_denied_or_changed", 403) from None

    def require_current(self, principal, project, binding, reference, purpose):
        try:
            selected = PersonaProfileSelection.model_validate(binding)
            reference = MediaAssetRef.model_validate(reference).model_dump(mode="json")
            if self.profiles.for_voice_execution(principal, project, selected) != reference:
                raise PermissionError("persona_execution_reference_changed")
            self.voices.require_current(principal, project, reference, purpose)
            if self.profiles.for_voice_execution(principal, project, selected) != reference:
                raise PermissionError("persona_execution_reference_changed")
        except (ValueError, PermissionError, ProjectAccessError):
            raise MeetError("meet_persona_voice_profile_denied_or_changed", 403) from None

    def prepare(self, principal, project, selection, purpose, *, max_seconds=40):
        reference, binding = self.select(principal, project, selection, purpose)
        try:
            value = self.voices.prepare(principal, project, reference, purpose, max_seconds=max_seconds)
            if value["reference"] != reference:
                raise PermissionError("persona_execution_reference_changed")
            self.require_current(principal, project, binding, reference, purpose)
            return value, binding
        except (ValueError, PermissionError, ProjectAccessError):
            raise MeetError("meet_persona_voice_profile_denied_or_changed", 403) from None
