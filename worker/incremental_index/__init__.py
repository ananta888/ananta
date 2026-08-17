"""CodeCompass Incremental Index - M1 Implementation.

Implements CIL-001 through CIL-004:
- CIL-001: JSON Schemas (ChangeSet, ArtifactLayer, LayerHead, CompactionPlan)
- CIL-002: BuildProfile Schema + CompatibilityKey Logic
- CIL-003: Content-addressed Layer Store Implementation
- CIL-004: Atomic Head Registry with Generation/CAS
"""

from worker.incremental_index.head_registry import HeadUpdateResult, LayerHeadRegistry
from worker.incremental_index.layer_store import ArtifactLayerStore, LayerMetadata

__version__ = "0.1.0"

__all__ = [
    "ArtifactLayerStore",
    "LayerMetadata",
    "LayerHeadRegistry",
    "HeadUpdateResult",
]
