"""Video-specific fixed file profiles composed with the existing Hub lifecycle."""

from agent.services.persona_asset_erasure import PersonaAssetErasureService
from agent.services.persona_file_erasure_store import PersonaFileErasureStore


class PersonaVideoErasureStore:
    def __init__(self, base_dir):
        self._video = PersonaFileErasureStore(base_dir, profile="video")
        self._preview = PersonaFileErasureStore(base_dir, profile="video_preview")

    def erase(self, reference, expected_size, *, checkpoint):
        if reference.kind not in ("video", "image"):
            raise ValueError("persona_video_erasure_kind_invalid")
        store = self._video if reference.kind == "video" else self._preview
        return store.erase(reference, expected_size, checkpoint=checkpoint)


def video_parts(asset):
    return ((asset.video, asset.video_size), (asset.preview, asset.preview_size))


def create_video_erasure_service(*, policy, catalog, base_dir):
    return PersonaAssetErasureService(
        policy=policy,
        catalog=catalog,
        eraser=PersonaVideoErasureStore(base_dir),
        parts=video_parts,
    )
