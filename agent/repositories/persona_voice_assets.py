"""Private one-part voice descriptor catalog; no image/video identity fallback."""

from agent.models.persona_voice_assets import PersonaVoiceAsset
from agent.repositories.persona_asset_catalog import SqlPersonaAssetCatalog
from agent.repositories.persona_asset_formats import PersonaCatalogPart
from agent.repositories.persona_asset_tables import persona_asset_tables
from ananta_contracts.persona_voice import MEDIA_TYPE

_metadata, assets, events = persona_asset_tables("voice")


class PersonaVoiceCatalogFormat:
    def primary(self, asset):
        return asset.voice

    def decode(self, payload):
        return PersonaVoiceAsset.model_validate_json(payload)

    def parts(self, asset):
        return (
            PersonaCatalogPart(asset.voice, "persona_media_voice", asset.descriptor_size, "voice.json", MEDIA_TYPE),
        )


def create_voice_asset_catalog(engine):
    return SqlPersonaAssetCatalog(
        engine, metadata=_metadata, assets=assets, events=events, format=PersonaVoiceCatalogFormat()
    )
