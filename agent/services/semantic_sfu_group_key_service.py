"""Hub-authorized SFU group-key epochs with opaque per-member delivery.

The Hub signs membership metadata and routes encrypted packages.  Content-key
bytes are created, wrapped and consumed only by clients and never cross this
service boundary in a plaintext field.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping, Protocol

from agent.config import settings
from agent.repositories.sfu_broadcast_group_key_repository import (
    InMemorySfuBroadcastGroupKeyRepository,
    SfuBroadcastGroupKeyRepositoryError,
    SqlSfuBroadcastGroupKeyRepository,
)
from agent.repositories.webrtc_epoch_repository import WebrtcEpochRepository
from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditEvent,
    SemanticMediaAuditPort,
)
from agent.services.semantic_sfu_admission_service import (
    SfuMembershipPort,
    ShareSessionSfuMembership,
    get_semantic_sfu_admission_service,
)
from agent.services.sfu_broadcast_capacity_profile_resolver import (
    SfuBroadcastCapacityProfilePort,
    get_sfu_broadcast_capacity_profile_resolver,
)
from agent.services.sfu_broadcast_control_observability import (
    SfuBroadcastControlObservationPort,
    control_observer_or_null,
    observed_control_path,
)
from agent.services.sfu_broadcast_group_key_repository_port import (
    SfuBroadcastGroupKeyRepositoryPort,
    SfuGroupKeyEpochState,
    SfuGroupKeyPackageWrite,
    SfuGroupKeyReceipt,
)
from agent.services.sfu_hub_secret_envelope import (
    SfuHubSecretEnvelopePort,
    derive_sfu_hub_envelope,
)
from agent.services.share_relay_compatibility_service import (
    ShareRelayCompatibilityService,
    get_share_relay_compatibility_service,
)
from agent.services.webrtc_epoch_service import WebrtcEpochService, get_webrtc_epoch_service
from agent.services.webrtc_group_key_authorization_service import (
    GroupKeyAuthorizationError,
    GroupKeyEpochAuthorization,
    WebrtcGroupKeyAuthorizationService,
)
from agent.services.webrtc_peer_identity_service import derive_hub_identity_key

_MAX_OPAQUE_PACKAGE_BYTES = 8 * 1024
_MIN_OPAQUE_PACKAGE_BYTES = 48
_ISSUED_LIMIT = 512
_RELAY_QUEUE_LIMIT = 32
_RELAY_TRAFFIC_CLASS = "control"


class SfuGroupKeyError(ValueError):
    def __init__(self, reason_code: str, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class SfuGroupPublicationPort(Protocol):
    def publication_for_group_key(
        self,
        *,
        session_id: str,
        membership_epoch: int,
        publication_id: str,
        actor_id: str,
        tenant_id: str,
    ) -> dict[str, Any]: ...


@dataclass
class _IssuedEpoch:
    authorization: GroupKeyEpochAuthorization
    session_id: str
    publisher_id: str
    package_refs: dict[str, str]
    delivered: set[str]
    acknowledged: set[str]


class SemanticSfuGroupKeyService:
    """Compose admission, signed epochs and the bounded opaque Relay store."""

    def __init__(
        self,
        *,
        membership: SfuMembershipPort,
        publications: SfuGroupPublicationPort,
        epochs: WebrtcEpochService,
        authorization: WebrtcGroupKeyAuthorizationService,
        relay: ShareRelayCompatibilityService,
        hub_id: str,
        clock: Callable[[], float] = time.time,
        audit: SemanticMediaAuditPort | None = None,
        capacity_profile: SfuBroadcastCapacityProfilePort | None = None,
        expose_capacity_profile: bool = False,
        repository: SfuBroadcastGroupKeyRepositoryPort | None = None,
        secret_envelope: SfuHubSecretEnvelopePort | None = None,
        control_observer: SfuBroadcastControlObservationPort | None = None,
    ) -> None:
        self._membership = membership
        self._publications = publications
        self._epochs = epochs
        self._authorization = authorization
        self._relay = relay
        self._hub_id = _identifier(hub_id, "hub_id")
        self._clock = clock
        self._audit = audit
        self._control_observer = control_observer_or_null(control_observer)
        self._capacity_profile = capacity_profile or get_sfu_broadcast_capacity_profile_resolver()
        self._capacity_profile.resolve()
        self._expose_capacity_profile = expose_capacity_profile
        self._secret_envelope = secret_envelope or derive_sfu_hub_envelope(
            hashlib.sha256(f"in-memory:{self._hub_id}".encode()).hexdigest(),
            key_id="sfu-group-key-test-v1",
        )
        self._repository = repository or InMemorySfuBroadcastGroupKeyRepository(
            self._secret_envelope
        )
        self._lock = threading.RLock()

    def configure_audit(self, audit: SemanticMediaAuditPort) -> None:
        """Attach the Hub audit factory even if the singleton was resolved early."""

        with self._lock:
            self._audit = audit

    @observed_control_path("key_delivery")
    def prepare_epoch(
        self,
        request: Mapping[str, Any],
        *,
        actor_id: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        _closed(
            request,
            {"session_id", "membership_epoch", "publication_id", "key_package_refs", "idempotency_key"},
            "sfu_group_epoch_request_invalid",
        )
        session_id = _identifier(request.get("session_id"), "session_id")
        membership_epoch = _positive_int(request.get("membership_epoch"), "membership_epoch")
        publication_id = _identifier(request.get("publication_id"), "publication_id")
        idempotency_key = _identifier(request.get("idempotency_key"), "idempotency_key")
        publication = self._publications.publication_for_group_key(
            session_id=session_id,
            membership_epoch=membership_epoch,
            publication_id=publication_id,
            actor_id=actor_id,
            tenant_id=tenant_id,
        )
        raw_subscribers = publication.get("authorized_subscriber_ids")
        if not isinstance(raw_subscribers, list):
            raise SfuGroupKeyError("sfu_group_size_invalid", 409)
        if not self._capacity_profile.resolve().allows_receiver_count(len(raw_subscribers)):
            raise SfuGroupKeyError("capacity_cap_exceeded", 409)
        subscribers = tuple(sorted(str(value) for value in raw_subscribers))
        members = tuple(sorted({actor_id, *subscribers}))
        if len(members) < 2 or not self._capacity_profile.resolve().allows_participant_count(len(members)):
            raise SfuGroupKeyError("sfu_group_size_invalid", 409)
        for member_id in members:
            self._require_member(tenant_id, session_id, member_id, membership_epoch)
        refs = _package_refs(request.get("key_package_refs"), members)
        canonical = {
            "session_id": session_id,
            "membership_epoch": membership_epoch,
            "publication_id": publication_id,
            "key_package_refs": refs,
        }
        request_digest = _digest(canonical)
        now_ms = int(self._clock() * 1000)
        room_id = str(publication["room_id"])
        with self._lock:
            self._prune(now_ms)
            cached = self._receipt(
                tenant_id, session_id, actor_id, "prepare", idempotency_key,
                request_digest, now_ms,
            )
            if cached is not None:
                return cached
            current_epoch = self._epochs.current_epoch("room", room_id)
            latest_state = self._repository.latest(
                tenant_id=tenant_id, session_id=session_id, room_id=room_id
            )
            latest = (
                (
                    int(latest_state.authorization.membership_epoch or 0),
                    latest_state.authorization.member_ids,
                    latest_state.authorization.epoch,
                )
                if latest_state is not None else None
            )
            if current_epoch is None:
                previous_epoch = 0
                reason = "create"
            else:
                previous_epoch = current_epoch
                reason = _rekey_reason(latest, membership_epoch, members)
            audit_factory = self._audit_event_factory(
                tenant_id=tenant_id,
                session_id=session_id,
                room_id=room_id,
                reason=reason,
                idempotency_key=idempotency_key,
            )
            takeover_audit_factory = self._audit_event_factory(
                tenant_id=tenant_id,
                session_id=session_id,
                room_id=room_id,
                reason="hub_failover",
                idempotency_key=idempotency_key,
            )
            if current_epoch is None:
                claimed = self._epochs.claim_epoch(
                    scope_kind="room",
                    scope_id=room_id,
                    hub_id=self._hub_id,
                    audit_event_factory=audit_factory,
                    takeover_audit_event_factory=takeover_audit_factory,
                )
            else:
                claimed = self._epochs.claim_epoch(
                    scope_kind="room",
                    scope_id=room_id,
                    hub_id=self._hub_id,
                    advance=True,
                    audit_event_factory=audit_factory,
                    takeover_audit_event_factory=takeover_audit_factory,
                )
            if not claimed.ok or claimed.epoch is None:
                raise SfuGroupKeyError(f"sfu_group_{claimed.reason}", 409)
            if claimed.ownership_changed:
                reason = "hub_failover"
            try:
                signed = self._authorization.authorize(
                    tenant_id=tenant_id,
                    room_id=room_id,
                    publication_id=publication_id,
                    epoch=claimed.epoch,
                    previous_epoch=previous_epoch,
                    active_member_ids=members,
                    key_package_refs=refs,
                    reason=reason,
                    rekey_deadline_ms=now_ms + 10_000,
                    expires_at_ms=now_ms + 120_000,
                    membership_epoch=membership_epoch,
                )
            except GroupKeyAuthorizationError as exc:
                raise SfuGroupKeyError(f"sfu_group_{exc.reason_code}", 409) from exc
            result = self._epoch_result(signed)
            if self._expose_capacity_profile:
                result["capacity_profile"] = self._capacity_profile.resolve().public_contract(room_id=room_id)
            state = SfuGroupKeyEpochState(
                authorization=signed,
                session_id=session_id,
                publisher_digest=self._subject_digest(tenant_id, session_id, actor_id),
                fencing_token=signed.epoch,
            )
            receipt = self._new_receipt(
                tenant_id, session_id, actor_id, "prepare", idempotency_key,
                request_digest, result, signed.expires_at_ms,
            )
            try:
                saved = self._repository.create_epoch(state, receipt, now_ms=now_ms)
            except SfuBroadcastGroupKeyRepositoryError as exc:
                raise SfuGroupKeyError(exc.reason_code, 503) from exc
            if not saved.committed:
                raise SfuGroupKeyError(saved.reason_code or "sfu_group_epoch_conflict", 409)
            return json.loads(json.dumps(result))

    def _audit_event_factory(
        self,
        *,
        tenant_id: str,
        session_id: str,
        room_id: str,
        reason: str,
        idempotency_key: str,
    ) -> Callable[[int], SemanticMediaAuditEvent] | None:
        if self._audit is None:
            return None

        def prepare(epoch: int) -> SemanticMediaAuditEvent:
            try:
                return self._audit.prepare_transition(
                    idempotency_key=f"semantic-sfu-rekey:{idempotency_key}",
                    tenant_id=tenant_id,
                    scope=f"semantic-sfu:{session_id}",
                    event_type="semantic_rekey",
                    transition="epoch_claimed",
                    reason_code=f"group_key_{reason}",
                    epoch=epoch,
                    contract_ref=f"sfu-group:{room_id}",
                )
            except Exception as exc:
                raise SfuGroupKeyError("semantic_audit_unavailable", 503) from exc

        return prepare

    @observed_control_path("key_delivery")
    def deliver_packages(
        self,
        authorization_id: str,
        request: Mapping[str, Any],
        *,
        actor_id: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        _closed(request, {"packages", "idempotency_key"}, "sfu_group_delivery_request_invalid")
        normalized_auth = _identifier(authorization_id, "authorization_id")
        idempotency_key = _identifier(request.get("idempotency_key"), "idempotency_key")
        raw_packages = request.get("packages")
        if not isinstance(raw_packages, list):
            raise SfuGroupKeyError("sfu_group_packages_invalid")
        with self._lock:
            state = self._require_issued(normalized_auth, actor_id, tenant_id)
            issued = _IssuedEpoch(
                state.authorization,
                state.session_id,
                actor_id,
                dict(state.authorization.key_package_refs),
                set(state.delivered_member_ids),
                set(state.acknowledged_member_ids),
            )
            expected = set(issued.authorization.member_ids) - {actor_id}
            packages = _delivery_packages(raw_packages, issued, expected)
            canonical = {"authorization_id": normalized_auth, "packages": packages}
            request_digest = _digest(canonical)
            now_ms = int(self._clock() * 1000)
            cached = self._receipt(
                tenant_id, state.session_id, actor_id, "deliver", idempotency_key,
                request_digest, now_ms,
            )
            if cached is not None:
                return cached
            writes = tuple(
                SfuGroupKeyPackageWrite(
                    recipient_id=package["recipient_id"],
                    recipient_digest=self._subject_digest(
                        tenant_id, state.session_id, package["recipient_id"]
                    ),
                    package_ref=package["package_ref"],
                    opaque_package=base64.b64decode(package["opaque_package_b64"], validate=True),
                    package_digest=package["package_digest"],
                    expires_at_ms=package["expires_at_ms"],
                )
                for package in packages
            )
            result = {
                "ok": True,
                "authorization_id": normalized_auth,
                "delivered_member_ids": sorted(expected),
                "pending_member_ids": [],
            }
            receipt = self._new_receipt(
                tenant_id, state.session_id, actor_id, "deliver", idempotency_key,
                request_digest, result, state.authorization.expires_at_ms,
            )
            try:
                saved = self._repository.deliver(
                    tenant_id=tenant_id,
                    authorization_id=normalized_auth,
                    expected_version=state.version,
                    expected_fencing_token=state.fencing_token,
                    packages=writes,
                    receipt=receipt,
                    now_ms=now_ms,
                )
            except SfuBroadcastGroupKeyRepositoryError as exc:
                raise SfuGroupKeyError(exc.reason_code, 503) from exc
            if not saved.committed:
                raise SfuGroupKeyError(saved.reason_code or "sfu_group_delivery_conflict", 409)
            return json.loads(json.dumps(result))

    def read_packages(
        self,
        *,
        session_id: str,
        membership_epoch: int,
        cursor: str,
        actor_id: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        normalized_session = _identifier(session_id, "session_id")
        epoch = _positive_int(membership_epoch, "membership_epoch")
        self._require_member(tenant_id, normalized_session, actor_id, epoch)
        try:
            page = None
            for recipient_digest in self._subject_digest_candidates(
                tenant_id, normalized_session, actor_id
            ):
                candidate_page = self._repository.read_for_recipient(
                    tenant_id=tenant_id,
                    session_id=normalized_session,
                    recipient_digest=recipient_digest,
                    membership_epoch=epoch,
                    cursor=cursor,
                    limit=self._capacity_profile.resolve().room_admission_cap,
                    now_ms=int(self._clock() * 1000),
                )
                page = candidate_page
                if candidate_page.items:
                    break
            if page is None:
                raise SfuGroupKeyError("sfu_group_package_recipient_mismatch", 403)
        except SfuBroadcastGroupKeyRepositoryError as exc:
            raise SfuGroupKeyError(exc.reason_code, 503) from exc
        packages = [{
            "kind": "sfu_group_key_package",
            "authorization": asdict(item.authorization),
            "package_ref": item.package_ref,
            "publisher_id": item.publisher_id,
            "recipient_id": actor_id,
            "membership_epoch": item.authorization.membership_epoch,
            "opaque_package_b64": base64.b64encode(item.opaque_package).decode("ascii"),
            "package_digest": item.package_digest,
            "expires_at_ms": item.expires_at_ms,
        } for item in page.items]
        return {"ok": True, "packages": packages, "cursor": page.next_cursor}

    @observed_control_path("key_delivery")
    def acknowledge_package(
        self,
        authorization_id: str,
        request: Mapping[str, Any],
        *,
        actor_id: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        _closed(request, {"package_ref", "membership_epoch"}, "sfu_group_ack_request_invalid")
        normalized_auth = _identifier(authorization_id, "authorization_id")
        package_ref = _identifier(request.get("package_ref"), "package_ref")
        epoch = _positive_int(request.get("membership_epoch"), "membership_epoch")
        with self._lock:
            state = self._repository.get(tenant_id=tenant_id, authorization_id=normalized_auth)
            if state is None or state.authorization.expires_at_ms <= int(self._clock() * 1000):
                raise SfuGroupKeyError("sfu_group_authorization_unavailable", 404)
            if state.authorization.membership_epoch != epoch:
                raise SfuGroupKeyError("sfu_group_membership_epoch_stale", 409)
            if self._epochs.current_epoch("room", state.authorization.room_id) != state.authorization.epoch:
                raise SfuGroupKeyError("sfu_group_authorization_stale", 409)
            self._require_member(tenant_id, state.session_id, actor_id, epoch)
            if (
                state.publisher_digest == self._subject_digest(tenant_id, state.session_id, actor_id)
                or state.authorization.key_package_refs.get(actor_id) != package_ref
            ):
                raise SfuGroupKeyError("sfu_group_package_recipient_mismatch", 403)
            result = None
            for recipient_digest in self._subject_digest_candidates(
                tenant_id, state.session_id, actor_id
            ):
                candidate_result = self._repository.acknowledge(
                    tenant_id=tenant_id,
                    authorization_id=normalized_auth,
                    package_ref=package_ref,
                    recipient_digest=recipient_digest,
                    membership_epoch=epoch,
                    now_ms=int(self._clock() * 1000),
                )
                result = candidate_result
                if candidate_result.status != "not_found":
                    break
            if result is None:
                raise SfuGroupKeyError("sfu_group_package_recipient_mismatch", 403)
            if not result.committed:
                status = 404 if result.status == "not_found" else 409
                raise SfuGroupKeyError(result.reason_code or "sfu_group_ack_conflict", status)
            return {
                "ok": True,
                "authorization_id": normalized_auth,
                "acknowledged_member_id": actor_id,
            }

    def epoch_status(
        self,
        authorization_id: str,
        *,
        actor_id: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            issued = self._require_issued(_identifier(authorization_id, "authorization_id"), actor_id, tenant_id)
            expected = set(issued.authorization.member_ids) - {actor_id}
            return {
                "ok": True,
                "authorization_id": issued.authorization.authorization_id,
                "membership_epoch": issued.authorization.membership_epoch,
                "group_key_epoch": issued.authorization.epoch,
                "acknowledged_member_ids": sorted(issued.acknowledged_member_ids),
                "pending_member_ids": sorted(expected - set(issued.acknowledged_member_ids)),
            }

    def _require_member(self, tenant: str, session: str, member_id: str, epoch: int) -> None:
        member = self._membership.member(tenant_id=tenant, session_id=session, participant_id=member_id)
        if member is None or not member.active:
            raise SfuGroupKeyError("sfu_group_membership_required", 403)
        if member.epoch != epoch:
            raise SfuGroupKeyError("sfu_group_membership_epoch_stale", 409)

    def _require_issued(self, authorization_id: str, actor: str, tenant: str) -> SfuGroupKeyEpochState:
        issued = self._repository.get(tenant_id=tenant, authorization_id=authorization_id)
        if issued is None or issued.authorization.expires_at_ms <= int(self._clock() * 1000):
            raise SfuGroupKeyError("sfu_group_authorization_unavailable", 404)
        if issued.publisher_digest != self._subject_digest(tenant, issued.session_id, actor):
            raise SfuGroupKeyError("sfu_group_publisher_required", 403)
        if self._epochs.current_epoch("room", issued.authorization.room_id) != issued.authorization.epoch:
            raise SfuGroupKeyError("sfu_group_authorization_stale", 409)
        self._require_member(
            tenant,
            issued.session_id,
            actor,
            int(issued.authorization.membership_epoch or 0),
        )
        return issued

    def _package_epoch_is_current(self, row: Mapping[str, Any]) -> bool:
        authorization = row.get("authorization")
        if not isinstance(authorization, Mapping):
            return False
        room_id = authorization.get("room_id")
        group_epoch = authorization.get("epoch")
        if not isinstance(room_id, str) or type(group_epoch) is not int:
            return False
        return self._epochs.current_epoch("room", room_id) == group_epoch

    def _epoch_result(self, authorization: GroupKeyEpochAuthorization) -> dict[str, Any]:
        return {
            "ok": True,
            "authorization": asdict(authorization),
            "hub_key_id": self._authorization.hub_key_id,
            "hub_public_key_b64": self._authorization.hub_public_key_b64(),
        }

    def _receipt(
        self, tenant: str, session: str, actor: str, operation: str,
        key: str, digest: str, now_ms: int,
    ) -> dict[str, Any] | None:
        found = self._repository.receipt(
            tenant_id=tenant,
            actor_digest=self._subject_digest(tenant, session, actor),
            operation=operation,
            idempotency_key_digest=self._idempotency_digest(tenant, operation, key),
            now_ms=now_ms,
        )
        if found is None:
            return None
        if found.request_digest != digest:
            raise SfuGroupKeyError("sfu_group_idempotency_conflict", 409)
        return json.loads(json.dumps(found.result))

    def _new_receipt(
        self, tenant: str, session: str, actor: str, operation: str,
        key: str, digest: str, result: dict[str, Any], expires_at_ms: int,
    ) -> SfuGroupKeyReceipt:
        return SfuGroupKeyReceipt(
            tenant_id=tenant,
            actor_digest=self._subject_digest(tenant, session, actor),
            operation=operation,
            idempotency_key_digest=self._idempotency_digest(tenant, operation, key),
            request_digest=digest,
            result=json.loads(json.dumps(result)),
            expires_at_ms=expires_at_ms,
        )

    def _subject_digest(self, tenant: str, session: str, actor: str) -> str:
        return self._secret_envelope.blind(
            purpose="sfu-group-key-subject", scope=f"{tenant}:{session}", value=actor
        )

    def _subject_digest_candidates(
        self, tenant: str, session: str, actor: str
    ) -> tuple[str, ...]:
        return tuple(
            candidate.digest
            for candidate in self._secret_envelope.blind_candidates(
                purpose="sfu-group-key-subject",
                scope=f"{tenant}:{session}",
                value=actor,
            )
        )

    def _idempotency_digest(self, tenant: str, operation: str, key: str) -> str:
        return self._secret_envelope.blind(
            purpose=f"sfu-group-key-idempotency:{operation}", scope=tenant, value=key
        )

    def _prune(self, now_ms: int) -> None:
        try:
            self._repository.purge_expired(now_ms=now_ms, limit=_ISSUED_LIMIT)
        except SfuBroadcastGroupKeyRepositoryError as exc:
            raise SfuGroupKeyError(exc.reason_code, 503) from exc


def _rekey_reason(
    latest: tuple[int, tuple[str, ...], int] | None,
    membership_epoch: int,
    members: tuple[str, ...],
) -> str:
    if latest is None:
        return "hub_failover"
    previous_membership_epoch, previous_members, _group_epoch = latest
    if set(members) - set(previous_members):
        return "join"
    if set(previous_members) - set(members):
        return "revoke"
    if membership_epoch != previous_membership_epoch:
        return "refresh"
    return "refresh"


def _package_refs(value: object, members: tuple[str, ...]) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(members):
        raise SfuGroupKeyError("sfu_group_key_package_set_mismatch")
    refs = {str(member): _identifier(reference, "package_ref") for member, reference in value.items()}
    if len(set(refs.values())) != len(refs):
        raise SfuGroupKeyError("sfu_group_key_package_ref_duplicate")
    return dict(sorted(refs.items()))


def _delivery_packages(
    values: list[object],
    issued: _IssuedEpoch,
    expected: set[str],
) -> list[dict[str, Any]]:
    if len(values) != len(expected):
        raise SfuGroupKeyError("sfu_group_packages_invalid")
    parsed: list[dict[str, Any]] = []
    recipients: set[str] = set()
    for raw in values:
        if not isinstance(raw, dict):
            raise SfuGroupKeyError("sfu_group_package_invalid")
        _closed(
            raw,
            {"recipient_id", "package_ref", "opaque_package_b64", "package_digest", "expires_at_ms"},
            "sfu_group_package_invalid",
        )
        recipient = _identifier(raw.get("recipient_id"), "recipient_id")
        reference = _identifier(raw.get("package_ref"), "package_ref")
        expiry = _positive_int(raw.get("expires_at_ms"), "expires_at_ms")
        digest = str(raw.get("package_digest") or "")
        opaque = str(raw.get("opaque_package_b64") or "")
        try:
            decoded = base64.b64decode(opaque, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise SfuGroupKeyError("sfu_group_opaque_package_invalid") from exc
        if not _MIN_OPAQUE_PACKAGE_BYTES <= len(decoded) <= _MAX_OPAQUE_PACKAGE_BYTES:
            raise SfuGroupKeyError("sfu_group_opaque_package_invalid")
        if digest != hashlib.sha256(decoded).hexdigest():
            raise SfuGroupKeyError("sfu_group_package_digest_mismatch")
        if expiry > issued.authorization.expires_at_ms or expiry <= issued.authorization.valid_from_ms:
            raise SfuGroupKeyError("sfu_group_package_expiry_invalid")
        if recipient not in expected or recipient in recipients or issued.package_refs.get(recipient) != reference:
            raise SfuGroupKeyError("sfu_group_package_recipient_mismatch", 403)
        recipients.add(recipient)
        parsed.append(
            {
                "recipient_id": recipient,
                "package_ref": reference,
                "opaque_package_b64": opaque,
                "package_digest": digest,
                "expires_at_ms": expiry,
            }
        )
    return sorted(parsed, key=lambda row: row["recipient_id"])


def _closed(value: Mapping[str, Any], fields: set[str], reason: str) -> None:
    if set(value) != fields:
        raise SfuGroupKeyError(reason)


def _identifier(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value.encode("utf-8")) <= 128
        or not value[0].isalnum()
        or any(not (char.isalnum() or char in "._:-") for char in value)
    ):
        raise SfuGroupKeyError(f"sfu_group_{field}_invalid")
    return value


def _positive_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise SfuGroupKeyError(f"sfu_group_{field}_invalid")
    return value


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


_SERVICE: SemanticSfuGroupKeyService | None = None
_SERVICE_LOCK = threading.Lock()
_AUDIT: SemanticMediaAuditPort | None = None


def get_semantic_sfu_group_key_service() -> SemanticSfuGroupKeyService:
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                membership = ShareSessionSfuMembership()
                private_key = derive_hub_identity_key(str(settings.secret_key).encode("utf-8"))
                public_key = base64.b64encode(private_key.public_key().public_bytes_raw()).decode("ascii")
                authorization = WebrtcGroupKeyAuthorizationService(
                    private_key=private_key,
                    hub_key_id=f"hub-ed25519:{hashlib.sha256(public_key.encode()).hexdigest()[:16]}",
                    epoch_repository=WebrtcEpochRepository(),
                )
                envelope = derive_sfu_hub_envelope(
                    str(settings.secret_key), key_id="sfu-group-key-v1"
                )
                _SERVICE = SemanticSfuGroupKeyService(
                    membership=membership,
                    publications=get_semantic_sfu_admission_service(),
                    epochs=get_webrtc_epoch_service(),
                    authorization=authorization,
                    relay=get_share_relay_compatibility_service(),
                    hub_id=str(settings.agent_name or "hub")[:128],
                    audit=_AUDIT,
                    expose_capacity_profile=True,
                    repository=SqlSfuBroadcastGroupKeyRepository(envelope),
                    secret_envelope=envelope,
                )
    return _SERVICE


def configure_semantic_sfu_group_key_audit(audit: SemanticMediaAuditPort) -> None:
    """Configure the Hub-owned group-key audit command factory."""

    global _AUDIT
    _AUDIT = audit
    if _SERVICE is not None:
        _SERVICE.configure_audit(audit)


__all__ = [
    "SemanticSfuGroupKeyService",
    "SfuGroupKeyError",
    "SfuGroupPublicationPort",
    "configure_semantic_sfu_group_key_audit",
    "get_semantic_sfu_group_key_service",
]
