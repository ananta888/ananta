"""Revalidate one private-result owner against the Hub-owned Worker registry."""

import math
from collections.abc import Mapping

from agent.services.workflow_worker_service_auth import STRICT_WORKER_REGISTRATION_PROVENANCE
from ananta_contracts.native_result_maintenance import MAINTENANCE_CAPABILITY, RESULT_STORE_BINDING_LIMIT


def require_registered_result_store_owner(worker, owner, *, now, offline_timeout=300):
    def value(name, default=None):
        return worker.get(name, default) if isinstance(worker, Mapping) else getattr(worker, name, default)

    limits = value('execution_limits', {}) or {}
    if not isinstance(limits, Mapping) and hasattr(limits, 'model_dump'):
        limits = limits.model_dump()
    seen = value('last_seen')
    if (type(now) not in (int, float) or not math.isfinite(now)
            or type(seen) not in (int, float) or not math.isfinite(seen)
            or type(offline_timeout) is not int or not 1 <= offline_timeout <= 86400
            or not -60 <= now - seen < offline_timeout
            or value('role') != 'worker' or value('status') != 'online'
            or value('registration_validated') is not True
            or value('registration_provenance') != STRICT_WORKER_REGISTRATION_PROVENANCE
            or not {MAINTENANCE_CAPABILITY, 'workflow.adapter.native'} <= set(
                value('authorized_capabilities', ()) or ())
            or value('name') != owner['worker_id']
            or str(value('url') or '').rstrip('/') != owner['worker_url']
            or not isinstance(limits, Mapping) or limits.get(RESULT_STORE_BINDING_LIMIT) != owner['store_binding_id']):
        raise ValueError('native_result_store_registered_owner_unavailable')
