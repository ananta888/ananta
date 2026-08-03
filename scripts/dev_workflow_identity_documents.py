"""Deterministic documents for local Compose Worker identities.

The bootstrap script owns filesystem safety and transactional publication.
This helper owns only the pure registration-document shape so identity schema
evolution stays separate from credential I/O.
"""

from __future__ import annotations

import hashlib
from typing import Any, NamedTuple

from ananta_contracts.general_worker_capabilities import (
    GENERAL_PURPOSE_WORKER_CAPABILITIES,
)

WORKER_CAPABILITIES = [
    *GENERAL_PURPOSE_WORKER_CAPABILITIES,
    "workflow.adapter.native",
    "approval",
    "bounded_parallel",
    "checkpoint",
    "deterministic_merge",
    "resume",
    "retrieval",
    "index_write",
    "stream",
    "structured_output",
    "subgraphs",
    "tool_calling",
    "vector_index_operation",
]
LEGACY_WORKER_CAPABILITIES = [
    capability
    for capability in WORKER_CAPABILITIES
    if capability not in {"index_write", "vector_index_operation"}
]
UPGRADABLE_WORKER_CAPABILITY_SETS = tuple(
    tuple(
        capability
        for capability in WORKER_CAPABILITIES
        if capability not in omitted
    )
    for omitted in (
        frozenset({"source_analysis"}),
        frozenset({"index_write", "vector_index_operation"}),
        frozenset(
            {
                "source_analysis",
                "index_write",
                "vector_index_operation",
            }
        ),
    )
)


class WorkerRegistrationSpec(NamedTuple):
    logical_name: str
    worker_id: str
    worker_url: str


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def registration_document(
    values: dict[str, str],
    *,
    worker_specs: tuple[WorkerRegistrationSpec, ...],
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    """Build the exact Hub registration keyring for both local Workers."""

    allowed_capabilities = list(
        WORKER_CAPABILITIES
        if capabilities is None
        else capabilities
    )
    specs_by_name = {
        spec.logical_name: spec for spec in worker_specs
    }
    alpha = specs_by_name["alpha"]
    beta = specs_by_name["beta"]
    return {
        "schema": "ananta.workflow-worker-registration-keyring.v1",
        "workers": {
            alpha.worker_id: {
                "worker_url": alpha.worker_url,
                "registration_token": values[
                    "alpha_registration_token"
                ],
                "service_token_sha256": _sha256_text(
                    values["alpha_service_token"]
                ),
                "session_signing_key_sha256": _sha256_text(
                    values["alpha_session_key"]
                ),
                "allowed_capabilities": allowed_capabilities,
            },
            beta.worker_id: {
                "worker_url": beta.worker_url,
                "registration_token": values[
                    "beta_registration_token"
                ],
                "service_token_sha256": _sha256_text(
                    values["beta_service_token"]
                ),
                "session_signing_key_sha256": _sha256_text(
                    values["beta_session_key"]
                ),
                "allowed_capabilities": list(allowed_capabilities),
            },
        },
    }
