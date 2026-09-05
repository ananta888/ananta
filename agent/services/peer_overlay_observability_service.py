"""Content-free, tenant-scoped operator projection for peer overlays."""

from __future__ import annotations

import hashlib
import hmac
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from ananta_contracts.peer_overlay import require_overlay_id


class PeerOverlayObservabilityService:
    """Project persisted control state through a strict telemetry allowlist."""

    def __init__(
        self,
        pseudonym_key: bytes,
        *,
        retention_seconds: int = 86_400,
        max_publications: int = 200,
    ) -> None:
        if len(pseudonym_key) < 32:
            raise ValueError("peer_overlay_observability_key_too_short")
        if not 60 <= retention_seconds <= 31_536_000 or not 1 <= max_publications <= 1_000:
            raise ValueError("peer_overlay_observability_bounds_invalid")
        self._key = bytes(pseudonym_key)
        self._retention = retention_seconds
        self._maximum = max_publications

    def project(
        self,
        *,
        plans: Iterable[Mapping[str, Any]],
        memberships: Iterable[Mapping[str, Any]],
        tenant_id: str | None,
        room_id: str | None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        tenant = require_overlay_id(tenant_id, "tenant_id") if tenant_id else None
        room = require_overlay_id(room_id, "room_id") if room_id else None
        instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff = instant - timedelta(seconds=self._retention)
        scoped_plans = self._scope(plans, tenant, room)
        retained = [item for item in scoped_plans if _timestamp(item.get("created_at")) >= cutoff]
        retained.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        selected = retained[: self._maximum] if tenant else []
        publications = [self._publication(item) for item in selected]
        topology_counts = Counter(self._selected_transport(item) for item in retained)
        membership_rows = self._scope(memberships, tenant, room)
        return {
            "scope": "tenant" if tenant else "global_aggregate",
            "tenant_ref": self._reference("tenant", tenant) if tenant else None,
            "room_ref": self._reference("room", room) if tenant and room else None,
            "memberships": [self._membership(item) for item in membership_rows[: self._maximum]]
            if tenant
            else [],
            "plans": publications,
            "aggregate": {
                "retained_publication_count": len(retained),
                "returned_publication_count": len(publications),
                "membership_count": len(membership_rows),
                "selected_transport_counts": dict(sorted(topology_counts.items())),
            },
            "telemetry_policy": {
                "schema": "ananta.peer-overlay-observability.v1",
                "retention_seconds": self._retention,
                "max_publications": self._maximum,
                "tenant_separated": True,
                "content_free": True,
                "identifiers": "hmac_pseudonyms",
                "forbidden_fields": [
                    "content_keys",
                    "sdp",
                    "ice_credentials",
                    "chat_content",
                    "media_content",
                    "full_ip_addresses",
                ],
            },
            "truncated": len(retained) > len(publications),
        }

    @staticmethod
    def _scope(
        rows: Iterable[Mapping[str, Any]], tenant_id: str | None, room_id: str | None
    ) -> list[Mapping[str, Any]]:
        return [
            row
            for row in rows
            if (tenant_id is None or row.get("tenant_id") == tenant_id)
            and (room_id is None or row.get("room_id") == room_id)
        ]

    def _publication(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        leases = list(plan.get("leases") or [])
        selected = self._selected_transport(plan)
        cost = dict(plan.get("cost_admission") or {})
        return {
            "publication_ref": self._reference("publication", str(plan.get("publication_id") or "")),
            "room_ref": self._reference("room", str(plan.get("room_id") or "")),
            "observed_at": plan.get("created_at"),
            "route_epoch": int(dict(plan.get("epochs") or {}).get("route") or 0),
            "lease_count": len(leases),
            "selected_transport": selected,
            "transports": {
                "direct": "active" if selected == "direct" else "standby",
                "mesh": "disabled",
                "peer_dag": "active" if selected == "peer_dag" else "standby",
                "sfu": "standby",
                "turn": "active" if selected == "turn" else "disabled",
            },
            "fallback": "livekit_e2ee",
            "cost": {
                key: cost.get(key)
                for key in (
                    "allowed",
                    "reason_code",
                    "profile_id",
                    "profile_version",
                    "projected_turn_egress_bytes",
                    "projected_peer_relay_egress_bytes",
                )
            },
        }

    @staticmethod
    def _selected_transport(plan: Mapping[str, Any]) -> str:
        source = str(plan.get("source_peer_id") or "")
        leases = list(plan.get("leases") or [])
        if any("turn" in set(lease.get("capabilities") or []) for lease in leases):
            return "turn"
        if any(lease.get("primary_parent_id") != source for lease in leases):
            return "peer_dag"
        return "direct"

    def _membership(self, membership: Mapping[str, Any]) -> dict[str, Any]:
        epochs = dict(membership.get("epochs") or {})
        return {
            "room_ref": self._reference("room", str(membership.get("room_id") or "")),
            "member_count": len(membership.get("member_ids") or []),
            "membership_epoch": int(epochs.get("membership") or 0),
            "key_epoch": int(epochs.get("key") or 0),
            "updated_at": membership.get("updated_at"),
        }

    def _reference(self, kind: str, value: str) -> str:
        digest = hmac.new(self._key, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()
        return f"{kind}_{digest[:16]}"


def _timestamp(value: object) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


__all__ = ["PeerOverlayObservabilityService"]
