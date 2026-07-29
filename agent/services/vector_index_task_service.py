"""Public facade for Hub-owned vector-index mutation task services."""

from __future__ import annotations

from agent.services.vector_index_preparation_policy import (
    DeploymentVectorIndexPreparationPolicy,
    VectorIndexPreparationPolicyConfigurationError,
    VectorIndexPreparationPolicyError,
    VectorIndexPreparationPolicyPort,
)
from agent.services.vector_index_task_contracts import (
    VECTOR_INDEX_OPERATIONS,
    VECTOR_INDEX_RESULT_SCHEMA,
    VECTOR_INDEX_TASK_SCHEMA,
    VectorIndexMigrationPayload,
    VectorIndexOperationPayload,
    VectorIndexTrustedScope,
)
from agent.services.vector_index_task_lifecycle_service import (
    VectorIndexTaskQueuePort,
    VectorIndexTaskRepositoryPort,
    VectorIndexTaskService,
    VectorIndexTaskSignerPort,
)
from agent.services.vector_index_worker_result_boundary import (
    VectorIndexWorkerResultBoundary,
    VectorIndexWorkerResultLimits,
)
from ananta_contracts.vector_index_task_attestation import (
    VectorIndexTaskAttestationError,
    VectorIndexTaskSigner,
)

vector_index_task_service = VectorIndexTaskService()


def get_vector_index_task_service() -> VectorIndexTaskService:
    """Return the process-local Hub lifecycle service."""

    return vector_index_task_service


__all__ = [
    "VECTOR_INDEX_OPERATIONS",
    "VECTOR_INDEX_RESULT_SCHEMA",
    "VECTOR_INDEX_TASK_SCHEMA",
    "VectorIndexMigrationPayload",
    "VectorIndexOperationPayload",
    "DeploymentVectorIndexPreparationPolicy",
    "VectorIndexPreparationPolicyConfigurationError",
    "VectorIndexPreparationPolicyError",
    "VectorIndexPreparationPolicyPort",
    "VectorIndexTaskQueuePort",
    "VectorIndexTaskRepositoryPort",
    "VectorIndexTaskSigner",
    "VectorIndexTaskSignerPort",
    "VectorIndexTaskService",
    "VectorIndexTaskAttestationError",
    "VectorIndexTrustedScope",
    "VectorIndexWorkerResultBoundary",
    "VectorIndexWorkerResultLimits",
    "get_vector_index_task_service",
]
