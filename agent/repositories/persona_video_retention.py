"""Video-only retention ledger; image grants and claims are never consumed."""

from agent.repositories.persona_retention_store import SqlPersonaRetentionStore
from agent.repositories.persona_retention_tables import persona_retention_tables

_metadata, retention, events = persona_retention_tables("video")


def create_video_retention_store(engine):
    return SqlPersonaRetentionStore(engine, metadata=_metadata, retention=retention, events=events)
