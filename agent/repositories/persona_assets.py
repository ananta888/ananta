"""Compatible image-catalog facade over shared fenced SQL persistence."""

from agent.repositories.persona_asset_catalog import SqlPersonaAssetCatalog
from agent.repositories.persona_asset_formats import PersonaImageCatalogFormat
from agent.repositories.persona_asset_tables import persona_asset_tables

_metadata, assets, events = persona_asset_tables("image")


class SqlPersonaAssets:
    def __init__(self, engine):
        self.engine = engine
        self._catalog = SqlPersonaAssetCatalog(
            engine,
            metadata=_metadata,
            assets=assets,
            events=events,
            format=PersonaImageCatalogFormat(),
        )

    def initialize(self):
        return self._catalog.initialize()

    def scan_active_ids(self, tenant, project, *, after, limit):
        return self._catalog.scan_active_ids(tenant, project, after=after, limit=limit)

    def reserve(self, asset, *, actor):
        return self._catalog.reserve(asset, actor=actor)

    def get_active(self, tenant, project, artifact):
        return self._catalog.get_active(tenant, project, artifact)

    def get_retired(self, tenant, project, artifact):
        return self._catalog.get_retired(tenant, project, artifact)

    def storage_guard(self, tenant, project, artifact, *, expected_revision, state):
        return self._catalog.storage_guard(tenant, project, artifact, expected_revision=expected_revision, state=state)

    def transition(self, tenant, project, artifact, *, expected_revision, state, actor, stored_paths=None):
        return self._catalog.transition(
            tenant,
            project,
            artifact,
            expected_revision=expected_revision,
            state=state,
            actor=actor,
            stored_paths=stored_paths,
        )
