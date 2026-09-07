"""Hub-owned passive voice selection; never model bytes, a grant or worker policy."""

from typing import Literal

from pydantic import model_validator

from agent.models.persona_media import ClosedModel, MediaAssetRef, PersonaProfileSelection


class ConfiguredVoiceSelection(ClosedModel):
    mode: Literal["configured-piper-v1"]


class ProfileVoiceSelection(ClosedModel):
    mode: Literal["persona-voice-v1"]
    reference: MediaAssetRef
    profile: PersonaProfileSelection

    @model_validator(mode="after")
    def require_voice_descriptor(self):
        if self.reference.kind != "voice" or self.reference.revision != 1:
            raise ValueError("meet_voice_reference_invalid")
        return self


def parse_voice_selection(value, tenant, project):
    model = (
        ConfiguredVoiceSelection
        if isinstance(value, dict) and value.get("mode") == "configured-piper-v1"
        else ProfileVoiceSelection
    )
    result = model.model_validate(value)
    if isinstance(result, ProfileVoiceSelection) and (result.reference.tenant_id, result.reference.project_id) != (
        tenant,
        project,
    ):
        raise ValueError("meet_voice_scope_invalid")
    return result.model_dump(mode="json")
