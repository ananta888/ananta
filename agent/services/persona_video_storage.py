"""Exact immutable MP4/PNG storage, with authority checks around every operation."""

import hashlib

from agent.models.persona_video_assets import PersonaVideoAsset
from agent.services.persona_asset_storage import ImmutablePersonaStorePort
from ananta_contracts.persona_video import SanitizedPersonaVideo, decode_video, encode_video


class PersonaVideoStorage:
    def __init__(self, store: ImmutablePersonaStorePort):
        self.store = store

    def write(self, asset: PersonaVideoAsset, inspected: SanitizedPersonaVideo, *, checkpoint):
        checkpoint()
        inspected = decode_video(encode_video(inspected), asset.admission.source_sha256)
        if (
            inspected.video_sha256 != asset.video.sha256
            or inspected.preview_sha256 != asset.preview.sha256
            or len(inspected.video) != asset.video_size
            or len(inspected.preview) != asset.preview_size
            or inspected.frames != asset.frames
        ):
            raise ValueError("persona_video_asset_inspection_mismatch")
        paths = {}
        for reference, filename, mime, content in (
            (asset.video, "clip.mp4", "video/mp4", inspected.video),
            (asset.preview, "preview.png", "image/png", inspected.preview),
        ):
            checkpoint()
            stored = self.store.store_immutable_bytes(
                artifact_id=reference.artifact_id,
                version_number=1,
                filename=filename,
                content=content,
                expected_sha256=reference.sha256,
                media_type=mime,
            )
            if stored["sha256"] != reference.sha256 or stored["size_bytes"] != len(content):
                raise ValueError("persona_video_asset_storage_mismatch")
            paths[reference.artifact_id] = stored["storage_path"]
            checkpoint()
        return paths

    def read(self, asset: PersonaVideoAsset, *, preview: bool, checkpoint):
        if type(preview) is not bool:
            raise ValueError("persona_video_asset_preview_flag_invalid")
        checkpoint()
        reference, filename, size = (
            (asset.preview, "preview.png", asset.preview_size)
            if preview
            else (asset.video, "clip.mp4", asset.video_size)
        )
        content = self.store.load_immutable_bytes(
            artifact_id=reference.artifact_id,
            version_number=1,
            filename=filename,
            expected_sha256=reference.sha256,
            expected_size=size,
        )
        checkpoint()
        if (
            type(content) is not bytes
            or len(content) != size
            or hashlib.sha256(content).hexdigest() != reference.sha256
        ):
            raise ValueError("persona_video_asset_storage_mismatch")
        return content
