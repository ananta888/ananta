"""Shared immutable profile pinning; each adapter declares its actual outputs."""

from dataclasses import dataclass

from agent.models.persona_media import PersonaProfileSelection
from agent.services.meet_contract import MeetError
from agent.services.project_access_authority import ProjectAccessError


@dataclass(frozen=True)
class MeetImageProfileBinding:
    profiles: object
    images: object
    required_outputs: tuple[str, ...]

    def __post_init__(self):
        if (
            not isinstance(self.required_outputs, tuple)
            or "image" not in self.required_outputs
            or any(kind not in ("image", "voice", "video") for kind in self.required_outputs)
            or len(set(self.required_outputs)) != len(self.required_outputs)
        ):
            raise ValueError("meet_persona_profile_outputs_invalid")

    def prepare(self, principal, project, selection, purpose):
        try:
            selection = PersonaProfileSelection.model_validate(selection)
            reference = self.profiles.for_execution(
                principal, project, selection, required_outputs=self.required_outputs
            )
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
            selected = self.profiles.for_execution(
                principal,
                project,
                PersonaProfileSelection.model_validate(binding),
                required_outputs=self.required_outputs,
            )
            if selected != reference:
                raise PermissionError("persona_execution_reference_changed")
        except (ValueError, PermissionError, ProjectAccessError):
            raise MeetError("meet_persona_profile_denied_or_changed", 403) from None
