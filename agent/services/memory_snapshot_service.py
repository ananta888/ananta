"""Observe Hub-authorized persistent result-memory write sessions only."""

import re
from typing import Protocol

from ananta_contracts.memory_access import (
    MemoryOperation,
    MemoryRunPolicy,
    MemoryScope,
    MemorySecurityMetadata,
    MemorySensitivity,
    require_memory_identifier,
)


class MemorySnapshotCapturePort(Protocol):
    def capture(self, *, policy: MemoryRunPolicy, phase: str, scope_outcome: str | None) -> dict: ...


def result_memory_snapshot_grants(policy):
    if not isinstance(policy, MemoryRunPolicy):
        raise ValueError('memory_snapshot_binding_invalid')
    writing = {MemoryOperation.append, MemoryOperation.update, MemoryOperation.delete, MemoryOperation.promote}
    grants = [grant for grant in policy.capabilities if writing.intersection(grant.operations)]
    if not grants or any(grant.channel_id != 'hub-result-memory' or grant.scope != MemoryScope.task
                         for grant in grants):
        raise ValueError('memory_snapshot_channel_unsupported')
    return tuple(grants)


def snapshot_capture_grants(policy, phase, scope_outcome):
    if phase not in {'before', 'after'}:
        raise ValueError('memory_snapshot_binding_invalid')
    if ((phase == 'before' and scope_outcome is not None)
            or phase == 'after' and scope_outcome not in {'completed', 'failed'}):
        raise ValueError('memory_snapshot_outcome_invalid')
    return result_memory_snapshot_grants(policy)


def validate_snapshot_entries(entries, *, sensitivity_ceiling):
    """An alternative source port must not persist content or broaden classification."""
    if not isinstance(entries, list) or len(entries) > 1000:
        raise ValueError('memory_snapshot_source_invalid')
    seen = set()
    normalized = []
    levels = tuple(MemorySensitivity)
    for item in entries:
        if not isinstance(item, dict) or set(item) != {'entry_id', 'content_digest', 'metadata_digest', 'security'}:
            raise ValueError('memory_snapshot_source_invalid')
        identity = require_memory_identifier(item['entry_id'], 'entry_id')
        if identity in seen:
            raise ValueError('memory_snapshot_source_invalid')
        seen.add(identity)
        for field in ('content_digest', 'metadata_digest'):
            if not isinstance(item[field], str) or re.fullmatch(r'[a-f0-9]{64}', item[field]) is None:
                raise ValueError('memory_snapshot_source_invalid')
        security = MemorySecurityMetadata.from_mapping(item['security'])
        if security.scope != MemoryScope.task or levels.index(security.sensitivity) > levels.index(sensitivity_ceiling):
            raise ValueError('memory_snapshot_source_invalid')
        normalized.append({**item, 'security': security.to_mapping()})
    return normalized


class MemorySnapshotService:
    def __init__(self, *, repository: MemorySnapshotCapturePort):
        self._repository = repository

    def capture(self, policy: MemoryRunPolicy, phase: str, *, scope_outcome=None):
        snapshot_capture_grants(policy, phase, scope_outcome)
        return self._repository.capture(policy=policy, phase=phase, scope_outcome=scope_outcome)
