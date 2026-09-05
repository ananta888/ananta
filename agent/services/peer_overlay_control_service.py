"""Hub-owned membership, route lease and link-ticket application service."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from agent.services.peer_overlay_cost_admission_policy import PeerOverlayCostAdmissionPolicy, PeerOverlayCostBudget
from agent.services.peer_overlay_observability_service import PeerOverlayObservabilityService
from agent.services.peer_overlay_offline_authority_policy import PeerOverlayOfflineAuthorityPolicy
from agent.services.peer_overlay_quality_policy import PeerOverlayQualityPolicy
from agent.services.peer_overlay_relay_health_policy import PeerOverlayRelayHealthPolicy
from agent.services.peer_overlay_rollout_policy import PeerOverlayRolloutPolicy
from agent.services.peer_overlay_state_store import PeerOverlayStateStore
from agent.services.peer_overlay_topology_service import PeerOverlayCandidate, PeerOverlayTopologyService
from ananta_contracts.peer_overlay import (
    MembershipEventV1,
    OverlayEpochs,
    PeerLinkTicket,
    PeerRouteLease,
    canonical_overlay_digest,
    require_overlay_id,
    utc_now,
)


class PeerOverlayDenied(RuntimeError):
    pass


class PeerOverlayControlService:
    def __init__(
        self,
        store: PeerOverlayStateStore,
        *,
        signing_key: bytes,
        hub_key_id: str,
        topology: PeerOverlayTopologyService,
        data_enabled: bool = False,
        relay_health: PeerOverlayRelayHealthPolicy | None = None,
        offline_policy: PeerOverlayOfflineAuthorityPolicy | None = None,
        quality_policy: PeerOverlayQualityPolicy | None = None,
        rollout_policy: PeerOverlayRolloutPolicy | None = None,
        cost_policy: PeerOverlayCostAdmissionPolicy | None = None,
        observability: PeerOverlayObservabilityService | None = None,
    ) -> None:
        if len(signing_key) < 32:
            raise ValueError("peer_overlay_signing_key_too_short")
        self._store = store
        self._key = bytes(signing_key)
        self._hub_key_id = require_overlay_id(hub_key_id, "hub_key_id")
        self._topology = topology
        self._relay_health = relay_health or PeerOverlayRelayHealthPolicy()
        self._offline_policy = offline_policy or PeerOverlayOfflineAuthorityPolicy()
        self._quality_policy = quality_policy or PeerOverlayQualityPolicy()
        self._rollout = rollout_policy or PeerOverlayRolloutPolicy.from_mapping(
            None, legacy_data_enabled=bool(data_enabled)
        )
        self._data_enabled = bool(data_enabled or self._rollout.matrix()["data_overlay"]["effective"])
        self._cost_policy = cost_policy or _conservative_cost_policy()
        self._observability = observability or PeerOverlayObservabilityService(self._key)

    def change_membership(
        self,
        *,
        tenant_id: str,
        room_id: str,
        action: str,
        subject_peer_id: str,
        expected_revision: int,
        replacement_peer_id: str | None = None,
    ) -> dict[str, Any]:
        tenant = require_overlay_id(tenant_id, "tenant_id")
        room = require_overlay_id(room_id, "room_id")
        subject = require_overlay_id(subject_peer_id, "subject_peer_id")
        membership_key = _scope_key(tenant, room)
        current = self._store.get("membership", membership_key)
        if (int(current.get("revision") or 0) if current else 0) != int(expected_revision):
            raise PeerOverlayDenied("peer_overlay_membership_revision_stale")
        if action != "device_replace" and replacement_peer_id is not None:
            raise ValueError("peer_overlay_membership_replacement_unexpected")
        members = set(current.get("member_ids") or []) if current else set()
        if action == "join":
            members.add(subject)
        elif action in {"leave", "revoke"}:
            if subject not in members:
                raise PeerOverlayDenied("peer_overlay_member_not_active")
            members.remove(subject)
        elif action == "device_replace":
            replacement = require_overlay_id(replacement_peer_id, "replacement_peer_id")
            if subject not in members:
                raise PeerOverlayDenied("peer_overlay_member_not_active")
            if replacement in members:
                raise PeerOverlayDenied("peer_overlay_replacement_already_active")
            members.remove(subject)
            members.add(replacement)
        elif action != "snapshot":
            raise ValueError("peer_overlay_membership_action_invalid")
        previous_epochs = _epochs(current.get("epochs")) if current else None
        epochs = (
            OverlayEpochs(1, 1, 1, 1)
            if previous_epochs is None
            else OverlayEpochs(
                previous_epochs.membership + 1,
                previous_epochs.key + 1,
                previous_epochs.route,
                previous_epochs.topology,
            )
        )
        now = datetime.now(timezone.utc)
        event = MembershipEventV1(
            version=1,
            event_id=f"pome_{uuid.uuid4().hex}",
            tenant_id=tenant,
            room_id=room,
            sequence=int(current.get("sequence") or 0) + 1 if current else 1,
            previous_digest=str(current.get("event_digest")) if current else None,
            action=action,
            subject_peer_id=subject,
            member_ids=tuple(sorted(members)),
            epochs=epochs,
            issued_at=_iso(now),
            expires_at=_iso(now + timedelta(minutes=10)),
            hub_key_id=self._hub_key_id,
            replacement_peer_id=replacement_peer_id if action == "device_replace" else None,
        ).sign(self._key)
        payload = {
            **event.unsigned(),
            "signature": event.signature,
            "event_digest": event.event_digest,
            "updated_at": utc_now(),
        }
        return self._store.append("membership", membership_key, payload, expected_revision=expected_revision)

    def plan_publication(
        self,
        *,
        tenant_id: str,
        room_id: str,
        publication_id: str,
        source_peer_id: str,
        candidates: list[Mapping[str, Any]],
        expected_revision: int = 0,
        browser_id: str | None = None,
        cost_observation: Mapping[str, Any] | None = None,
        strict_e2ee_ready: bool = True,
    ) -> dict[str, Any]:
        self._require_data_enabled()
        rollout = self._rollout.evaluate(
            "data_overlay",
            tenant_id=tenant_id,
            room_id=room_id,
            publication_id=publication_id,
            browser_id=browser_id,
        )
        if not rollout.allowed:
            raise PeerOverlayDenied(rollout.reason_code)
        membership = self._require_membership(tenant_id, room_id)
        candidate_ids = {require_overlay_id(item.get("peer_id"), "peer_id") for item in candidates}
        if not candidate_ids.issubset(set(membership["member_ids"])) or source_peer_id not in candidate_ids:
            raise PeerOverlayDenied("peer_overlay_candidate_not_member")
        plan_key = _scope_key(tenant_id, room_id, publication_id)
        prior = self._store.get("publication_plan", plan_key)
        if (int(prior.get("revision") or 0) if prior else 0) != int(expected_revision):
            raise PeerOverlayDenied("peer_overlay_plan_revision_stale")
        current = _epochs(membership["epochs"])
        previous_plan = _epochs(prior["epochs"]) if prior else current
        epochs = OverlayEpochs(
            current.membership,
            current.key,
            max(current.route, previous_plan.route) + 1,
            max(current.topology, previous_plan.topology) + 1,
        )
        plan = self._topology.plan(
            tenant_id=tenant_id,
            room_id=room_id,
            publication_id=publication_id,
            source_peer_id=source_peer_id,
            candidates=[PeerOverlayCandidate(**dict(item)) for item in candidates],
            epochs=epochs,
        )
        candidate_by_id = {
            str(item["peer_id"]): PeerOverlayCandidate(**dict(item)) for item in candidates
        }
        turn_edges = sum(
            "turn" in {str(capability) for capability in lease.capabilities} for lease in plan.leases
        )
        relay_leases = [lease for lease in plan.leases if lease.primary_parent_id != source_peer_id]
        relay_parents = [candidate_by_id[lease.primary_parent_id] for lease in relay_leases]
        cost_decision = self._cost_policy.evaluate(
            tenant_id=tenant_id,
            turn_edges=turn_edges,
            peer_relay_edges=len(relay_leases),
            observation=cost_observation,
            strict_e2ee_ready=strict_e2ee_ready,
            relay_consent_complete=all(candidate.relay_consent for candidate in relay_parents),
            minimum_quality_met=all(candidate.eligible_relay for candidate in relay_parents),
        )
        if not cost_decision.allowed:
            raise PeerOverlayDenied(cost_decision.reason_code)
        payload = {
            **plan.as_dict(),
            "tenant_id": tenant_id,
            "room_id": room_id,
            "epochs": asdict(epochs),
            "media_forwarding_allowed": False,
            "fallback": "livekit_e2ee",
            "created_at": utc_now(),
            "cost_admission": cost_decision.as_dict(),
        }
        payload["plan_digest"] = canonical_overlay_digest(payload)
        return self._store.append("publication_plan", plan_key, payload, expected_revision=expected_revision)

    def issue_link_ticket(
        self,
        *,
        tenant_id: str,
        room_id: str,
        publication_id: str,
        lease_id: str,
        parent_kind: str = "primary",
        ice_policy: str = "all",
    ) -> dict[str, Any]:
        self._require_data_enabled()
        plan = self._require_plan(tenant_id, room_id, publication_id)
        lease = self._lease(plan, lease_id)
        if parent_kind not in {"primary", "backup"}:
            raise ValueError("peer_overlay_parent_kind_invalid")
        responder = lease.primary_parent_id if parent_kind == "primary" else lease.backup_parent_id
        if responder is None:
            raise PeerOverlayDenied("peer_overlay_backup_parent_unavailable")
        now = datetime.now(timezone.utc)
        ticket = PeerLinkTicket(
            version=1,
            ticket_id=f"polt_{uuid.uuid4().hex}",
            lease_id=lease.lease_id,
            tenant_id=lease.tenant_id,
            room_id=lease.room_id,
            publication_id=lease.publication_id,
            initiator_peer_id=lease.child_peer_id,
            responder_peer_id=responder,
            route_epoch=lease.epochs.route,
            ice_policy=ice_policy,
            nonce=uuid.uuid4().hex,
            issued_at=_iso(now),
            expires_at=_iso(now + timedelta(seconds=30)),
        ).sign(self._key)
        payload = {**ticket.unsigned(), "signature": ticket.signature, "consumed": False}
        return self._store.append("link_ticket", ticket.ticket_id, payload, expected_revision=0)

    def consume_link_ticket(self, *, ticket: Mapping[str, Any], local_peer_id: str) -> dict[str, Any]:
        self._require_data_enabled()
        parsed = _ticket(ticket)
        stored = self._store.get("link_ticket", parsed.ticket_id)
        if not stored or any(stored.get(key) != value for key, value in parsed.unsigned().items()):
            raise PeerOverlayDenied("peer_overlay_ticket_not_hub_issued")
        plan = self._require_plan(parsed.tenant_id, parsed.room_id, parsed.publication_id)
        lease = self._lease(plan, parsed.lease_id)
        parsed.verify(self._key, now=utc_now(), expected_lease=lease)
        if require_overlay_id(local_peer_id, "local_peer_id") not in {
            parsed.initiator_peer_id,
            parsed.responder_peer_id,
        }:
            raise PeerOverlayDenied("peer_overlay_ticket_peer_mismatch")
        consumed = self._store.consume_ticket(
            parsed.ticket_id,
            {"ticket_id": parsed.ticket_id, "local_peer_id": local_peer_id, "consumed_at": utc_now()},
        )
        return {
            "accepted": True,
            "ticket_id": parsed.ticket_id,
            "edge": [parsed.initiator_peer_id, parsed.responder_peer_id],
            **consumed,
        }

    def request_automatic_failover(
        self,
        *,
        tenant_id: str,
        room_id: str,
        publication_id: str,
        lease_id: str,
        observations: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        self._require_data_enabled()
        membership = self._require_membership(tenant_id, room_id)
        plan = self._require_plan(tenant_id, room_id, publication_id)
        lease = self._lease(plan, lease_id)
        members = set(membership["member_ids"])
        if any(item.get("observer_peer_id") not in members for item in observations):
            raise PeerOverlayDenied("peer_overlay_observer_not_member")
        failover_key = _scope_key(tenant_id, room_id, publication_id, lease_id)
        previous = self._store.get("failover", failover_key)
        now = datetime.now(timezone.utc)
        now_ms = int(now.timestamp() * 1_000)
        decision = self._relay_health.evaluate(
            lease=lease,
            observations=observations,
            now_ms=now_ms,
            last_failover_at_ms=int(previous["failover_at_ms"]) if previous else None,
        )
        if not decision["switch_to_backup"]:
            return {**decision, "ticket": None}
        audit = self._store.append(
            "failover",
            failover_key,
            {
                "tenant_id": tenant_id,
                "room_id": room_id,
                "publication_id": publication_id,
                "lease_id": lease_id,
                "route_epoch": lease.epochs.route,
                "from_peer_id": lease.primary_parent_id,
                "to_peer_id": lease.backup_parent_id,
                "failover_at_ms": now_ms,
                "reason_code": decision["reason_code"],
                "affected_traffic_classes": decision["affected_traffic_classes"],
            },
            expected_revision=int(previous.get("revision") or 0) if previous else 0,
        )
        ticket = self.issue_link_ticket(
            tenant_id=tenant_id,
            room_id=room_id,
            publication_id=publication_id,
            lease_id=lease_id,
            parent_kind="backup",
        )
        return {**decision, "audit_revision": audit["revision"], "ticket": ticket}

    def aggregate_quality(
        self,
        *,
        tenant_id: str,
        room_id: str,
        publication_id: str,
        relay_peer_id: str,
        route_epoch: int,
        observations: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        self._require_data_enabled()
        membership = self._require_membership(tenant_id, room_id)
        plan = self._require_plan(tenant_id, room_id, publication_id)
        members = set(membership["member_ids"])
        relay = require_overlay_id(relay_peer_id, "relay_peer_id")
        if relay not in members or route_epoch != int(plan["epochs"]["route"]):
            raise PeerOverlayDenied("peer_quality_scope_mismatch")
        allowed = {
            "observer_peer_id",
            "relay_peer_id",
            "route_epoch",
            "observed_at_ms",
            "sample_count",
            "link_state",
            "relay_delivery_ratio",
            "end_to_end_delay_ms",
        }
        accepted: list[dict[str, Any]] = []
        for raw in observations:
            if set(raw) != allowed or raw.get("observer_peer_id") not in members:
                raise PeerOverlayDenied("peer_quality_observation_scope_mismatch")
            accepted.append({**dict(raw), "validation": "hub-quality-observation-accepted-v1"})
        return self._quality_policy.aggregate(
            relay_peer_id=relay,
            route_epoch=route_epoch,
            observations=accepted,
            now_ms=int(datetime.now(timezone.utc).timestamp() * 1_000),
        )

    def offline_authority(
        self,
        *,
        tenant_id: str,
        room_id: str,
        security_profile: str = "strict",
        grace_seconds: int | None = None,
    ) -> dict[str, Any]:
        tenant = require_overlay_id(tenant_id, "tenant_id")
        room = require_overlay_id(room_id, "room_id")
        membership = self._store.get("membership", _scope_key(tenant, room))
        if not membership:
            raise KeyError("peer_overlay_membership_not_found")
        profile = self._offline_policy.resolve(security_profile, grace_seconds)
        now = datetime.now(timezone.utc)
        return {
            "tenant_id": tenant,
            "room_id": room,
            "epochs": membership["epochs"],
            "security_profile": profile.name,
            "maximum_grace_seconds": profile.maximum_grace_seconds,
            "offline_grace_expires_at": _iso(now + timedelta(seconds=profile.maximum_grace_seconds)),
            "new_publications_allowed": False,
            "route_changes_allowed": False,
            "peer_lease_extension_allowed": False,
            "human_intervention_required": False,
        }

    def overview(self, *, tenant_id: str | None = None, room_id: str | None = None) -> dict[str, Any]:
        projection = self._observability.project(
            plans=self._store.list("publication_plan"),
            memberships=self._store.list("membership"),
            tenant_id=tenant_id,
            room_id=room_id,
        )
        return {
            **projection,
            "media_peer_dag": "no_go",
            "data_overlay": "available"
            if self._data_enabled and projection["aggregate"]["retained_publication_count"]
            else "enabled"
            if self._data_enabled
            else "disabled",
            "rollout_matrix": self._rollout.matrix(),
            "fallback": "livekit_e2ee",
            "human_intervention_required": False,
        }

    def _require_membership(self, tenant_id: str, room_id: str) -> dict[str, Any]:
        tenant = require_overlay_id(tenant_id, "tenant_id")
        room = require_overlay_id(room_id, "room_id")
        membership = self._store.get("membership", _scope_key(tenant, room))
        if not membership:
            raise KeyError("peer_overlay_membership_not_found")
        if membership.get("tenant_id") != tenant or membership.get("room_id") != room:
            raise PeerOverlayDenied("peer_overlay_membership_scope_mismatch")
        return membership

    def _require_data_enabled(self) -> None:
        if not self._data_enabled:
            raise PeerOverlayDenied("peer_overlay_data_disabled")

    def _require_plan(self, tenant_id: str, room_id: str, publication_id: str) -> dict[str, Any]:
        plan = self._store.get(
            "publication_plan",
            _scope_key(
                require_overlay_id(tenant_id, "tenant_id"),
                require_overlay_id(room_id, "room_id"),
                require_overlay_id(publication_id, "publication_id"),
            ),
        )
        if not plan:
            raise KeyError("peer_overlay_plan_not_found")
        return plan

    @staticmethod
    def _lease(plan: Mapping[str, Any], lease_id: str) -> PeerRouteLease:
        for raw in list(plan.get("leases") or []):
            if raw.get("lease_id") == lease_id:
                return _lease(raw)
        raise KeyError("peer_overlay_lease_not_found")


def _epochs(value: Mapping[str, Any] | None) -> OverlayEpochs:
    if not value:
        raise ValueError("peer_overlay_epochs_missing")
    return OverlayEpochs(**dict(value))


def _lease(value: Mapping[str, Any]) -> PeerRouteLease:
    return PeerRouteLease(**dict(value))


def _ticket(value: Mapping[str, Any]) -> PeerLinkTicket:
    allowed = set(PeerLinkTicket.__dataclass_fields__)
    return PeerLinkTicket(**{key: item for key, item in value.items() if key in allowed})


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _scope_key(*parts: str) -> str:
    return ":".join(require_overlay_id(part, "scope") for part in parts)


def _conservative_cost_policy() -> PeerOverlayCostAdmissionPolicy:
    return PeerOverlayCostAdmissionPolicy(
        default_budget=PeerOverlayCostBudget(
            profile_id="conservative-unverified-v1",
            version="1.0.0",
            evidence_revision="unverified-v1",
            evidence_scope="unverified",
            browser="unknown",
            hardware_class="unknown",
            network_profile="unknown",
            measurement_duration_seconds=0,
            window_seconds=60,
            max_turn_edges=0,
            max_peer_relay_edges=0,
            max_turn_egress_bytes=0,
            max_peer_relay_egress_bytes=0,
            reserved_turn_egress_bytes_per_edge=1_048_576,
            reserved_peer_relay_egress_bytes_per_edge=1_048_576,
        )
    )


__all__ = ["PeerOverlayControlService", "PeerOverlayDenied"]
