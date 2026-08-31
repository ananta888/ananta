"""Hub-owned, fail-closed admission for the optional ordinary-media SFU."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

import jwt

from agent.repositories.semantic_sfu_admission_repository import (
    InMemorySfuAdmissionStateRepository,
    SemanticSfuAdmissionRepositoryError,
    SfuAdmissionStatePort,
    SfuRoomState,
    SqlSfuAdmissionStateRepository,
)
from agent.services.media_topology_policy import (
    MediaTopologyContext,
    MediaTopologyDecision,
    MediaTopologyPolicy,
)
from agent.services.semantic_fanout_coordination_service import (
    ReceiverRouteRequest,
    SemanticFanoutCoordinationService,
    SemanticFanoutPlan,
)
from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditError,
    SemanticMediaAuditEvent,
    SemanticMediaAuditPort,
)
from agent.services.semantic_sfu_admission_validation import (
    SfuAdmissionError,
)
from agent.services.semantic_sfu_admission_validation import (
    bounded_id as _id,
)
from agent.services.semantic_sfu_admission_validation import (
    exact_bool as _exact_bool,
)
from agent.services.semantic_sfu_admission_validation import (
    mutation_context as _mutation_context,
)
from agent.services.semantic_sfu_admission_validation import (
    nonnegative_int as _nonnegative_int,
)
from agent.services.semantic_sfu_admission_validation import (
    positive_int as _positive_int,
)
from agent.services.semantic_sfu_admission_validation import (
    publication_limits as _limits,
)
from agent.services.semantic_sfu_admission_validation import (
    request_digest as _digest,
)
from agent.services.semantic_sfu_admission_validation import (
    room_id as _room_id,
)
from agent.services.semantic_sfu_admission_validation import (
    valid_id as _valid_id,
)
from agent.services.sfu_broadcast_capacity_profile_resolver import (
    SfuBroadcastCapacityProfileError,
    SfuBroadcastCapacityProfilePort,
    get_sfu_broadcast_capacity_profile_resolver,
)
from agent.services.sfu_broadcast_control_observability import (
    SfuBroadcastControlObservationPort,
    control_observer_or_null,
    observed_control_path,
)
from agent.services.sfu_vendor_identity_service import SfuVendorIdentityService
from agent.services.share_session_service import get_share_session_service
from agent.services.webrtc_epoch_service import get_webrtc_epoch_service

_TOKEN_TTL_MAX_SECONDS = 60
_ALLOWED_SOURCES = frozenset({"microphone", "camera", "screen"})
_SOURCE_TO_LIVEKIT = {"microphone": "microphone", "camera": "camera", "screen": "screen_share"}


@dataclass(frozen=True)
class SfuMembership:
    tenant_id: str
    session_id: str
    participant_id: str
    role: str
    epoch: int
    permissions: frozenset[str]
    active: bool = True


class SfuMembershipPort(Protocol):
    def member(self, *, tenant_id: str, session_id: str, participant_id: str) -> SfuMembership | None: ...


class MediaTopologyPolicyPort(Protocol):
    def decide(self, context: MediaTopologyContext) -> MediaTopologyDecision: ...


class SemanticFanoutCoordinationPort(Protocol):
    def plan(
        self,
        *,
        publication_id: str,
        receivers: tuple[ReceiverRouteRequest, ...],
        private_recovery_audience: Mapping[str, bool] | None = None,
    ) -> SemanticFanoutPlan: ...


class ShareSessionSfuMembership:
    """Read-only adapter; it cannot mutate share-session or epoch state."""

    def member(self, *, tenant_id: str, session_id: str, participant_id: str) -> SfuMembership | None:
        sessions = get_share_session_service()
        item = sessions.get_session(session_id)
        if not isinstance(item, dict) or str(item.get("tenant_id") or "default") != tenant_id:
            return None
        if item.get("revoked_at") is not None:
            return None
        expires_at = item.get("expires_at")
        if isinstance(expires_at, (int, float)) and float(expires_at) <= time.time():
            return None
        owner_id = str(item.get("owner_user_id") or "")
        permissions: Mapping[str, Any]
        role = "owner" if participant_id == owner_id else "participant"
        if role == "owner":
            permissions = dict(item.get("permissions") or {})
        else:
            participant = next(
                (
                    row
                    for row in sessions.get_participants(session_id)
                    if str(row.get("user_id") or "") == participant_id and row.get("revoked_at") is None
                ),
                None,
            )
            if participant is None:
                return None
            permissions = dict(participant.get("permissions") or {})
        epoch = get_webrtc_epoch_service().current_epoch("session", session_id)
        if epoch is None:
            return None
        granted = frozenset(key for key, value in permissions.items() if value is True)
        return SfuMembership(tenant_id, session_id, participant_id, role, epoch, granted)


class SemanticSfuAdmissionService:
    """Issue narrowed LiveKit grants after Hub membership and revision checks.

    Mutable room projections and idempotency receipts are isolated behind this
    service and bounded. Persistent share-session membership/epoch remains the
    authority; losing this cache can never expand rights.
    """

    def __init__(
        self,
        membership: SfuMembershipPort,
        *,
        enabled: bool,
        public_ws_url: str,
        api_key: str,
        api_secret: str,
        token_ttl_seconds: int = 45,
        clock: Callable[[], float] = time.time,
        state_repository: SfuAdmissionStatePort | None = None,
        audit: SemanticMediaAuditPort | None = None,
        topology_policy: MediaTopologyPolicyPort | None = None,
        fanout: SemanticFanoutCoordinationPort | None = None,
        capacity_profile: SfuBroadcastCapacityProfilePort | None = None,
        expose_capacity_profile: bool = False,
        vendor_identity_service: SfuVendorIdentityService | None = None,
        control_observer: SfuBroadcastControlObservationPort | None = None,
    ) -> None:
        self._membership = membership
        self._enabled = enabled
        self._public_ws_url = public_ws_url.rstrip("/")
        self._api_key = api_key
        self._api_secret = api_secret
        self._ttl = token_ttl_seconds
        self._clock = clock
        self._control_observer = control_observer_or_null(control_observer)
        self._lock = threading.RLock()
        self._state = state_repository or InMemorySfuAdmissionStateRepository(clock=clock)
        self._audit = audit
        self._capacity_profile = capacity_profile or get_sfu_broadcast_capacity_profile_resolver()
        self._capacity_profile.resolve()
        self._expose_capacity_profile = expose_capacity_profile
        self._topology_policy = topology_policy or MediaTopologyPolicy()
        self._fanout = fanout or SemanticFanoutCoordinationService(self._capacity_profile)
        if vendor_identity_service is None:
            from agent.repositories.sfu_vendor_identity_repository import (
                InMemorySfuVendorIdentityRepository,
            )
            from agent.services.sfu_hub_secret_envelope import derive_sfu_hub_envelope

            envelope = derive_sfu_hub_envelope(
                hashlib.sha256(f"admission:{api_secret}".encode()).hexdigest(),
                key_id="sfu-vendor-test-v1",
            )
            vendor_identity_service = SfuVendorIdentityService(
                InMemorySfuVendorIdentityRepository(), envelope, clock=clock
            )
        self._vendor_identities = vendor_identity_service

    def configure_audit(self, audit: SemanticMediaAuditPort) -> None:
        """Attach the Hub audit factory even if the singleton was resolved early."""

        with self._lock:
            self._audit = audit

    def configure_topology(
        self,
        topology_policy: MediaTopologyPolicyPort,
        fanout: SemanticFanoutCoordinationPort,
    ) -> None:
        """Attach Hub policy ports without giving them transport side effects."""

        with self._lock:
            self._topology_policy = topology_policy
            self._fanout = fanout

    def configure_vendor_identities(self, service: SfuVendorIdentityService) -> None:
        """Replace only the Hub-side opaque identity authority."""

        with self._lock:
            self._vendor_identities = service

    @observed_control_path("admission")
    def join(self, request: Mapping[str, Any], *, actor_id: str, tenant_id: str) -> dict[str, Any]:
        self._require_configuration()
        session_id = _id(request.get("session_id"), "session_id")
        idempotency_key = _id(request.get("idempotency_key"), "idempotency_key")
        expected_epoch = _positive_int(request.get("membership_epoch"), "membership_epoch")
        expected_revision = _nonnegative_int(request.get("expected_revision"), "expected_revision")
        strict_e2ee = _exact_bool(request.get("strict_e2ee", True), "strict_e2ee")
        supported = _exact_bool(request.get("e2ee_supported", False), "e2ee_supported")
        if strict_e2ee and not supported:
            raise SfuAdmissionError("sfu_e2ee_capability_required", 409)
        member = self._member(tenant_id, session_id, actor_id, expected_epoch)
        canonical = {
            "session_id": session_id,
            "membership_epoch": expected_epoch,
            "expected_revision": expected_revision,
            "strict_e2ee": strict_e2ee,
            "e2ee_supported": supported,
        }
        capacity = self._capacity_profile.resolve()
        with self._lock:
            cached = self._receipt(tenant_id, session_id, actor_id, "join", idempotency_key, canonical)
            if cached is not None:
                return cached
            state = self._room(tenant_id, session_id)
            if state.revision != expected_revision:
                raise SfuAdmissionError("sfu_revision_conflict", 409)
            previous_epoch = state.participants.get(actor_id)
            known_epochs = set(state.participants.values())
            if any(value > expected_epoch for value in known_epochs):
                raise SfuAdmissionError("sfu_membership_epoch_stale", 409)
            rolls_epoch = any(value < expected_epoch for value in known_epochs)
            prospective_count = 1 if rolls_epoch else len(state.participants) + (previous_epoch is None)
            if not capacity.allows_participant_count(prospective_count):
                raise SfuAdmissionError("capacity_cap_exceeded", 409)
            if rolls_epoch:
                # Membership changes invalidate the complete room projection.
                # The first still-active member joining the new authoritative
                # epoch performs the fail-closed rollover; stale LiveKit grants
                # remain cryptographically fenced by the mandatory group rekey.
                self._vendor_identities.revoke_room_before_epoch(
                    tenant_id=tenant_id,
                    room_id=_room_id(tenant_id, session_id),
                    membership_epoch=expected_epoch,
                    fencing_token=max(1, state.revision + 1),
                )
                state.participants.clear()
                state.publications.clear()
                state.subscriptions.clear()
                previous_epoch = None
            if previous_epoch is not None and previous_epoch != expected_epoch:
                raise SfuAdmissionError("sfu_membership_epoch_stale", 409)
            state.participants[actor_id] = expected_epoch
            state.revision += 1
            result = self._token_result(member, state, sources=(), can_subscribe=False)
            return self._commit_room_with_receipt(
                tenant_id,
                session_id,
                actor_id,
                "join",
                idempotency_key,
                canonical,
                expected_revision,
                state,
                result,
            )

    def read_state(
        self,
        *,
        session_id: str,
        membership_epoch: int,
        actor_id: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        """Return the caller's bounded room projection without issuing a token.

        This read endpoint lets a restarted browser resume with the current
        CAS revision.  It cannot enumerate other participants or expand any
        publication/subscription grant.
        """

        normalized_session = _id(session_id, "session_id")
        epoch = _positive_int(membership_epoch, "membership_epoch")
        room_id = _room_id(tenant_id, normalized_session)
        self._require_configuration()
        self._member(tenant_id, normalized_session, actor_id, epoch)
        with self._lock:
            state = self._state.load(tenant_id, normalized_session)
            if state is None:
                return self._with_capacity_profile({
                    "ok": True,
                    "room_id": room_id,
                    "membership_epoch": epoch,
                    "revision": 0,
                    "joined": False,
                    "publications": [],
                    "subscriptions": [],
                }, room_id)
            publications = [
                json.loads(json.dumps(row))
                for row in state.publications.values()
                if row["participant_id"] == actor_id and row["status"] != "revoked"
            ]
            subscriptions = [
                json.loads(json.dumps(row))
                for row in state.subscriptions.values()
                if row["subscriber_id"] == actor_id and row["status"] != "revoked"
            ]
            return self._with_capacity_profile({
                "ok": True,
                "room_id": room_id,
                "membership_epoch": epoch,
                "revision": state.revision,
                "joined": state.participants.get(actor_id) == epoch,
                "publications": sorted(publications, key=lambda row: row["publication_id"]),
                "subscriptions": sorted(subscriptions, key=lambda row: row["subscription_id"]),
            }, room_id)

    @observed_control_path("admission")
    def authorize_publication(self, request: Mapping[str, Any], *, actor_id: str, tenant_id: str) -> dict[str, Any]:
        self._require_configuration()
        session_id, idempotency_key, epoch, expected_revision = _mutation_context(request)
        member = self._member(tenant_id, session_id, actor_id, epoch)
        publication_id = _id(request.get("publication_id"), "publication_id")
        source = str(request.get("source") or "")
        if source not in _ALLOWED_SOURCES:
            raise SfuAdmissionError("sfu_publication_source_invalid")
        kind = str(request.get("kind") or "")
        if kind != ("audio" if source == "microphone" else "video"):
            raise SfuAdmissionError("sfu_publication_kind_invalid")
        privacy = str(request.get("privacy") or "ordinary")
        if privacy not in {"ordinary", "private_recovery"}:
            raise SfuAdmissionError("sfu_publication_privacy_invalid")
        audience = request.get("audience_participant_id")
        audience_id = _id(audience, "audience_participant_id") if audience is not None else None
        required_permission = "chat" if source == "microphone" else "view_tui"
        if required_permission not in member.permissions:
            raise SfuAdmissionError("sfu_publication_forbidden", 403)
        if privacy == "private_recovery" and ("artifact_share" not in member.permissions or not audience_id):
            raise SfuAdmissionError("sfu_private_recovery_forbidden", 403)
        raw_subscribers = request.get("authorized_subscriber_ids")
        if not isinstance(raw_subscribers, list):
            raise SfuAdmissionError("sfu_publication_subscribers_invalid")
        if not self._capacity_profile.resolve().allows_receiver_count(len(raw_subscribers)):
            raise SfuAdmissionError("capacity_cap_exceeded", 409)
        subscriber_ids = sorted({_id(value, "authorized_subscriber_id") for value in raw_subscribers})
        if len(subscriber_ids) != len(raw_subscribers) or actor_id in subscriber_ids:
            raise SfuAdmissionError("sfu_publication_subscribers_invalid")
        if privacy == "private_recovery" and subscriber_ids != [audience_id]:
            raise SfuAdmissionError("sfu_private_recovery_forbidden", 403)
        for subscriber_id in subscriber_ids:
            subscriber = self._member(tenant_id, session_id, subscriber_id, epoch)
            if required_permission not in subscriber.permissions:
                raise SfuAdmissionError("sfu_publication_subscriber_forbidden", 403)
        limits = _limits(request.get("constraints"), source)
        self._authorize_topology(
            publication_id=publication_id,
            subscriber_ids=tuple(subscriber_ids),
            privacy=privacy,
            audience_id=audience_id,
        )
        canonical = {
            "session_id": session_id,
            "membership_epoch": epoch,
            "expected_revision": expected_revision,
            "publication_id": publication_id,
            "source": source,
            "kind": kind,
            "privacy": privacy,
            "audience_participant_id": audience_id,
            "authorized_subscriber_ids": subscriber_ids,
            "constraints": limits,
        }
        with self._lock:
            cached = self._receipt(tenant_id, session_id, actor_id, "publish", idempotency_key, canonical)
            if cached is not None:
                return cached
            state = self._active_room(tenant_id, session_id, actor_id, epoch, expected_revision)
            existing = state.publications.get(publication_id)
            if existing is not None and existing != canonical:
                raise SfuAdmissionError("sfu_publication_id_conflict", 409)
            state.revision += 1
            publication = {
                "schema": "ananta.webrtc.media-publication.v1",
                "publication_id": publication_id,
                "tenant_id": tenant_id,
                "room_id": _room_id(tenant_id, session_id),
                "participant_id": actor_id,
                "membership_epoch": epoch,
                "revision": state.revision,
                "source": source,
                "kind": kind,
                "privacy": privacy,
                "status": "authorized",
                "audience_participant_id": audience_id,
                "authorized_subscriber_ids": subscriber_ids,
                "constraints": limits,
            }
            state.publications[publication_id] = publication
            result = self._token_result(member, state, sources=(source,), can_subscribe=False)
            result["publication"] = publication
            return self._commit_room_with_receipt(
                tenant_id,
                session_id,
                actor_id,
                "publish",
                idempotency_key,
                canonical,
                expected_revision,
                state,
                result,
            )

    def _authorize_topology(
        self,
        *,
        publication_id: str,
        subscriber_ids: tuple[str, ...],
        privacy: str,
        audience_id: str | None,
    ) -> None:
        """Intersect one common SFU upload with the deterministic Hub policy.

        This is an admission-only calculation.  It neither opens rooms nor
        instructs a browser/SFU; the client still has to execute the admitted
        transition through its guarded transport reducer.
        """

        if not subscriber_ids:
            return
        requests = tuple(
            ReceiverRouteRequest(
                receiver_id=receiver_id,
                requested_path="ordinary",
                sfu_authorized=True,
                ordinary_authorized=True,
                semantic_authorized=False,
                semantic_capable=False,
                semantic_contract_active=False,
            )
            for receiver_id in subscriber_ids
        )
        recovery = {audience_id: True} if privacy == "private_recovery" and audience_id else None
        now_ms = max(0, int(self._clock() * 1000))
        try:
            plan = self._fanout.plan(
                publication_id=publication_id,
                receivers=requests,
                private_recovery_audience=recovery,
            )
            decision = self._topology_policy.decide(
                MediaTopologyContext(
                    current="ordinary_sfu",
                    participant_count=1 + len(subscriber_ids),
                    now_ms=now_ms,
                    last_transition_ms=now_ms,
                    ordinary_direct_healthy=True,
                    ordinary_sfu_healthy=True,
                    sfu_enabled=self._enabled,
                    sfu_admitted=True,
                    sfu_e2ee_ready=True,
                    semantic_contract_active=False,
                    semantic_quality_healthy=False,
                    relay_control_available=True,
                    user_override="sfu",
                )
            )
        except (TypeError, ValueError) as exc:
            raise SfuAdmissionError("sfu_topology_policy_denied", 409) from exc
        planned_ids = tuple(route.receiver_id for route in plan.routes)
        if (
            planned_ids != tuple(sorted(subscriber_ids))
            or plan.upload_count != 1
            or any(route.path != "ordinary_sfu" for route in plan.routes)
            or decision.target != "ordinary_sfu"
            or decision.bulk_path_count != 1
        ):
            raise SfuAdmissionError("sfu_topology_policy_denied", 409)
        if privacy == "private_recovery" and (
            audience_id is None
            or sum(route.private_recovery_authorized for route in plan.routes) != 1
        ):
            raise SfuAdmissionError("sfu_private_recovery_forbidden", 403)

    @observed_control_path("admission")
    def authorize_subscription(self, request: Mapping[str, Any], *, actor_id: str, tenant_id: str) -> dict[str, Any]:
        self._require_configuration()
        session_id, idempotency_key, epoch, expected_revision = _mutation_context(request)
        member = self._member(tenant_id, session_id, actor_id, epoch)
        subscription_id = _id(request.get("subscription_id"), "subscription_id")
        publication_id = _id(request.get("publication_id"), "publication_id")
        canonical = {
            "session_id": session_id,
            "membership_epoch": epoch,
            "expected_revision": expected_revision,
            "subscription_id": subscription_id,
            "publication_id": publication_id,
        }
        with self._lock:
            cached = self._receipt(tenant_id, session_id, actor_id, "subscribe", idempotency_key, canonical)
            if cached is not None:
                return cached
            state = self._active_room(tenant_id, session_id, actor_id, epoch, expected_revision)
            publication = state.publications.get(publication_id)
            if publication is None or publication["status"] == "revoked":
                raise SfuAdmissionError("sfu_publication_not_available", 404)
            audience = publication.get("audience_participant_id")
            if publication["privacy"] == "private_recovery" and audience != actor_id:
                raise SfuAdmissionError("sfu_private_recovery_forbidden", 403)
            if actor_id not in publication["authorized_subscriber_ids"]:
                raise SfuAdmissionError("sfu_subscription_forbidden", 403)
            required = "chat" if publication["source"] == "microphone" else "view_tui"
            if required not in member.permissions:
                raise SfuAdmissionError("sfu_subscription_forbidden", 403)
            state.revision += 1
            subscription = {
                "schema": "ananta.webrtc.media-subscription.v1",
                "subscription_id": subscription_id,
                "tenant_id": tenant_id,
                "room_id": _room_id(tenant_id, session_id),
                "subscriber_id": actor_id,
                "publisher_id": publication["participant_id"],
                "publication_id": publication_id,
                "membership_epoch": epoch,
                "revision": state.revision,
                "status": "authorized",
            }
            state.subscriptions[subscription_id] = subscription
            result = self._token_result(member, state, sources=(), can_subscribe=True)
            result["subscription"] = subscription
            return self._commit_room_with_receipt(
                tenant_id,
                session_id,
                actor_id,
                "subscribe",
                idempotency_key,
                canonical,
                expected_revision,
                state,
                result,
            )

    def publication_for_group_key(
        self,
        *,
        session_id: str,
        membership_epoch: int,
        publication_id: str,
        actor_id: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        """Return one current publisher-owned grant for group-key authorization.

        This read does not issue a token or mutate admission.  It deliberately
        revalidates every audience member against the authoritative share
        session so a stale publication can never bootstrap a new key epoch.
        """

        self._require_configuration()
        normalized_session = _id(session_id, "session_id")
        epoch = _positive_int(membership_epoch, "membership_epoch")
        normalized_publication = _id(publication_id, "publication_id")
        self._member(tenant_id, normalized_session, actor_id, epoch)
        with self._lock:
            state = self._state.load(tenant_id, normalized_session)
            publication = state.publications.get(normalized_publication) if state else None
            if (
                publication is None
                or publication.get("status") == "revoked"
                or publication.get("participant_id") != actor_id
            ):
                raise SfuAdmissionError("sfu_publication_not_available", 404)
            if publication.get("membership_epoch") != epoch:
                raise SfuAdmissionError("sfu_membership_epoch_stale", 409)
            subscribers = list(publication.get("authorized_subscriber_ids") or [])
        if not subscribers:
            raise SfuAdmissionError("sfu_group_size_invalid", 409)
        if not self._capacity_profile.resolve().allows_receiver_count(len(subscribers)):
            raise SfuAdmissionError("capacity_cap_exceeded", 409)
        for subscriber_id in subscribers:
            self._member(tenant_id, normalized_session, str(subscriber_id), epoch)
        return json.loads(json.dumps(publication))

    @observed_control_path("admission")
    def leave(self, request: Mapping[str, Any], *, actor_id: str, tenant_id: str) -> dict[str, Any]:
        session_id, idempotency_key, epoch, expected_revision = _mutation_context(request)
        self._member(tenant_id, session_id, actor_id, epoch)
        canonical = {"session_id": session_id, "membership_epoch": epoch, "expected_revision": expected_revision}
        with self._lock:
            cached = self._receipt(tenant_id, session_id, actor_id, "leave", idempotency_key, canonical)
            if cached is not None:
                return cached
            state = self._active_room(tenant_id, session_id, actor_id, epoch, expected_revision)
            state.participants.pop(actor_id, None)
            for publication in state.publications.values():
                if publication["participant_id"] == actor_id:
                    publication["status"] = "revoked"
            for subscription in state.subscriptions.values():
                if subscription["subscriber_id"] == actor_id or subscription["publisher_id"] == actor_id:
                    subscription["status"] = "revoked"
            state.revision += 1
            self._vendor_identities.revoke_membership(
                tenant_id=tenant_id,
                room_id=_room_id(tenant_id, session_id),
                membership_ref=actor_id,
                fencing_token=max(1, state.revision),
            )
            result = {
                "ok": True,
                "room_id": _room_id(tenant_id, session_id),
                "revision": state.revision,
                "reason_code": "sfu_participant_left",
            }
            return self._commit_room_with_receipt(
                tenant_id,
                session_id,
                actor_id,
                "leave",
                idempotency_key,
                canonical,
                expected_revision,
                state,
                result,
            )

    def _member(self, tenant_id: str, session_id: str, actor_id: str, epoch: int) -> SfuMembership:
        member = self._membership.member(tenant_id=tenant_id, session_id=session_id, participant_id=actor_id)
        if member is None or not member.active:
            raise SfuAdmissionError("sfu_membership_required", 403)
        if member.epoch != epoch:
            raise SfuAdmissionError("sfu_membership_epoch_stale", 409)
        return member

    def _active_room(self, tenant: str, session: str, actor: str, epoch: int, revision: int) -> SfuRoomState:
        state = self._state.load(tenant, session)
        if state is None or state.participants.get(actor) != epoch:
            raise SfuAdmissionError("sfu_join_required", 409)
        if state.revision != revision:
            raise SfuAdmissionError("sfu_revision_conflict", 409)
        return state

    def _token_result(
        self, member: SfuMembership, state: SfuRoomState, *, sources: tuple[str, ...], can_subscribe: bool
    ) -> dict[str, Any]:
        now = int(self._clock())
        room = _room_id(member.tenant_id, member.session_id)
        expires = now + self._ttl
        publications = sorted(
            row["publication_id"]
            for row in state.publications.values()
            if row["participant_id"] == member.participant_id and row["status"] != "revoked"
        )
        subscriptions = sorted(
            row["subscription_id"]
            for row in state.subscriptions.values()
            if row["subscriber_id"] == member.participant_id and row["status"] != "revoked"
        )
        active_sources = sorted(
            {
                *sources,
                *(
                    str(row["source"])
                    for row in state.publications.values()
                    if row["participant_id"] == member.participant_id and row["status"] != "revoked"
                ),
            }
        )
        vendor_identity = self._vendor_identities.issue_identity(
            tenant_id=member.tenant_id,
            room_id=room,
            membership_ref=member.participant_id,
            membership_epoch=member.epoch,
            identity_epoch=member.epoch,
            ttl_seconds=max(60, min(600, self._ttl * 4)),
            fencing_token=max(1, state.revision),
        )
        authorized_subscribers = sorted({
            str(subscriber_id)
            for publication in state.publications.values()
            if publication["participant_id"] == member.participant_id
            and publication["status"] != "revoked"
            for subscriber_id in publication.get("authorized_subscriber_ids") or ()
        })
        subscriber_vendor_identities = {
            subscriber_id: self._vendor_identities.issue_identity(
                tenant_id=member.tenant_id,
                room_id=room,
                membership_ref=subscriber_id,
                membership_epoch=member.epoch,
                identity_epoch=member.epoch,
                ttl_seconds=max(60, min(600, self._ttl * 4)),
                fencing_token=max(1, state.revision),
            ).identity_handle
            for subscriber_id in authorized_subscribers
        }
        claims = {
            "iss": self._api_key,
            "sub": vendor_identity.identity_handle,
            "nbf": now - 1,
            "exp": expires,
            "jti": uuid.uuid4().hex,
            "video": {
                "roomJoin": True,
                "room": room,
                "canPublish": bool(active_sources),
                "canSubscribe": can_subscribe,
                "canPublishData": False,
                "canPublishSources": [_SOURCE_TO_LIVEKIT[source] for source in active_sources],
                "roomAdmin": False,
                "roomRecord": False,
            },
            "ananta_sfu": {
                "room_id": room,
                "vendor_identity": vendor_identity.identity_handle,
                "membership_epoch": member.epoch,
                "identity_epoch": vendor_identity.identity_epoch,
                "revision": state.revision,
                "publication_ids": publications,
                "subscription_ids": subscriptions,
                "lease_authority": False,
            },
        }
        return self._with_capacity_profile({
            "ok": True,
            "server_url": self._public_ws_url,
            "access_token": jwt.encode(claims, self._api_secret, algorithm="HS256"),
            "expires_at": expires,
            "room_id": room,
            "livekit_identity": vendor_identity.identity_handle,
            "authorized_subscriber_livekit_identities": subscriber_vendor_identities,
            "membership_epoch": member.epoch,
            "revision": state.revision,
        }, room)

    def _require_configuration(self) -> None:
        try:
            self._capacity_profile.resolve()
        except SfuBroadcastCapacityProfileError as exc:
            raise SfuAdmissionError("sfu_capacity_configuration_invalid", 503) from exc
        if not self._enabled:
            raise SfuAdmissionError("sfu_disabled", 503)
        if not self._public_ws_url.startswith(("ws://", "wss://")):
            raise SfuAdmissionError("sfu_configuration_invalid", 503)
        if not _valid_id(self._api_key) or len(self._api_secret.encode("utf-8")) < 32:
            raise SfuAdmissionError("sfu_configuration_invalid", 503)
        if not 5 <= self._ttl <= _TOKEN_TTL_MAX_SECONDS:
            raise SfuAdmissionError("sfu_configuration_invalid", 503)

    def _with_capacity_profile(self, result: dict[str, Any], room_id: str) -> dict[str, Any]:
        if self._expose_capacity_profile:
            result["capacity_profile"] = self._capacity_profile.resolve().public_contract(room_id=room_id)
        return result

    def _room(self, tenant: str, session: str) -> SfuRoomState:
        return self._state.load(tenant, session) or SfuRoomState()

    def _commit_room_with_receipt(
        self,
        tenant: str,
        session: str,
        actor: str,
        operation: str,
        key: str,
        request: Mapping[str, Any],
        expected_revision: int,
        state: SfuRoomState,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        audit_event = self._prepare_audit_event(
            tenant=tenant,
            session=session,
            operation=operation,
            idempotency_key=key,
            epoch=int(request["membership_epoch"]),
        )
        expires_at = min(
            float(result.get("expires_at") or self._clock() + 120),
            self._clock() + 120,
        )
        try:
            committed = self._state.commit_mutation(
                tenant,
                session,
                expected_revision=expected_revision,
                state=state,
                actor_id=actor,
                operation=operation,
                idempotency_key=key,
                request_digest=_digest(request),
                result=result,
                expires_at=expires_at,
                audit_event=audit_event,
            )
        except SemanticSfuAdmissionRepositoryError as exc:
            raise SfuAdmissionError(exc.reason_code, 409) from exc
        except SemanticMediaAuditError as exc:
            raise SfuAdmissionError(exc.reason_code, exc.status_code) from exc
        if committed.status == "conflict" or committed.result is None:
            raise SfuAdmissionError("sfu_revision_conflict", 409)
        return json.loads(json.dumps(committed.result))

    def _prepare_audit_event(
        self,
        *,
        tenant: str,
        session: str,
        operation: str,
        idempotency_key: str,
        epoch: int,
    ) -> SemanticMediaAuditEvent | None:
        if self._audit is None:
            return None
        transitions = {
            "join": ("joined", "sfu_membership_confirmed"),
            "publish": ("publication_authorized", "sfu_policy_confirmed"),
            "subscribe": ("subscription_authorized", "sfu_policy_confirmed"),
            "leave": ("left", "sfu_participant_left"),
        }
        transition = transitions.get(operation)
        if transition is None:
            raise SfuAdmissionError("sfu_operation_invalid")
        try:
            return self._audit.prepare_transition(
                idempotency_key=f"semantic-sfu:{operation}:{idempotency_key}",
                tenant_id=tenant,
                scope=f"semantic-sfu:{session}",
                event_type="semantic_admission",
                transition=transition[0],
                reason_code=transition[1],
                epoch=epoch,
                contract_ref=f"sfu-room:{tenant}:{session}",
            )
        except Exception as exc:
            raise SfuAdmissionError("semantic_audit_unavailable", 503) from exc

    def _receipt(
        self,
        tenant: str,
        session: str,
        actor: str,
        operation: str,
        key: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        found = self._state.get_receipt(tenant, session, actor, operation, key)
        if found is None:
            return None
        digest = _digest(request)
        if found.request_digest != digest:
            raise SfuAdmissionError("sfu_idempotency_conflict", 409)
        return json.loads(json.dumps(found.result))


_SERVICE: SemanticSfuAdmissionService | None = None
_AUDIT: SemanticMediaAuditPort | None = None
_TOPOLOGY_POLICY: MediaTopologyPolicyPort | None = None
_FANOUT: SemanticFanoutCoordinationPort | None = None
_VENDOR_IDENTITIES: SfuVendorIdentityService | None = None


def get_semantic_sfu_admission_service() -> SemanticSfuAdmissionService:
    global _SERVICE
    if _SERVICE is None:
        enabled = os.environ.get("ANANTA_SEMANTIC_MEDIA_SFU_ENABLED", "false").lower() in {"1", "true", "yes"}
        raw_ttl = os.environ.get("ANANTA_SEMANTIC_MEDIA_SFU_TOKEN_TTL_SECONDS", "45")
        try:
            ttl = int(raw_ttl)
        except ValueError:
            ttl = 0
        _SERVICE = SemanticSfuAdmissionService(
            ShareSessionSfuMembership(),
            enabled=enabled,
            public_ws_url=os.environ.get("ANANTA_SEMANTIC_MEDIA_SFU_PUBLIC_WS_URL", ""),
            api_key=os.environ.get("ANANTA_SEMANTIC_MEDIA_SFU_API_KEY", ""),
            api_secret=os.environ.get("ANANTA_SEMANTIC_MEDIA_SFU_API_SECRET", ""),
            token_ttl_seconds=ttl,
            state_repository=SqlSfuAdmissionStateRepository(),
            audit=_AUDIT,
            topology_policy=_TOPOLOGY_POLICY,
            fanout=_FANOUT,
            vendor_identity_service=_VENDOR_IDENTITIES,
            expose_capacity_profile=True,
        )
    return _SERVICE


def configure_semantic_sfu_admission_audit(audit: SemanticMediaAuditPort) -> None:
    """Configure the Hub audit command factory for current and future service instances."""

    global _AUDIT
    _AUDIT = audit
    if _SERVICE is not None:
        _SERVICE.configure_audit(audit)


def configure_semantic_sfu_topology(
    topology_policy: MediaTopologyPolicyPort,
    fanout: SemanticFanoutCoordinationPort,
) -> None:
    """Configure the productive Hub-only admission policy composition."""

    global _FANOUT, _TOPOLOGY_POLICY
    _TOPOLOGY_POLICY = topology_policy
    _FANOUT = fanout
    if _SERVICE is not None:
        _SERVICE.configure_topology(topology_policy, fanout)


def configure_semantic_sfu_vendor_identities(service: SfuVendorIdentityService) -> None:
    """Install the persistent Hub identity authority for current and future instances."""

    global _VENDOR_IDENTITIES
    _VENDOR_IDENTITIES = service
    if _SERVICE is not None:
        _SERVICE.configure_vendor_identities(service)


__all__ = [
    "SemanticSfuAdmissionService",
    "SfuAdmissionError",
    "SfuMembership",
    "SfuMembershipPort",
    "ShareSessionSfuMembership",
    "configure_semantic_sfu_admission_audit",
    "configure_semantic_sfu_topology",
    "configure_semantic_sfu_vendor_identities",
    "get_semantic_sfu_admission_service",
]
