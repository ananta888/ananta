"""Compatibility re-export of the CodeCompass artifact manifest projection.

The projection is pure and dependency-free; it lives in
``agent.models.codecompass_artifact_manifest`` so repositories can verify
manifests without importing the service layer.
"""

from __future__ import annotations

from agent.models.codecompass_artifact_manifest import (
    CodeCompassArtifactManifest,
    CodeCompassArtifactManifestError,
    CodeCompassArtifactManifestProjector,
    CodeCompassCoverage,
    CodeCompassExclusion,
    PublicArtifactReference,
)

__all__ = [
    "CodeCompassArtifactManifest",
    "CodeCompassArtifactManifestError",
    "CodeCompassArtifactManifestProjector",
    "CodeCompassCoverage",
    "CodeCompassExclusion",
    "PublicArtifactReference",
]
