"""Hub-owned, advisory-only training backend selection policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

BACKEND_METADATA: Mapping[str, Mapping[str, Any]] = {
    "autotrain": {
        "version": "0.8.36",
        "license_spdx": "Apache-2.0",
        "maintenance": "unmaintained",
        "maturity": "experimental",
        "methods": ("lora", "qlora"),
        "objectives": ("sft",),
        "resource_profiles": ("generic-safe", "rtx3080-safe"),
    },
    "axolotl": {
        "version": "0.18.0",
        "license_spdx": "Apache-2.0",
        "maintenance": "active",
        "maturity": "experimental",
        "methods": ("lora", "qlora"),
        "objectives": ("sft",),
        "resource_profiles": ("generic-safe", "rtx3080-safe"),
    },
    "llamafactory": {
        "version": "0.9.5",
        "license_spdx": "Apache-2.0",
        "maintenance": "active",
        "maturity": "experimental",
        "methods": ("lora", "qlora"),
        "objectives": ("sft",),
        "resource_profiles": ("generic-safe", "rtx3080-safe"),
    },
    "torchtune": {
        "version": "0.6.1",
        "license_spdx": "BSD-3-Clause",
        "maintenance": "unmaintained",
        "maturity": "experimental",
        "methods": ("lora", "qlora"),
        "objectives": ("sft",),
        "resource_profiles": ("generic-safe", "rtx3080-safe"),
    },
}

_PREFERENCE = {
    "unsloth": 100,
    "peft_trl": 90,
    "axolotl": 80,
    "llamafactory": 70,
    "autotrain": 20,
    "torchtune": 10,
    "mock": 0,
}


class BackendSelectionError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class BackendSelectionRequest:
    objective: str
    method: str
    modality: str
    resource_profile: str
    estimated_model_bytes: int
    runtime_budget_seconds: int
    export_format: str
    manual_backend: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BackendSelectionRequest":
        allowed = {
            "estimated_model_bytes",
            "export_format",
            "manual_backend",
            "method",
            "modality",
            "objective",
            "resource_profile",
            "runtime_budget_seconds",
        }
        if set(value) - allowed:
            raise BackendSelectionError("selection_request_invalid", "selection request contains unknown fields")
        return cls(
            objective=_choice(value.get("objective", "sft"), {"sft"}, "objective"),
            method=_choice(value.get("method", "lora"), {"lora", "qlora"}, "method"),
            modality=_choice(value.get("modality", "text"), {"text"}, "modality"),
            resource_profile=_choice(
                value.get("resource_profile", "rtx3080-safe"),
                {"cpu", "generic-safe", "rtx3080-safe"},
                "resource_profile",
            ),
            estimated_model_bytes=_integer(value.get("estimated_model_bytes", 0), "estimated_model_bytes", 0, 1024**5),
            runtime_budget_seconds=_integer(
                value.get("runtime_budget_seconds", 3600), "runtime_budget_seconds", 60, 30 * 24 * 3600
            ),
            export_format=_choice(value.get("export_format", "adapter"), {"adapter"}, "export_format"),
            manual_backend=_optional_identifier(value.get("manual_backend")),
        )


class MlInternBackendSelectionService:
    def enrich_catalog(self, backends: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for raw in backends:
            item = dict(raw)
            metadata = BACKEND_METADATA.get(str(item.get("id") or ""), {})
            item.update(
                {
                    "version": metadata.get("version", "managed-by-existing-worker"),
                    "license_spdx": metadata.get("license_spdx", "see-third-party-register"),
                    "maintenance": metadata.get("maintenance", "active"),
                    "maturity": metadata.get("maturity", "production"),
                    "methods": list(metadata.get("methods", ("lora", "qlora"))),
                    "objectives": list(metadata.get("objectives", ("sft",))),
                    "resource_profiles": list(
                        metadata.get("resource_profiles", ("cpu", "generic-safe", "rtx3080-safe"))
                    ),
                }
            )
            result.append(item)
        return result

    def recommend(
        self,
        request: BackendSelectionRequest,
        *,
        backends: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        catalog = self.enrich_catalog(backends)
        eligible = [item for item in catalog if self._eligible(item, request)]
        if request.manual_backend is not None:
            selected = next((item for item in eligible if item["id"] == request.manual_backend), None)
            if selected is None:
                raise BackendSelectionError(
                    "manual_backend_unavailable", "manually selected backend lacks an admitted capability"
                )
            mode = "manual"
        else:
            maintained = [item for item in eligible if item["maintenance"] == "active"]
            candidates = maintained
            if not candidates:
                raise BackendSelectionError("backend_unavailable", "no backend satisfies the admitted capabilities")
            selected = max(candidates, key=lambda item: (_PREFERENCE.get(str(item["id"]), -1), str(item["id"])))
            mode = "recommendation"
        alternatives = [item for item in eligible if item["id"] != selected["id"]]
        return {
            "schema_version": "ananta.ml-intern-backend-recommendation.v1",
            "mode": mode,
            "backend": selected["id"],
            "requires_confirmation": True,
            "reasons": [
                "worker_capability_available",
                f"resource_profile:{request.resource_profile}",
                f"method:{request.method}",
                f"maintenance:{selected['maintenance']}",
            ],
            "capability_evidence": {
                "source": "current_worker_probe_and_hub_policy",
                "backend_version": selected["version"],
                "available": selected.get("available") is True,
                "reason_code": selected.get("reason_code"),
            },
            "estimated_resources": {
                "model_bytes": request.estimated_model_bytes,
                "runtime_budget_seconds": request.runtime_budget_seconds,
                "profile": request.resource_profile,
                "estimate_only": True,
            },
            "alternatives": [
                {
                    "backend": item["id"],
                    "maintenance": item["maintenance"],
                    "maturity": item["maturity"],
                }
                for item in sorted(alternatives, key=lambda item: str(item["id"]))
            ],
            "fallback_policy": "new_visible_attempt_only",
        }

    @staticmethod
    def _eligible(item: Mapping[str, Any], request: BackendSelectionRequest) -> bool:
        return (
            item.get("available") is True
            and request.method in item.get("methods", ())
            and request.objective in item.get("objectives", ())
            and request.resource_profile in item.get("resource_profiles", ())
        )


def _choice(value: Any, allowed: set[str], field: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in allowed:
        raise BackendSelectionError("selection_request_invalid", f"{field} is unsupported")
    return normalized


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise BackendSelectionError("selection_request_invalid", f"{field} is outside its bound")
    return int(value)


def _optional_identifier(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized not in _PREFERENCE:
        raise BackendSelectionError("selection_request_invalid", "manual_backend is unsupported")
    return normalized


__all__ = [
    "BACKEND_METADATA",
    "BackendSelectionError",
    "BackendSelectionRequest",
    "MlInternBackendSelectionService",
]
