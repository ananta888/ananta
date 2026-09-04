"""Side-effect-free Hub policy for local model resource admission."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ananta_contracts.local_model_evaluation import LocalModelRuntimeProfile


@dataclass(frozen=True, slots=True)
class LocalResourceObservation:
    gpu_name: str
    total_vram_bytes: int
    free_vram_bytes: int
    total_ram_bytes: int
    available_ram_bytes: int
    swap_used_bytes: int
    thermal_throttling: bool = False


@dataclass(frozen=True, slots=True)
class LocalModelResourceDecision:
    admitted: bool
    reason_code: str
    context_tokens: int
    required_vram_bytes: int
    required_ram_bytes: int
    reserve_vram_bytes: int
    reserve_ram_bytes: int
    swap_baseline_bytes: int


class LocalModelRuntimeProfileLoader:
    def load(self, path: str | Path) -> LocalModelRuntimeProfile:
        try:
            return LocalModelRuntimeProfile.model_validate_json(
                Path(path).read_text(encoding="utf-8")
            )
        except Exception as exc:
            raise ValueError("local_model_runtime_profile_invalid") from exc


class LocalModelResourcePolicy:
    """Admit one configured context while preserving explicit headroom."""

    def evaluate(
        self,
        profile: LocalModelRuntimeProfile,
        *,
        context_tokens: int,
        resources: LocalResourceObservation,
    ) -> LocalModelResourceDecision:
        candidate = next(
            (item for item in profile.contexts if item.context_tokens == context_tokens),
            None,
        )
        if candidate is None:
            return self._deny(profile, context_tokens, resources, "local_model_context_not_configured")
        if resources.thermal_throttling:
            return self._deny(profile, context_tokens, resources, "local_model_thermal_throttling")
        if resources.gpu_name != profile.gpu_name:
            return self._deny(profile, context_tokens, resources, "local_model_gpu_mismatch")
        if resources.total_vram_bytes < profile.minimum_total_vram_bytes:
            return self._deny(profile, context_tokens, resources, "local_model_total_vram_insufficient")
        if resources.total_ram_bytes < profile.minimum_total_ram_bytes:
            return self._deny(profile, context_tokens, resources, "local_model_total_ram_insufficient")
        reserve_vram = int(resources.total_vram_bytes * profile.minimum_reserve_fraction)
        reserve_ram = int(resources.total_ram_bytes * profile.minimum_reserve_fraction)
        if candidate.estimated_vram_bytes > max(0, resources.free_vram_bytes - reserve_vram):
            return self._deny(profile, context_tokens, resources, "local_model_vram_reserve_insufficient")
        if candidate.estimated_ram_bytes > max(0, resources.available_ram_bytes - reserve_ram):
            return self._deny(profile, context_tokens, resources, "local_model_ram_reserve_insufficient")
        if candidate.state == "stress_only":
            return self._deny(profile, context_tokens, resources, "local_model_context_stress_only")
        return LocalModelResourceDecision(
            admitted=True,
            reason_code="local_model_resources_admitted",
            context_tokens=context_tokens,
            required_vram_bytes=candidate.estimated_vram_bytes,
            required_ram_bytes=candidate.estimated_ram_bytes,
            reserve_vram_bytes=reserve_vram,
            reserve_ram_bytes=reserve_ram,
            swap_baseline_bytes=resources.swap_used_bytes,
        )

    @staticmethod
    def _deny(
        profile: LocalModelRuntimeProfile,
        context_tokens: int,
        resources: LocalResourceObservation,
        reason_code: str,
    ) -> LocalModelResourceDecision:
        candidate = next(
            (item for item in profile.contexts if item.context_tokens == context_tokens),
            None,
        )
        return LocalModelResourceDecision(
            admitted=False,
            reason_code=reason_code,
            context_tokens=context_tokens,
            required_vram_bytes=candidate.estimated_vram_bytes if candidate else 0,
            required_ram_bytes=candidate.estimated_ram_bytes if candidate else 0,
            reserve_vram_bytes=int(resources.total_vram_bytes * profile.minimum_reserve_fraction),
            reserve_ram_bytes=int(resources.total_ram_bytes * profile.minimum_reserve_fraction),
            swap_baseline_bytes=resources.swap_used_bytes,
        )


__all__ = [
    "LocalModelResourceDecision",
    "LocalModelResourcePolicy",
    "LocalModelRuntimeProfileLoader",
    "LocalResourceObservation",
]
