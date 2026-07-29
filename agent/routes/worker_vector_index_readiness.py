"""Worker-only HTTP composition for Vector index readiness."""

from __future__ import annotations

import time

from flask import Blueprint, current_app, jsonify

from agent.config import settings
from agent.services.background.registration import (
    get_registration_state,
)
from worker.retrieval.vector_index_worker_readiness import (
    VectorIndexWorkerReadinessPolicy,
)

worker_vector_index_readiness_bp = Blueprint(
    "worker_vector_index_readiness",
    __name__,
)
_policy = VectorIndexWorkerReadinessPolicy()


@worker_vector_index_readiness_bp.get(
    "/internal/worker/vector-index-readiness"
)
def vector_index_worker_readiness():
    workflow_registration = current_app.extensions.get(
        "workflow_adapter_worker_registration"
    )
    workflow_registration = (
        workflow_registration
        if isinstance(workflow_registration, dict)
        else {}
    )
    snapshot = _policy.evaluate(
        role=current_app.config.get("ROLE", settings.role),
        agent_name=current_app.config.get("AGENT_NAME"),
        vector_registration=current_app.extensions.get(
            "vector_index_worker_registration"
        ),
        advertised_capabilities=workflow_registration.get(
            "capabilities"
        ),
        hub_registration=get_registration_state(),
        now=time.time(),
        registration_max_age_seconds=float(
            settings.agent_offline_timeout
        ),
    )
    return jsonify(snapshot.as_dict()), 200 if snapshot.ready else 503


__all__ = ["worker_vector_index_readiness_bp"]
