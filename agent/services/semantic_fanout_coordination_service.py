"""Hub-owned receiver routing for the bounded semantic-media parent profile.

This module does not implement the specialised broadcast/fleet optimiser.  It
creates a small, deterministic plan for at most eight already-authorized room
members and keeps private recovery separate from the common SFU publication.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

from agent.services.sfu_broadcast_participant_limits import SFU_BROADCAST_MAX_GROUP_MEMBERS

ReceiverPath = Literal["ordinary_sfu", "semantic_sfu", "ordinary_direct"]


@dataclass(frozen=True, slots=True)
class ReceiverRouteRequest:
    receiver_id: str
    requested_path: Literal["auto", "ordinary", "semantic"]
    sfu_authorized: bool
    ordinary_authorized: bool
    semantic_authorized: bool
    semantic_capable: bool
    semantic_contract_active: bool


@dataclass(frozen=True, slots=True)
class ReceiverRoute:
    receiver_id: str
    path: ReceiverPath
    reason_code: str
    common_publication: bool
    private_recovery_authorized: bool = False


@dataclass(frozen=True, slots=True)
class SemanticFanoutPlan:
    publication_id: str
    routes: tuple[ReceiverRoute, ...]
    ordinary_receiver_count: int
    semantic_receiver_count: int
    upload_count: int


class SemanticFanoutCoordinationService:
    """Intersect user preferences with Hub grants; never expand rights."""

    def plan(
        self,
        *,
        publication_id: str,
        receivers: tuple[ReceiverRouteRequest, ...],
        private_recovery_audience: Mapping[str, bool] | None = None,
    ) -> SemanticFanoutPlan:
        if not _identifier(publication_id):
            raise ValueError("semantic_fanout_publication_invalid")
        if not receivers or len(receivers) > SFU_BROADCAST_MAX_GROUP_MEMBERS:
            raise ValueError("semantic_fanout_receiver_count_invalid")
        if len({row.receiver_id for row in receivers}) != len(receivers):
            raise ValueError("semantic_fanout_receiver_duplicate")
        recovery = dict(private_recovery_audience or {})
        routes: list[ReceiverRoute] = []
        for request in sorted(receivers, key=lambda item: item.receiver_id):
            if not _identifier(request.receiver_id):
                raise ValueError("semantic_fanout_receiver_invalid")
            path, reason = self._route(request)
            routes.append(
                ReceiverRoute(
                    receiver_id=request.receiver_id,
                    path=path,
                    reason_code=reason,
                    common_publication=path in {"ordinary_sfu", "semantic_sfu"},
                    private_recovery_authorized=recovery.get(request.receiver_id) is True,
                )
            )
        semantic = sum(route.path == "semantic_sfu" for route in routes)
        ordinary = len(routes) - semantic
        return SemanticFanoutPlan(
            publication_id=publication_id,
            routes=tuple(routes),
            ordinary_receiver_count=ordinary,
            semantic_receiver_count=semantic,
            # Ordinary and semantic payloads are multiplexed on one admitted
            # SFU publication; direct-only plans do not allocate an SFU upload.
            upload_count=1 if any(route.common_publication for route in routes) else 0,
        )

    @staticmethod
    def _route(value: ReceiverRouteRequest) -> tuple[ReceiverPath, str]:
        if not value.ordinary_authorized:
            raise ValueError("semantic_fanout_ordinary_permission_required")
        semantic_ready = (
            value.sfu_authorized
            and value.semantic_authorized
            and value.semantic_capable
            and value.semantic_contract_active
        )
        if value.requested_path == "semantic" and semantic_ready:
            return "semantic_sfu", "semantic_fanout_semantic_admitted"
        if value.requested_path == "semantic" and not semantic_ready:
            return "ordinary_sfu" if value.sfu_authorized else "ordinary_direct", "semantic_fanout_safe_fallback"
        if value.sfu_authorized and value.requested_path in {"auto", "ordinary"}:
            return "ordinary_sfu", "semantic_fanout_ordinary_admitted"
        return "ordinary_direct", "semantic_fanout_sfu_not_authorized"


def _identifier(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value.encode("utf-8")) <= 128:
        return False
    return value[0].isalnum() and all(char.isalnum() or char in "._:-" for char in value)


__all__ = [
    "ReceiverRoute",
    "ReceiverRouteRequest",
    "SemanticFanoutCoordinationService",
    "SemanticFanoutPlan",
]
