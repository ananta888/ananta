"""Explicit video-policy storage; image policy rows can never be a fallback."""

from agent.models.persona_asset_policy import PersonaVideoPolicy
from agent.repositories.persona_policy_tables import persona_policy_tables
from agent.repositories.persona_source_policies import SqlPersonaSourcePolicies

_metadata, heads, versions = persona_policy_tables("video")


def create_video_policy_repository(engine):
    return SqlPersonaSourcePolicies(
        engine,
        metadata=_metadata,
        heads=heads,
        versions=versions,
        policy_type=PersonaVideoPolicy,
    )
