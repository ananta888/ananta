"""Synthetic Hub-policy doubles; no evidence identities or production approvals."""

import time
from contextlib import contextmanager
from dataclasses import replace

from agent.services.memory_channel_registry import MemoryChannelRegistry
from agent.services.memory_policy_gateway import MemoryPolicyGateway
from ananta_contracts.memory_access import (
    MemoryActor,
    MemoryCapability,
    MemoryNamespace,
    MemoryOperation,
    MemoryRunPolicy,
    MemoryScope,
    MemorySensitivity,
)

ACTOR = MemoryActor("test-tenant", "test-actor")
NAMESPACE = MemoryNamespace("test-tenant", "test-workspace", "test-goal", "test-task", "technical-test-run")


def grant(channel="run-scratchpad", scope=MemoryScope.run, operations=tuple(MemoryOperation), **changes):
    return replace(MemoryCapability(channel, NAMESPACE, scope, operations, MemorySensitivity.internal, 900), **changes)


def policy(capabilities=None, **changes):
    return replace(MemoryRunPolicy(ACTOR, NAMESPACE, "test-policy-revision", 1000,
                                  capabilities=(grant(),) if capabilities is None else capabilities), **changes)


class PolicyFixture:
    def __init__(self, value):
        self.value = value
        self.calls = 0
        self.error = False

    def current(self, *, actor, run_id):
        self.calls += 1
        if self.error:
            raise RuntimeError("private database information")
        return self.value


class AuditFixture:
    def __init__(self):
        self.decisions = []
        self.error = False

    def record(self, decision):
        if self.error:
            raise RuntimeError("private audit backend information")
        self.decisions.append(decision)


def gateway(value=None, *, clock=lambda: 100):
    policies = PolicyFixture(policy() if value is None else value)
    audit = AuditFixture()
    service = MemoryPolicyGateway(
        policies=policies, audit=audit,
        channels=MemoryChannelRegistry(frozenset({"run-scratchpad", "hub-result-memory", "hub-memory-tree"})),
        clock=clock,
    )
    return service, policies, audit


@contextmanager
def synthetic_memory_access(*, task_id="test-task", goal_id="test-goal", channels, scopes,
                            operations=tuple(MemoryOperation), sensitivity=MemorySensitivity.security_sensitive):
    """Explicit test-only preauthorization; never installed into application defaults."""
    from agent.services.memory_access_context import MemoryAccessContext, bind_memory_access

    namespace = replace(NAMESPACE, task_id=task_id, goal_id=goal_id, conversation_id="synthetic-conversation")
    expiry = time.time() + 3600
    grants = tuple(MemoryCapability(channel, namespace, scope, operations, sensitivity, expiry)
                   for channel in channels for scope in scopes)
    policies = PolicyFixture(MemoryRunPolicy(ACTOR, namespace, "synthetic-memory-fixture", expiry,
                                             capabilities=grants))
    audit = AuditFixture()
    gate = MemoryPolicyGateway(policies=policies, audit=audit,
                               channels=MemoryChannelRegistry(frozenset(channels)))
    with bind_memory_access(MemoryAccessContext(ACTOR, namespace.run_id, gate)):
        yield policies, audit


class SyntheticWorkerMemoryAuthorization:
    """Explicit mutable Hub-transport double, isolated to worker execution tests."""

    def __init__(self, *, task_id="t1", project_id="proj-a"):
        self.namespace = replace(NAMESPACE, task_id=task_id, workspace_id=project_id)
        self.operations = {MemoryOperation.read, MemoryOperation.append, MemoryOperation.update}
        self.expires_at = time.time() + 3600
        self.digest = "a" * 64

    def authorize(self, *, scope, operation):
        from worker.core.memory_authorization import WorkerMemoryAccess

        if operation not in self.operations:
            raise ValueError("synthetic_memory_capability_missing")
        return WorkerMemoryAccess(self.namespace, scope, operation, self.expires_at, self.digest)
