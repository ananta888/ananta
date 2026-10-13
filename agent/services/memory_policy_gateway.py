"""Hub-owned, deny-by-default authorization before memory storage I/O."""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, replace
from typing import Callable, Protocol

from ananta_contracts.memory_access import (
    MemoryActor,
    MemoryOperation,
    MemoryRunPolicy,
    MemoryScope,
    MemorySecurityMetadata,
    MemorySensitivity,
    MemoryTarget,
    require_memory_identifier,
    require_memory_timestamp,
)


class MemoryRunPolicyPort(Protocol):
    def current(self, *, actor: MemoryActor, run_id: str) -> MemoryRunPolicy | None:
        """Resolve authenticated actor, active assignment and current Hub policy."""
        ...


class MemoryChannelRegistryPort(Protocol):
    def contains(self, channel_id: str) -> bool: ...


@dataclass(frozen=True)
class MemoryPolicyDecision:
    allowed: bool
    reason_code: str
    rule_id: str
    actor: MemoryActor
    run_id: str
    operation: MemoryOperation
    target: MemoryTarget | None
    policy_revision: str | None
    policy_digest: str | None
    expires_at: float | None
    trace_id: str | None = None
    audit_event_id: str | None = None

    def __post_init__(self):
        if (type(self.allowed) is not bool or not isinstance(self.actor, MemoryActor)
                or not isinstance(self.operation, MemoryOperation)
                or self.target is not None and not isinstance(self.target, MemoryTarget)):
            raise ValueError("memory_decision_invalid")
        for value in (self.reason_code, self.rule_id):
            if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{1,100}", value):
                raise ValueError("memory_decision_code_invalid")
        require_memory_identifier(self.run_id, "run_id")
        if self.policy_revision is not None:
            require_memory_identifier(self.policy_revision, "policy_revision")
        if self.policy_digest is not None and (
            not isinstance(self.policy_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", self.policy_digest)
        ):
            raise ValueError("memory_policy_digest_invalid")
        if self.expires_at is not None:
            require_memory_timestamp(self.expires_at, "expires_at")
        if self.trace_id is not None:
            require_memory_identifier(self.trace_id, "trace_id")
        if self.audit_event_id is not None and (
                not isinstance(self.audit_event_id, str)
                or re.fullmatch(r"memory-audit-[a-f0-9]{64}", self.audit_event_id) is None):
            raise ValueError("memory_audit_reference_invalid")
        if self.allowed and (self.target is None or self.reason_code != "memory_access_allowed"):
            raise ValueError("memory_decision_invalid")


class MemoryDecisionAuditPort(Protocol):
    def record(self, decision: MemoryPolicyDecision) -> dict | None: ...


class MemoryAccessError(ValueError):
    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


class MemoryPolicyGateway:
    """Evaluate immutable Hub grants. Inventory entries alone grant nothing."""

    def __init__(self, *, policies: MemoryRunPolicyPort, audit: MemoryDecisionAuditPort,
                 channels: MemoryChannelRegistryPort,
                 clock: Callable[[], float] = time.time):
        self._policies = policies
        self._audit = audit
        self._channels = channels
        self._clock = clock

    def authorize(self, *, actor: MemoryActor, run_id: str, operation: MemoryOperation,
                  target: MemoryTarget) -> MemoryPolicyDecision:
        return self._authorize(actor=actor, run_id=run_id, operation=operation, target=target)

    def authorize_run(self, *, actor: MemoryActor, run_id: str, operation: MemoryOperation,
                      channel_id: str, security: MemorySecurityMetadata) -> MemoryPolicyDecision:
        """Derive run namespace from Hub state; callers cannot choose task/workspace."""
        if security.scope != MemoryScope.run:
            raise MemoryAccessError("memory_run_scope_required")
        return self.authorize_current(actor=actor, run_id=run_id, operation=operation,
                                      channel_id=channel_id, security=security)

    def authorize_current(self, *, actor: MemoryActor, run_id: str, operation: MemoryOperation,
                          channel_id: str, security: MemorySecurityMetadata) -> MemoryPolicyDecision:
        return self._authorize(actor=actor, run_id=run_id, operation=operation, target=None,
                               channel_id=channel_id, security=security)

    def _authorize(self, *, actor, run_id, operation, target, channel_id=None, security=None, forced_denial=None):
        require_memory_identifier(run_id, "run_id")
        if not isinstance(actor, MemoryActor) or not isinstance(operation, MemoryOperation):
            raise MemoryAccessError("memory_request_invalid")
        if target is not None and not isinstance(target, MemoryTarget):
            raise MemoryAccessError("memory_target_invalid")
        policy = None
        reason = "memory_policy_unavailable"
        try:
            policy = self._policies.current(actor=actor, run_id=run_id)
            now = self._clock()
            if type(now) not in (int, float) or not math.isfinite(now):
                raise ValueError("invalid clock")
            if not isinstance(policy, MemoryRunPolicy):
                reason = "memory_capability_missing"
            elif policy.actor != actor or policy.namespace.run_id != run_id:
                reason = "memory_run_binding_mismatch"
            elif not policy.active or now >= policy.expires_at:
                reason = "memory_run_inactive"
            else:
                if target is None:
                    target = MemoryTarget(channel_id, policy.namespace, security)
                if self._channels.contains(target.channel_id) is not True:
                    reason = "memory_channel_unregistered"
                else:
                    reason = forced_denial or self._evaluate(policy, operation, target, now)
        except Exception:
            # Do not expose database, policy-provider or payload details in diagnostics.
            reason = "memory_policy_unavailable"
        revision = policy.policy_revision if isinstance(policy, MemoryRunPolicy) else None
        expiry = policy.expires_at if isinstance(policy, MemoryRunPolicy) else None
        decision = MemoryPolicyDecision(
            allowed=reason == "memory_access_allowed", reason_code=reason,
            rule_id="memory.explicit-capability.v1", actor=actor, run_id=run_id,
            operation=operation, target=target, policy_revision=revision, expires_at=expiry,
            policy_digest=policy.digest if isinstance(policy, MemoryRunPolicy) else None,
            trace_id=policy.trace_id if isinstance(policy, MemoryRunPolicy) else None,
        )
        reference = self._publish(decision)
        if not decision.allowed:
            raise MemoryAccessError(decision.reason_code)
        return replace(decision, audit_event_id=reference)

    def _publish(self, decision):
        try:
            receipt = self._audit.record(decision)
            # Legacy/test audit adapters may return no receipt. That origin stays
            # explicitly unverified; the productive SQL issuer returns its own link.
            if receipt is None:
                return None
            if (not isinstance(receipt, dict) or set(receipt) != {'event_id', 'event_hash', 'sequence'}
                    or not isinstance(receipt['event_hash'], str)
                    or re.fullmatch(r'[a-f0-9]{64}', receipt['event_hash']) is None
                    or receipt['event_id'] != 'memory-audit-' + receipt['event_hash']
                    or type(receipt['sequence']) is not int or receipt['sequence'] < 1):
                raise ValueError('memory_audit_reference_invalid')
            return receipt['event_id']
        except Exception as exc:
            raise MemoryAccessError("memory_audit_unavailable") from exc

    @staticmethod
    def _evaluate(policy, operation, target, now):
        if target.namespace.tenant_id != policy.actor.tenant_id:
            return "memory_namespace_mismatch"
        scope = target.security.scope
        # A capability never turns a caller-supplied namespace into current authority.
        if target.namespace.key(scope) != policy.namespace.key(scope):
            return "memory_namespace_mismatch"
        levels = tuple(MemorySensitivity)
        for grant in policy.capabilities:
            if (grant.channel_id == target.channel_id and grant.scope == scope
                    and operation in grant.operations and now < grant.expires_at
                    and grant.namespace.key(scope) == target.namespace.key(scope)
                    and levels.index(target.security.sensitivity) <= levels.index(grant.sensitivity_ceiling)):
                return "memory_access_allowed"
        return "memory_capability_missing"

    def authorize_read(self, **request) -> MemoryPolicyDecision:
        return self.authorize(operation=MemoryOperation.read, **request)

    def authorize_write(self, **request) -> MemoryPolicyDecision:
        return self.authorize(operation=MemoryOperation.append, **request)

    def authorize_update(self, **request) -> MemoryPolicyDecision:
        return self.authorize(operation=MemoryOperation.update, **request)

    def authorize_delete(self, **request) -> MemoryPolicyDecision:
        return self.authorize(operation=MemoryOperation.delete, **request)

    def authorize_promote(
        self, *, actor: MemoryActor, run_id: str, target: MemoryTarget, destination: MemoryTarget,
    ) -> tuple[MemoryPolicyDecision, MemoryPolicyDecision]:
        transitions = {
            MemoryScope.run: MemoryScope.task, MemoryScope.task: MemoryScope.goal,
            MemoryScope.goal: MemoryScope.workspace, MemoryScope.conversation: MemoryScope.workspace,
            MemoryScope.workspace: MemoryScope.global_,
        }
        if not isinstance(target, MemoryTarget) or not isinstance(destination, MemoryTarget):
            raise MemoryAccessError("memory_target_invalid")
        if transitions.get(target.security.scope) != destination.security.scope:
            self._authorize(actor=actor, run_id=run_id, operation=MemoryOperation.promote, target=target,
                            forced_denial="memory_promotion_transition_invalid")
        source = self.authorize(actor=actor, run_id=run_id, operation=MemoryOperation.promote, target=target)
        dest = self.authorize(actor=actor, run_id=run_id, operation=MemoryOperation.append, target=destination)
        if source.policy_digest != dest.policy_digest:
            self._publish(replace(dest, allowed=False, reason_code="memory_policy_changed"))
            raise MemoryAccessError("memory_policy_changed")
        return source, dest
