"""Worker-side verification of scheduler-bound runtime identity."""

from __future__ import annotations

import os
import platform
from collections.abc import Mapping
from typing import Protocol

from ananta_contracts.research_training_execution import ResearchRuntimeManifestV1


class ResearchRuntimeVerifier(Protocol):
    def configure_and_verify(self, manifest: ResearchRuntimeManifestV1) -> None: ...


class EnvironmentResearchRuntimeVerifier:
    """Compare an assignment with immutable values injected by the scheduler."""

    def __init__(
        self,
        *,
        repository_revision: str,
        image_digest: str,
        hardware_profile_digest: str,
        backend_name: str = "ananta-local-torch",
        backend_version: str = "v1",
    ) -> None:
        self._expected = {
            "repository_revision": repository_revision,
            "image_digest": image_digest,
            "hardware_profile_digest": hardware_profile_digest,
            "backend_name": backend_name,
            "backend_version": backend_version,
        }

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> EnvironmentResearchRuntimeVerifier:
        values = environ if environ is not None else os.environ
        required = {
            "repository_revision": "ANANTA_RESEARCH_REPOSITORY_REVISION",
            "image_digest": "ANANTA_RESEARCH_IMAGE_DIGEST",
            "hardware_profile_digest": "ANANTA_RESEARCH_HARDWARE_PROFILE_DIGEST",
        }
        resolved = {name: str(values.get(variable) or "").strip() for name, variable in required.items()}
        missing = sorted(name for name, value in resolved.items() if not value)
        if missing:
            raise RuntimeError(f"research_worker_runtime_identity_missing:{','.join(missing)}")
        return cls(**resolved)

    def configure_and_verify(self, manifest: ResearchRuntimeManifestV1) -> None:
        try:
            import torch
        except ImportError as exc:  # pragma: no cover - image gate exercises the installed runtime
            raise RuntimeError("research_torch_runtime_unavailable") from exc
        torch.use_deterministic_algorithms(True)
        actual = {
            **self._expected,
            "python_version": platform.python_version(),
            "torch_version": str(torch.__version__),
            "cuda_version": str(torch.version.cuda or "none"),
            "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
        }
        declared = manifest.to_dict()
        mismatches = sorted(name for name, value in actual.items() if declared.get(name) != value)
        if mismatches:
            raise PermissionError(f"research_worker_runtime_identity_mismatch:{','.join(mismatches)}")


__all__ = ["EnvironmentResearchRuntimeVerifier", "ResearchRuntimeVerifier"]
