"""One immutable descriptor, no audio preview, with authority checks around storage."""

from agent.models.persona_voice_assets import PersonaVoiceAsset
from agent.services.persona_asset_storage import ImmutablePersonaStorePort
from agent.services.persona_voice_inspection import voice_receipt
from ananta_contracts.persona_voice import MEDIA_TYPE, inspect_voice_descriptor


class PersonaVoiceStorage:
    def __init__(self, store: ImmutablePersonaStorePort):
        self.store = store

    def write(self, asset, inspected, *, checkpoint):
        checkpoint()
        asset = PersonaVoiceAsset.model_validate_json(asset.model_dump_json())
        voice_receipt(inspected, asset.admission.source_sha256)
        if inspected.voice_id != asset.voice_id or len(inspected.descriptor) != asset.descriptor_size:
            raise ValueError("persona_voice_asset_inspection_mismatch")
        checkpoint()
        stored = self.store.store_immutable_bytes(
            artifact_id=asset.voice.artifact_id,
            version_number=1,
            filename="voice.json",
            content=inspected.descriptor,
            expected_sha256=asset.voice.sha256,
            media_type=MEDIA_TYPE,
        )
        checkpoint()
        if stored["sha256"] != asset.voice.sha256 or stored["size_bytes"] != asset.descriptor_size:
            raise ValueError("persona_voice_asset_storage_mismatch")
        return {asset.voice.artifact_id: stored["storage_path"]}

    def read(self, asset, *, preview, checkpoint):
        # Both purposes return metadata, never synthesized preview PCM. Their
        # separate authorizations are owned by PersonaAssetLifecycle.
        if type(preview) is not bool:
            raise ValueError("persona_voice_asset_preview_flag_invalid")
        checkpoint()
        asset = PersonaVoiceAsset.model_validate_json(asset.model_dump_json())
        content = self.store.load_immutable_bytes(
            artifact_id=asset.voice.artifact_id,
            version_number=1,
            filename="voice.json",
            expected_sha256=asset.voice.sha256,
            expected_size=asset.descriptor_size,
        )
        checkpoint()
        inspected = inspect_voice_descriptor(content)
        if inspected.source_sha256 != asset.voice.sha256 or inspected.voice_id != asset.voice_id:
            raise ValueError("persona_voice_asset_storage_mismatch")
        return content
