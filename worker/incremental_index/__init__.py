"""
CodeCompass Incremental Index - M1 Implementation

Implements CIL-001 through CIL-004:
- CIL-001: JSON Schemas (ChangeSet, ArtifactLayer, LayerHead, CompactionPlan)
- CIL-002: BuildProfile Schema + CompatibilityKey Logic  
- CIL-003: Content-addressed Layer Store Implementation
- CIL-004: Atomic Head Registry with Generation/CAS
"""

__version__ = "0.1.0"

from .layer_store import ArtifactLayerStore, LayerMetadata
from .head_registry import LayerHeadRegistry, HeadUpdateResult

__all__ = [
    # Layer Store
    "ArtifactLayerStore",
    "LayerMetadata",
    
    # Head Registry
    "LayerHeadRegistry",
    "HeadUpdateResult",
]
