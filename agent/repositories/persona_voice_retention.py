"""Voice-only retention ledger; no reuse of image/video grants or claims."""

from agent.repositories.persona_retention_store import SqlPersonaRetentionStore
from agent.repositories.persona_retention_tables import persona_retention_tables

_metadata, retention, events = persona_retention_tables("voice")


def create_voice_retention_store(engine):
    return SqlPersonaRetentionStore(engine, metadata=_metadata, retention=retention, events=events)
