"""Private video catalog composition; never shares image policy or asset rows."""

from agent.repositories.persona_asset_catalog import SqlPersonaAssetCatalog
from agent.repositories.persona_asset_formats import PersonaVideoCatalogFormat
from agent.repositories.persona_asset_tables import persona_asset_tables

_metadata, assets, events = persona_asset_tables("video")


def create_video_asset_catalog(engine):
    return SqlPersonaAssetCatalog(
        engine,
        metadata=_metadata,
        assets=assets,
        events=events,
        format=PersonaVideoCatalogFormat(),
    )
