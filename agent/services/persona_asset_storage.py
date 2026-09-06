"""Private infrastructure adapter around Ananta's immutable artifact store."""

import hashlib
from typing import Protocol

from agent.models.persona_assets import PersonaImageAsset


class InspectedPersonaImagePort(Protocol):
    @property
    def source_sha256(self) -> str: ...
    @property
    def image_sha256(self) -> str: ...
    @property
    def preview_sha256(self) -> str: ...
    @property
    def png(self) -> bytes: ...
    @property
    def preview(self) -> bytes: ...


class ImmutablePersonaStorePort(Protocol):
    def store_immutable_bytes(self, *, artifact_id, version_number, filename, content, expected_sha256, media_type): ...
    def load_immutable_bytes(self, *, artifact_id, version_number, filename, expected_sha256, expected_size): ...


class PersonaAssetStorage:
    def __init__(self, store: ImmutablePersonaStorePort):
        self.store = store

    def write(self, asset: PersonaImageAsset, inspected: InspectedPersonaImagePort, *, checkpoint):
        if (
            asset.source_sha256 != inspected.source_sha256
            or asset.image.sha256 != inspected.image_sha256
            or asset.preview.sha256 != inspected.preview_sha256
            or asset.image_size != len(inspected.png)
            or asset.preview_size != len(inspected.preview)
            or hashlib.sha256(inspected.png).hexdigest() != asset.image.sha256
            or hashlib.sha256(inspected.preview).hexdigest() != asset.preview.sha256
        ):
            raise ValueError("persona_asset_inspection_mismatch")
        paths = {}
        for reference, content in ((asset.image, inspected.png), (asset.preview, inspected.preview)):
            checkpoint()
            stored = self.store.store_immutable_bytes(
                artifact_id=reference.artifact_id,
                version_number=1,
                filename="image.png",
                content=content,
                expected_sha256=reference.sha256,
                media_type="image/png",
            )
            if stored["sha256"] != reference.sha256 or stored["size_bytes"] != len(content):
                raise ValueError("persona_asset_storage_mismatch")
            paths[reference.artifact_id] = stored["storage_path"]
        checkpoint()
        return paths

    def read(self, asset: PersonaImageAsset, *, preview: bool, checkpoint):
        checkpoint()
        reference, size = (asset.preview, asset.preview_size) if preview else (asset.image, asset.image_size)
        result = self.store.load_immutable_bytes(
            artifact_id=reference.artifact_id,
            version_number=1,
            filename="image.png",
            expected_sha256=reference.sha256,
            expected_size=size,
        )
        checkpoint()
        return result
