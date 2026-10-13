"""Worker execution port: current authorization comes from the Hub, never flags."""

import math
import re
import time
from dataclasses import dataclass
from typing import Protocol

from ananta_contracts.memory_access import (
    MemoryNamespace,
    MemoryOperation,
    MemoryScope,
    require_memory_timestamp,
)


@dataclass(frozen=True)
class WorkerMemoryAccess:
    namespace: MemoryNamespace
    scope: MemoryScope
    operation: MemoryOperation
    expires_at: float
    policy_digest: str

    def __post_init__(self):
        if not isinstance(self.namespace, MemoryNamespace):
            raise ValueError("memory_namespace_invalid")
        self.namespace.key(self.scope)
        if not isinstance(self.operation, MemoryOperation):
            raise ValueError("memory_operation_invalid")
        require_memory_timestamp(self.expires_at, "expires_at")
        if not isinstance(self.policy_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", self.policy_digest):
            raise ValueError("memory_policy_digest_invalid")


class WorkerMemoryAuthorizationPort(Protocol):
    def authorize(self, *, scope: MemoryScope, operation: MemoryOperation) -> WorkerMemoryAccess:
        """Resolve current assignment/lease through authenticated Hub transport."""
        ...


def require_worker_memory(authorization, scope, operation, *, clock=time.time):
    if authorization is None:
        raise ValueError("memory_hub_authorization_required")
    try:
        decision = authorization.authorize(scope=scope, operation=operation)
    except Exception as exc:
        raise ValueError("memory_hub_authorization_unavailable") from exc
    now = clock()
    if (not isinstance(decision, WorkerMemoryAccess) or decision.scope != scope
            or decision.operation != operation or type(now) not in (int, float) or not math.isfinite(now)
            or now >= decision.expires_at):
        raise ValueError("memory_hub_authorization_invalid")
    return decision
