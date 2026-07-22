"""Durable TURN pool directory and deterministic failover selection.

The directory is a hub-side control-plane service.  It never probes TURN
instances itself; collector observations are projected through the repository
after signature, identity and replay validation.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit


class TurnPoolDirectoryError(RuntimeError):
    def __init__(self, reason_code: str, status_code: int = 409) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code


@dataclass(frozen=True)
class TurnPoolRegistration:
    pool_id: str
    instance_id: str
    region: str
    endpoints: tuple[Mapping[str, str], ...]
    credential_modes: tuple[str, ...]
    config_version: str
    config_digest: str
    observer_identity_id: str
    observer_identity_version: int
    trust_policy_version: str
    cost_units: int


@dataclass(frozen=True)
class TurnPoolNode:
    pool_id: str
    instance_id: str
    region: str
    endpoints: tuple[Mapping[str, str], ...]
    credential_modes: tuple[str, ...]
    config_version: str
    config_digest: str
    observer_identity_id: str
    observer_identity_version: int
    trust_policy_version: str
    lifecycle_state: str
    health_status: str
    relay_ready: bool
    capacity_status: str
    cost_units: int
    fresh_until: datetime | None
    observation_fencing_token: int
    version: int


@dataclass(frozen=True)
class TurnPoolSelectionQuery:
    pool_id: str
    region: str
    consumer: str
    transport: str
    credential_mode: str
    config_version: str
    trust_policy_version: str
    receiver_stability_ref: str
    excluded_instance_ids: tuple[str, ...] = ()
    retry_index: int = 0


@dataclass(frozen=True)
class TurnPoolSelection:
    pool_id: str
    instance_id: str
    endpoints: tuple[Mapping[str, str], ...]
    config_version: str
    observer_identity_id: str
    observation_fencing_token: int
    failover_retry_index: int


class TurnPoolRepository(Protocol):
    def register(
        self,
        *,
        registration: TurnPoolRegistration,
        actor_id: str,
        idempotency_key: str,
        request_digest: str,
        expected_version: int,
    ) -> TurnPoolNode: ...

    def apply_observation(self, *, cursor: Any, normalized: Mapping[str, Any]) -> TurnPoolNode: ...

    def list_pool(self, *, pool_id: str, region: str) -> Sequence[TurnPoolNode]: ...

    def stop_observer(self, *, observer_identity_id: str, reason_code: str) -> int: ...


class TurnPoolDirectory:
    """Owns directory mutations and fail-closed TURN instance selection."""

    _ID_LIMIT = 128
    _ALLOWED_CONSUMERS = frozenset({"peer", "livekit_sfu", "sfu_all_turn"})
    _ALLOWED_TRANSPORTS = frozenset({"udp", "tcp", "tls"})
    _ALLOWED_CREDENTIAL_MODES = frozenset({"rest_hmac_sha256"})

    def __init__(
        self,
        repository: TurnPoolRepository,
        *,
        selection_hmac_key: bytes,
        observer_is_active: Callable[[str, int], bool],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_failover_retries: int = 2,
    ) -> None:
        if len(selection_hmac_key) < 32:
            raise ValueError("turn_pool_selection_key_too_short")
        if max_failover_retries < 0 or max_failover_retries > 5:
            raise ValueError("turn_pool_failover_bound_invalid")
        self._repository = repository
        self._selection_hmac_key = selection_hmac_key
        self._observer_is_active = observer_is_active
        self._now = now
        self._max_failover_retries = max_failover_retries

    def register(
        self,
        registration: TurnPoolRegistration,
        *,
        actor_id: str,
        idempotency_key: str,
        request_digest: str,
        expected_version: int,
    ) -> TurnPoolNode:
        self._validate_registration(registration)
        if not actor_id or not idempotency_key or len(idempotency_key) > 128:
            raise TurnPoolDirectoryError("turn_pool_mutation_context_invalid", 400)
        return self._repository.register(
            registration=registration,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            expected_version=expected_version,
        )

    def apply_observation(self, *, cursor: Any, normalized: Mapping[str, Any]) -> TurnPoolNode:
        # The ingestion service has authenticated and normalized this object.
        # The durable repository still enforces config, observer and fencing CAS.
        return self._repository.apply_observation(cursor=cursor, normalized=normalized)

    def select(self, query: TurnPoolSelectionQuery) -> TurnPoolSelection:
        self._validate_query(query)
        now = self._now()
        eligible: list[tuple[TurnPoolNode, tuple[Mapping[str, str], ...]]] = []
        excluded = set(query.excluded_instance_ids)
        for node in self._repository.list_pool(pool_id=query.pool_id, region=query.region):
            endpoints = tuple(
                endpoint
                for endpoint in node.endpoints
                if endpoint.get("consumer") == query.consumer
                and endpoint.get("transport") == query.transport
            )
            if (
                node.instance_id not in excluded
                and self._observer_is_active(node.observer_identity_id, node.observer_identity_version)
                and node.lifecycle_state == "active"
                and node.health_status == "healthy"
                and node.relay_ready
                and node.capacity_status == "accept"
                and node.fresh_until is not None
                and self._is_fresh(node.fresh_until, now)
                and node.config_version == query.config_version
                and node.trust_policy_version == query.trust_policy_version
                and query.credential_mode in node.credential_modes
                and endpoints
            ):
                eligible.append((node, endpoints))
        if not eligible:
            raise TurnPoolDirectoryError("turn_pool_no_eligible_instance", 503)
        eligible.sort(key=lambda item: (item[0].cost_units, self._stable_rank(query, item[0])))
        selected, endpoints = eligible[0]
        return TurnPoolSelection(
            pool_id=selected.pool_id,
            instance_id=selected.instance_id,
            endpoints=endpoints,
            config_version=selected.config_version,
            observer_identity_id=selected.observer_identity_id,
            observation_fencing_token=selected.observation_fencing_token,
            failover_retry_index=query.retry_index,
        )

    def stop_observer(self, *, observer_identity_id: str) -> int:
        if not observer_identity_id:
            raise TurnPoolDirectoryError("turn_observer_identity_required", 400)
        return self._repository.stop_observer(
            observer_identity_id=observer_identity_id,
            reason_code="turn_observer_revoked",
        )

    def _stable_rank(self, query: TurnPoolSelectionQuery, node: TurnPoolNode) -> bytes:
        payload = "\x1f".join(
            (query.receiver_stability_ref, query.pool_id, query.region, node.instance_id)
        ).encode("utf-8")
        return hmac.new(self._selection_hmac_key, payload, hashlib.sha256).digest()

    def _validate_registration(self, registration: TurnPoolRegistration) -> None:
        identifiers = (
            registration.pool_id,
            registration.instance_id,
            registration.region,
            registration.config_version,
            registration.observer_identity_id,
            registration.trust_policy_version,
        )
        if any(not value or len(value) > self._ID_LIMIT for value in identifiers):
            raise TurnPoolDirectoryError("turn_pool_registration_identifier_invalid", 400)
        if registration.observer_identity_version < 1 or not 0 <= registration.cost_units <= 1_000_000:
            raise TurnPoolDirectoryError("turn_pool_registration_version_invalid", 400)
        if not registration.config_digest.startswith("sha256:") or len(registration.config_digest) != 71:
            raise TurnPoolDirectoryError("turn_pool_config_digest_invalid", 400)
        if not registration.endpoints or len(registration.endpoints) > 12:
            raise TurnPoolDirectoryError("turn_pool_endpoints_invalid", 400)
        for endpoint in registration.endpoints:
            if set(endpoint) != {"url", "consumer", "transport"}:
                raise TurnPoolDirectoryError("turn_pool_endpoint_shape_invalid", 400)
            expected_scheme = "turns" if endpoint["transport"] == "tls" else "turn"
            raw_url = endpoint["url"]
            normalized_url = raw_url.replace(f"{expected_scheme}:", f"{expected_scheme}://", 1)
            parsed = urlsplit(normalized_url)
            allowed_queries = {"", f"transport={endpoint['transport']}"}
            if endpoint["transport"] == "tls":
                allowed_queries.add("transport=tcp")
            if (
                endpoint["consumer"] not in self._ALLOWED_CONSUMERS
                or endpoint["transport"] not in self._ALLOWED_TRANSPORTS
                or parsed.scheme != expected_scheme
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.query not in allowed_queries
                or parsed.fragment
            ):
                raise TurnPoolDirectoryError("turn_pool_endpoint_invalid", 400)
        modes = set(registration.credential_modes)
        if not modes or not modes <= self._ALLOWED_CREDENTIAL_MODES:
            raise TurnPoolDirectoryError("turn_pool_credential_mode_invalid", 400)

    def _validate_query(self, query: TurnPoolSelectionQuery) -> None:
        if (
            query.consumer not in self._ALLOWED_CONSUMERS
            or query.transport not in self._ALLOWED_TRANSPORTS
            or query.credential_mode not in self._ALLOWED_CREDENTIAL_MODES
        ):
            raise TurnPoolDirectoryError("turn_pool_selection_constraint_invalid", 400)
        if not query.receiver_stability_ref or len(query.receiver_stability_ref) > 256:
            raise TurnPoolDirectoryError("turn_pool_selection_reference_invalid", 400)
        if query.retry_index < 0 or query.retry_index > self._max_failover_retries:
            raise TurnPoolDirectoryError("turn_pool_failover_exhausted", 503)
        if len(query.excluded_instance_ids) != query.retry_index:
            raise TurnPoolDirectoryError("turn_pool_failover_state_invalid", 400)
        if len(set(query.excluded_instance_ids)) != len(query.excluded_instance_ids):
            raise TurnPoolDirectoryError("turn_pool_failover_state_invalid", 400)

    @staticmethod
    def _is_fresh(fresh_until: datetime, now: datetime) -> bool:
        if fresh_until.tzinfo is None:
            fresh_until = fresh_until.replace(tzinfo=UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return fresh_until > now
