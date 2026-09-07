"""Exact retired descriptor erasure; never removes models, keys or other assets."""

from agent.models.persona_voice_assets import PersonaVoiceAsset
from agent.services.persona_asset_erasure import PersonaAssetErasureService
from agent.services.persona_file_erasure_store import PersonaFileErasureStore


def voice_parts(asset):
    asset = PersonaVoiceAsset.model_validate_json(asset.model_dump_json())
    return ((asset.voice, asset.descriptor_size),)


def create_voice_erasure_service(*, policy, catalog, base_dir):
    policy.require_media_kind("voice")
    return PersonaAssetErasureService(
        policy=policy, catalog=catalog, eraser=PersonaFileErasureStore(base_dir, profile="voice"), parts=voice_parts
    )
