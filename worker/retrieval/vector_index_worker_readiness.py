"""Fail-closed readiness policy for delegated Vector index Workers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

REQUIRED_VECTOR_INDEX_CAPABILITIES = (
    "retrieval",
    "index_write",
    "vector_index_operation",
)


def _capabilities(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(
        value, (list, tuple, set, frozenset)
    ):
        return ()
    return tuple(
        sorted(
            {
                str(item).strip()
                for item in value
                if str(item).strip()
            }
        )
    )


@dataclass(frozen=True, slots=True)
class VectorIndexWorkerReadiness:
    """Operator-safe projection consumed by the Worker HTTP route."""

    ready: bool
    reason_codes: tuple[str, ...]
    advertised_capabilities: tuple[str, ...]
    registered_capabilities: tuple[str, ...]
    vector_registration_ready: bool
    vector_registration_reason: str | None
    hub_registration_ready: bool
    registered_as: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "ready" if self.ready else "not_ready",
            "ready": self.ready,
            "reason_codes": list(self.reason_codes),
            "required_capabilities": list(
                REQUIRED_VECTOR_INDEX_CAPABILITIES
            ),
            "advertised_capabilities": list(
                self.advertised_capabilities
            ),
            "registered_capabilities": list(
                self.registered_capabilities
            ),
            "vector_index_worker_registration": {
                "ready": self.vector_registration_ready,
                "reason_code": self.vector_registration_reason,
            },
            "hub_registration": {
                "ready": self.hub_registration_ready,
                "registered_as": self.registered_as,
            },
        }


class VectorIndexWorkerReadinessPolicy:
    """Combine local composition and Hub-registration evidence."""

    def evaluate(
        self,
        *,
        role: object,
        agent_name: object,
        vector_registration: object,
        advertised_capabilities: object,
        hub_registration: object,
        now: float,
        registration_max_age_seconds: float,
    ) -> VectorIndexWorkerReadiness:
        vector_state = (
            vector_registration
            if isinstance(vector_registration, Mapping)
            else {}
        )
        hub_state = (
            hub_registration
            if isinstance(hub_registration, Mapping)
            else {}
        )
        advertised = _capabilities(advertised_capabilities)
        registered = _capabilities(
            hub_state.get("registered_capabilities")
        )
        required = set(REQUIRED_VECTOR_INDEX_CAPABILITIES)
        normalized_role = str(role or "").strip().lower()
        expected_name = str(agent_name or "").strip()
        registered_as = str(
            hub_state.get("registered_as") or ""
        ).strip()

        reasons: list[str] = []
        if normalized_role != "worker":
            reasons.append("vector_index_worker_role_required")

        vector_ready = vector_state.get("ready") is True
        vector_reason = self._safe_vector_reason(
            vector_state.get("reason_code")
        )
        if not vector_ready:
            reasons.append(
                vector_reason
                or "vector_index_worker_composition_not_ready"
            )

        if not required.issubset(set(advertised)):
            reasons.append(
                "vector_index_worker_capabilities_not_advertised"
            )

        hub_ready, hub_reason = self._hub_registration_ready(
            hub_state=hub_state,
            expected_name=expected_name,
            registered_as=registered_as,
            registered_capabilities=registered,
            required_capabilities=required,
            now=now,
            registration_max_age_seconds=(
                registration_max_age_seconds
            ),
        )
        if hub_reason:
            reasons.append(hub_reason)

        unique_reasons = tuple(dict.fromkeys(reasons))
        return VectorIndexWorkerReadiness(
            ready=not unique_reasons,
            reason_codes=unique_reasons,
            advertised_capabilities=advertised,
            registered_capabilities=registered,
            vector_registration_ready=vector_ready,
            vector_registration_reason=vector_reason,
            hub_registration_ready=hub_ready,
            registered_as=registered_as or None,
        )

    @staticmethod
    def _hub_registration_ready(
        *,
        hub_state: Mapping[str, object],
        expected_name: str,
        registered_as: str,
        registered_capabilities: tuple[str, ...],
        required_capabilities: set[str],
        now: float,
        registration_max_age_seconds: float,
    ) -> tuple[bool, str | None]:
        if hub_state.get("enabled") is not True:
            return (
                False,
                "vector_index_worker_hub_registration_disabled",
            )
        if not expected_name or registered_as != expected_name:
            return (
                False,
                "vector_index_worker_hub_registration_identity_mismatch",
            )
        try:
            last_success_at = float(
                hub_state.get("last_success_at")
            )
            maximum_age = float(registration_max_age_seconds)
            current_time = float(now)
        except (TypeError, ValueError):
            return (
                False,
                "vector_index_worker_hub_registration_pending",
            )
        if (
            last_success_at <= 0
            or maximum_age <= 0
            or current_time < last_success_at
            or current_time - last_success_at > maximum_age
        ):
            return (
                False,
                "vector_index_worker_hub_registration_stale",
            )
        if not required_capabilities.issubset(
            set(registered_capabilities)
        ):
            return (
                False,
                "vector_index_worker_hub_capabilities_incomplete",
            )
        return True, None

    @staticmethod
    def _safe_vector_reason(value: object) -> str | None:
        reason = str(value or "").strip()
        if (
            reason.startswith("vector_index_")
            and len(reason) <= 160
            and "\x00" not in reason
        ):
            return reason
        return None


__all__ = [
    "REQUIRED_VECTOR_INDEX_CAPABILITIES",
    "VectorIndexWorkerReadiness",
    "VectorIndexWorkerReadinessPolicy",
]
