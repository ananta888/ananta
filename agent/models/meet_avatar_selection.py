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


class VideoAvatarSelection(ClosedModel):
    mode: Literal["persona-video-v1"]
    reference: MediaAssetRef
    profile: PersonaProfileSelection
    repeat_mode: Literal["loop", "hold_last"]

    @model_validator(mode="after")
    def require_normalized_video(self):
        if self.reference.kind != "video" or self.reference.revision != 1:
            raise ValueError("meet_avatar_video_reference_invalid")
        return self


def parse_avatar_selection(value, tenant, project, *, videos=False):
    mode = value.get("mode") if isinstance(value, dict) else None
    if type(mode) is not str:
        raise ValueError("meet_avatar_selection_invalid")
    model = {"neutral-ai-v1": NeutralAvatarSelection, "persona-image-v1": ImageAvatarSelection}.get(mode)
    if videos is True and mode == "persona-video-v1":
        model = VideoAvatarSelection
    if model is None:
        raise ValueError("meet_avatar_selection_invalid")
    result = model.model_validate(value)
    if isinstance(result, (ImageAvatarSelection, VideoAvatarSelection)) and (
        result.reference.tenant_id,
        result.reference.project_id,
    ) != (
        tenant,
        project,
    ):
        raise ValueError("meet_avatar_image_scope_invalid")
    return result.model_dump(mode="json")
