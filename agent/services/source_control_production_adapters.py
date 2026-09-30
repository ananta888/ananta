"""Production adapters for source operations, destinations and artifact purge.

Public entry point of the Source Control production adapter family. Each
adapter lives in its own module (SRP) and is re-exported here so existing
imports keep working:

* ``source_control_adapter_common`` -- error type and public value projection
* ``source_control_index_submission_adapter`` -- bound index job submission
* ``source_control_operations_adapter`` -- refresh/scan/run/graph/query ops
* ``source_control_destination_catalog`` -- scoped worker/model destinations
* ``source_control_effective_access_adapters`` -- effective access adapters
* ``source_control_artifact_deletion`` -- contained artifact deletion
"""

from __future__ import annotations

from agent.services.source_control_adapter_common import (
    SourceControlProductionAdapterError,
)
from agent.services.source_control_artifact_deletion import (
    ContainedArtifactDeletionService,
)
from agent.services.source_control_destination_catalog import (
    ScopedWorkerModelDestinationCatalog,
)
from agent.services.source_control_effective_access_adapters import (
    PersistentGrantEffectivePolicy,
    ScopedEffectiveDestinationCatalog,
    SQLSourceRevisionAccessCatalog,
    build_scoped_effective_access_service,
    derive_policy_snapshot_id,
)
from agent.services.source_control_index_submission_adapter import (
    HubBoundSourceIndexSubmissionAdapter,
)
from agent.services.source_control_operations_adapter import (
    HubSourceControlOperationsAdapter,
)

__all__ = [
    "ContainedArtifactDeletionService",
    "HubBoundSourceIndexSubmissionAdapter",
    "HubSourceControlOperationsAdapter",
    "PersistentGrantEffectivePolicy",
    "SQLSourceRevisionAccessCatalog",
    "ScopedEffectiveDestinationCatalog",
    "ScopedWorkerModelDestinationCatalog",
    "SourceControlProductionAdapterError",
    "build_scoped_effective_access_service",
    "derive_policy_snapshot_id",
]
