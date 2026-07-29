"""Worker-facing imports for the shared vector-index attestation contract."""

from ananta_contracts.vector_index_task_attestation import (
    VECTOR_INDEX_TASK_ATTESTATION_FIELD,
    VECTOR_INDEX_TASK_ATTESTATION_SCHEMA,
    VectorIndexTaskAttestationError,
    VectorIndexTaskVerifier,
)

__all__ = [
    "VECTOR_INDEX_TASK_ATTESTATION_FIELD",
    "VECTOR_INDEX_TASK_ATTESTATION_SCHEMA",
    "VectorIndexTaskAttestationError",
    "VectorIndexTaskVerifier",
]
