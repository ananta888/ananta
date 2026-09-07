"""Content-free Hub avatar selection; never a grant or a Worker policy input."""

from typing import Literal

from pydantic import model_validator

from agent.models.persona_media import ClosedModel, MediaAssetRef, PersonaProfileSelection


class NeutralAvatarSelection(ClosedModel):
    mode: Literal["neutral-ai-v1"]


class ImageAvatarSelection(ClosedModel):
    mode: Literal["persona-image-v1"]
    reference: MediaAssetRef
    profile: PersonaProfileSelection

    @model_validator(mode="after")
    def require_normalized_image(self):
        if self.reference.kind != "image" or self.reference.revision != 1:
            raise ValueError("meet_avatar_image_reference_invalid")
        return self


def parse_avatar_selection(value, tenant, project):
    model = (
        NeutralAvatarSelection
        if isinstance(value, dict) and value.get("mode") == "neutral-ai-v1"
        else ImageAvatarSelection
    )
    result = model.model_validate(value)
    if isinstance(result, ImageAvatarSelection) and (result.reference.tenant_id, result.reference.project_id) != (
        tenant,
        project,
    ):
        raise ValueError("meet_avatar_image_scope_invalid")
    return result.model_dump(mode="json")
