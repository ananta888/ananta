"""Atomic first-manifest binding for Recovery execute leases."""

from __future__ import annotations

import copy
import hmac
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from agent.common.recovery_artifact_manifest_write_boundary import (
    RECOVERY_ARTIFACT_MANIFEST_BINDING_FIELDS,
    RECOVERY_ARTIFACT_MANIFEST_BINDING_SCHEMA,
    authorize_recovery_artifact_manifest_write,
)

RECOVERY_ARTIFACT_MANIFEST_BINDING_KEY = (
    "artifact_manifest_binding"
)


class RecoveryArtifactManifestBindingError(RuntimeError):
    """Stable denial at the lease-owned manifest write boundary."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class RecoveryArtifactManifestBindingResult:
    task: Any
    binding: dict[str, Any]
    replayed: bool


class RecoveryArtifactManifestBindingService:
    """Publish exactly one immutable manifest claim per lease revision."""

    def __init__(
        self,
        *,
        now_provider: Callable[[], float] = time.time,
    ) -> None:
        self._now_provider = now_provider

    def bind(
        self,
        *,
        task: Any,
        manifest: Mapping[str, Any],
        task_repository: Any,
    ) -> RecoveryArtifactManifestBindingResult:
        task_id = str(_value(task, "id") or "").strip()
        details = _mapping(
            _value(task, "status_reason_details")
        )
        lease = _mapping(
            details.get("recovery_dispatch_lease")
        )
        expected = self._expected_binding(
            task_id=task_id,
            lease=lease,
            manifest=manifest,
        )
        existing = lease.get(
            RECOVERY_ARTIFACT_MANIFEST_BINDING_KEY
        )
        if existing is not None:
            normalized_existing = self._validated_existing(
                existing,
                expected=expected,
            )
            return RecoveryArtifactManifestBindingResult(
                task=task,
                binding=normalized_existing,
                replayed=True,
            )

        binding = {
            **expected,
            "bound_at": self._bound_at(),
        }
        candidate = copy.deepcopy(task)
        candidate_details = _mapping(
            _value(candidate, "status_reason_details")
        )
        candidate_lease = _mapping(
            candidate_details.get("recovery_dispatch_lease")
        )
        candidate_lease[
            RECOVERY_ARTIFACT_MANIFEST_BINDING_KEY
        ] = dict(binding)
        candidate_details["recovery_dispatch_lease"] = (
            candidate_lease
        )
        _set_value(
            candidate,
            "status_reason_details",
            candidate_details,
        )
        if _has_value(candidate, "updated_at"):
            _set_value(
                candidate,
                "updated_at",
                self._now_provider(),
            )
        try:
            with authorize_recovery_artifact_manifest_write(
                task_id=task_id,
                lease=lease,
                binding=binding,
            ):
                persisted = task_repository.save(candidate)
        except (TypeError, ValueError) as exc:
            raise RecoveryArtifactManifestBindingError(
                "recovery_artifact_manifest_binding_denied"
            ) from exc

        persisted_lease = _mapping(
            _mapping(
                _value(persisted, "status_reason_details")
            ).get("recovery_dispatch_lease")
        )
        persisted_binding = persisted_lease.get(
            RECOVERY_ARTIFACT_MANIFEST_BINDING_KEY
        )
        normalized_persisted = self._validated_existing(
            persisted_binding,
            expected=expected,
        )
        return RecoveryArtifactManifestBindingResult(
            task=persisted,
            binding=normalized_persisted,
            replayed=False,
        )

    def _bound_at(self) -> float:
        value = float(self._now_provider())
        if not math.isfinite(value) or value <= 0.0:
            raise RecoveryArtifactManifestBindingError(
                "recovery_artifact_manifest_binding_clock_invalid"
            )
        return value

    @staticmethod
    def _expected_binding(
        *,
        task_id: str,
        lease: Mapping[str, Any],
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            lease_revision = int(lease.get("revision"))
        except (TypeError, ValueError) as exc:
            raise RecoveryArtifactManifestBindingError(
                "recovery_artifact_manifest_binding_invalid"
            ) from exc
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list):
            raise RecoveryArtifactManifestBindingError(
                "recovery_artifact_manifest_binding_invalid"
            )
        return {
            "schema": (
                RECOVERY_ARTIFACT_MANIFEST_BINDING_SCHEMA
            ),
            "task_id": task_id,
            "lease_revision": lease_revision,
            "token_digest": str(
                lease.get("token_digest") or ""
            ),
            "request_fingerprint": str(
                lease.get("request_fingerprint") or ""
            ),
            "manifest_digest": str(
                manifest.get("digest") or ""
            ),
            "artifact_count": len(artifacts),
            "total_bytes": sum(
                int(value["size_bytes"])
                for value in artifacts
            ),
        }

    @staticmethod
    def _validated_existing(
        value: Any,
        *,
        expected: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise RecoveryArtifactManifestBindingError(
                "recovery_artifact_manifest_binding_invalid"
            )
        binding = dict(value)
        try:
            bound_at = float(binding.get("bound_at"))
        except (TypeError, ValueError) as exc:
            raise RecoveryArtifactManifestBindingError(
                "recovery_artifact_manifest_binding_invalid"
            ) from exc
        if (
            set(binding)
            != RECOVERY_ARTIFACT_MANIFEST_BINDING_FIELDS
            or not math.isfinite(bound_at)
            or bound_at <= 0.0
        ):
            raise RecoveryArtifactManifestBindingError(
                "recovery_artifact_manifest_binding_invalid"
            )
        for key, expected_value in expected.items():
            actual_value = binding.get(key)
            if key in {
                "token_digest",
                "request_fingerprint",
                "manifest_digest",
            }:
                matches = bool(
                    isinstance(actual_value, str)
                    and isinstance(expected_value, str)
                    and hmac.compare_digest(
                        actual_value,
                        expected_value,
                    )
                )
            else:
                matches = actual_value == expected_value
            if not matches:
                raise RecoveryArtifactManifestBindingError(
                    "recovery_artifact_manifest_conflict"
                )
        binding["bound_at"] = bound_at
        return binding


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _value(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _has_value(value: Any, name: str) -> bool:
    if isinstance(value, Mapping):
        return name in value
    return hasattr(value, name)


def _set_value(value: Any, name: str, item: Any) -> None:
    if isinstance(value, dict):
        value[name] = item
        return
    setattr(value, name, item)


_SERVICE = RecoveryArtifactManifestBindingService()


def get_recovery_artifact_manifest_binding_service() -> (
    RecoveryArtifactManifestBindingService
):
    return _SERVICE


__all__ = [
    "RECOVERY_ARTIFACT_MANIFEST_BINDING_KEY",
    "RecoveryArtifactManifestBindingError",
    "RecoveryArtifactManifestBindingResult",
    "RecoveryArtifactManifestBindingService",
    "get_recovery_artifact_manifest_binding_service",
]
