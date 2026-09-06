"""Closed two-part formats for the shared private persona SQL catalog."""

from dataclasses import dataclass

from agent.models.persona_assets import PersonaImageAsset
from agent.models.persona_media import MediaAssetRef
from agent.models.persona_video_assets import PersonaVideoAsset


@dataclass(frozen=True)
class PersonaCatalogPart:
    reference: MediaAssetRef
    kind: str
    size: int
    filename: str
    media_type: str


class PersonaImageCatalogFormat:
    def primary(self, asset):
        return asset.image

    def decode(self, payload):
        return PersonaImageAsset.model_validate_json(payload)

    def parts(self, asset):
        return (
            PersonaCatalogPart(asset.image, "persona_media_image", asset.image_size, "image.png", "image/png"),
            PersonaCatalogPart(asset.preview, "persona_media_preview", asset.preview_size, "image.png", "image/png"),
        )


class PersonaVideoCatalogFormat:
    def primary(self, asset):
        return asset.video

    def decode(self, payload):
        return PersonaVideoAsset.model_validate_json(payload)

    def parts(self, asset):
        return (
            PersonaCatalogPart(asset.video, "persona_media_video", asset.video_size, "clip.mp4", "video/mp4"),
            PersonaCatalogPart(asset.preview, "persona_media_preview", asset.preview_size, "preview.png", "image/png"),
        )
