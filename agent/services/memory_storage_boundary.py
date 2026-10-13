"""Small namespace/audit boundary shared by legacy SQL tree and file adapters."""

import hashlib
import json

from agent.services.memory_access_context import current_memory_access
from agent.services.memory_policy_gateway import MemoryAccessError
from ananta_contracts.memory_access import MemoryScope, MemorySecurityMetadata, MemorySensitivity, MemoryTarget


class MemoryStorageBoundary:
    def __init__(self, channel_id):
        self.channel_id = channel_id

    def authorize(self, operation, *, scope=MemoryScope.task, sensitivity="internal"):
        context = current_memory_access()
        return context.gateway.authorize_current(
            actor=context.actor, run_id=context.run_id, channel_id=self.channel_id, operation=operation,
            security=MemorySecurityMetadata(scope=scope, sensitivity=MemorySensitivity(sensitivity)),
        )

    def recheck(self, previous):
        decision = self.authorize(previous.operation, scope=previous.target.security.scope,
                                  sensitivity=previous.target.security.sensitivity.value)
        if decision.policy_digest != previous.policy_digest or decision.target != previous.target:
            raise MemoryAccessError("memory_policy_changed")

    def promote_to(self, destination, *, source_scope=MemoryScope.task, sensitivity="internal"):
        """Require every Hub-approved intermediate transition for a derived summary."""
        context = current_memory_access()
        chain = (MemoryScope.task, MemoryScope.goal, MemoryScope.workspace, MemoryScope.global_)
        if source_scope not in chain or destination.target.security.scope not in chain:
            raise MemoryAccessError("memory_promotion_transition_invalid")
        start, end = chain.index(source_scope), chain.index(destination.target.security.scope)
        if start > end:
            raise MemoryAccessError("memory_promotion_transition_invalid")
        decisions = []
        for source, target in zip(chain[start:end], chain[start + 1:end + 1]):
            namespace = destination.target.namespace
            source_target = MemoryTarget(self.channel_id, namespace, MemorySecurityMetadata(
                scope=source, sensitivity=MemorySensitivity(sensitivity),
            ))
            dest_target = MemoryTarget(self.channel_id, namespace, MemorySecurityMetadata(
                scope=target, sensitivity=MemorySensitivity(sensitivity),
            ))
            decisions.extend(context.gateway.authorize_promote(
                actor=context.actor, run_id=context.run_id, target=source_target, destination=dest_target,
            ))
        if any(item.policy_digest != destination.policy_digest for item in decisions):
            raise MemoryAccessError("memory_policy_changed")

    @staticmethod
    def metadata(decision, supplied=None):
        from agent.services.result_memory_access import ResultMemoryAccess

        metadata = dict(supplied or {})
        metadata.update(ResultMemoryAccess.metadata(decision))
        return metadata

    @staticmethod
    def scoped_id(decision, original_id):
        key = decision.target.namespace.key(decision.target.security.scope)
        encoded = json.dumps([key, original_id], separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()[:32]

    @staticmethod
    def constrain(statement, metadata, decision):
        target = decision.target
        statement = statement.where(
            metadata["memory_security_schema"].as_string() == "ananta.memory-security.v1",
            metadata["memory_security"]["scope"].as_string() == target.security.scope.value,
        )
        for field, value in target.namespace.bindings(target.security.scope).items():
            column = metadata["memory_namespace"][field].as_string()
            statement = statement.where(column.is_(None) if value is None else column == value)
        levels = tuple(MemorySensitivity)
        allowed = [item.value for item in levels[:levels.index(target.security.sensitivity) + 1]]
        return statement.where(metadata["memory_security"]["sensitivity"].as_string().in_(allowed))

    @staticmethod
    def node_scope(node_type):
        scopes = {"source": MemoryScope.task, "topic": MemoryScope.workspace, "global": MemoryScope.global_}
        if node_type not in scopes:
            raise MemoryAccessError("memory_tree_scope_invalid")
        return scopes[node_type]
