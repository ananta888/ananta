"""Voice-only policy versions; never reuses image, video or ASR permission rows."""

from agent.models.persona_asset_policy import PersonaVoicePolicy
from agent.repositories.persona_policy_tables import persona_policy_tables
from agent.repositories.persona_source_policies import SqlPersonaSourcePolicies

_metadata, heads, versions = persona_policy_tables("voice")


def create_voice_policy_repository(engine):
    return SqlPersonaSourcePolicies(
        engine, metadata=_metadata, heads=heads, versions=versions, policy_type=PersonaVoicePolicy
    )
