"""Compatible immutable image-policy repository facade."""

from agent.models.persona_asset_policy import PersonaImagePolicy
from agent.repositories.persona_policy_tables import persona_policy_tables
from agent.repositories.persona_source_policies import SqlPersonaSourcePolicies

_metadata, heads, versions = persona_policy_tables("image")


class SqlPersonaImagePolicies:
    def __init__(self, engine):
        self.engine = engine
        self._policies = SqlPersonaSourcePolicies(
            engine,
            metadata=_metadata,
            heads=heads,
            versions=versions,
            policy_type=PersonaImagePolicy,
        )

    def initialize(self):
        return self._policies.initialize()

    def install(self, policy, *, expected_revision, actor):
        return self._policies.install(policy, expected_revision=expected_revision, actor=actor)

    def for_source(self, tenant, project, source_id):
        return self._policies.for_source(tenant, project, source_id)

    def revoke(self, tenant, project, source_id, *, expected_revision, actor):
        return self._policies.revoke(tenant, project, source_id, expected_revision=expected_revision, actor=actor)
