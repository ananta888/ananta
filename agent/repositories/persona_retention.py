"""Compatible image-only retention store over the shared SQL implementation."""

from agent.repositories.persona_retention_store import SqlPersonaRetentionStore
from agent.repositories.persona_retention_store import scope as scope
from agent.repositories.persona_retention_tables import persona_retention_tables

_metadata, retention, events = persona_retention_tables("image")


class SqlPersonaRetention:
    def __init__(self, engine):
        self.engine = engine
        self._store = SqlPersonaRetentionStore(engine, metadata=_metadata, retention=retention, events=events)

    def initialize(self):
        return self._store.initialize()

    def get(self, key):
        return self._store.get(key)

    def install(self, record, *, expected_revision):
        return self._store.install(record, expected_revision=expected_revision)

    def cancel(self, key, *, expected_revision, actor):
        return self._store.cancel(key, expected_revision=expected_revision, actor=actor)

    def due(self, now_ms, *, limit):
        return self._store.due(now_ms, limit=limit)

    def claim(self, observed, now_ms):
        return self._store.claim(observed, now_ms)

    def require_claim(self, record, now_ms):
        return self._store.require_claim(record, now_ms)

    def finish(self, record, state, now_ms):
        return self._store.finish(record, state, now_ms)
