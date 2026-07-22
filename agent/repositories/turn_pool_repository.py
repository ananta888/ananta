"""SQL-backed TURN pool directory repository.

There is deliberately no in-memory fallback: pool eligibility is a
multi-hub safety decision and therefore requires durable CAS state.
"""

from __future__ import annotations

import hashlib
import time

from agent.services.turn_pool_contract import (
    TurnPoolNodeDocument,
    TurnPoolObservationDocument,
)

from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.turn_pool_nodes import TurnPoolNodeDB, TurnPoolNodeMutationDB
from agent.services.turn_pool_directory import TurnPoolNode, TurnPoolRegistration


class TurnPoolRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class SqlTurnPoolRepository:
    """Maps the directory port to SQLModel with row-level mutation fencing."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def register(
        self,
        *,
        registration: TurnPoolRegistration,
        actor_id: str,
        idempotency_key: str,
        request_digest: str,
        expected_version: int,
    ) -> TurnPoolNode:
        now = time.time()
        idempotency_digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        with self._session_factory() as session:
            stale_receipts = session.exec(
                select(TurnPoolNodeMutationDB)
                .where(
                    TurnPoolNodeMutationDB.expires_at.is_not(None),
                    TurnPoolNodeMutationDB.expires_at <= now,
                )
                .limit(100)
            ).all()
            for stale_receipt in stale_receipts:
                session.delete(stale_receipt)
            receipt = session.exec(
                select(TurnPoolNodeMutationDB).where(
                    TurnPoolNodeMutationDB.pool_id == registration.pool_id,
                    TurnPoolNodeMutationDB.instance_id == registration.instance_id,
                    TurnPoolNodeMutationDB.actor == actor_id,
                    TurnPoolNodeMutationDB.idempotency_key_digest == idempotency_digest,
                )
            ).first()
            if receipt is not None:
                if receipt.request_digest != request_digest:
                    raise TurnPoolRepositoryError("turn_pool_idempotency_conflict")
                return self._record_from_mapping(receipt.response_json)
            scoped_receipts = session.exec(
                select(TurnPoolNodeMutationDB.id)
                .where(
                    TurnPoolNodeMutationDB.pool_id == registration.pool_id,
                    TurnPoolNodeMutationDB.instance_id == registration.instance_id,
                    TurnPoolNodeMutationDB.actor == actor_id,
                )
                .limit(1_024)
            ).all()
            if len(scoped_receipts) >= 1_024:
                raise TurnPoolRepositoryError("turn_pool_mutation_capacity_exceeded")

            node = session.exec(
                select(TurnPoolNodeDB)
                .where(
                    TurnPoolNodeDB.pool_id == registration.pool_id,
                    TurnPoolNodeDB.instance_id == registration.instance_id,
                )
                .with_for_update()
            ).first()
            current_version = 0 if node is None else node.version
            if current_version != expected_version:
                raise TurnPoolRepositoryError("turn_pool_version_conflict")
            if node is None:
                node = TurnPoolNodeDB(
                    pool_id=registration.pool_id,
                    instance_id=registration.instance_id,
                    region=registration.region,
                    endpoint_urls=[dict(value) for value in registration.endpoints],
                    transports=sorted({str(value.get("transport", "udp")) for value in registration.endpoints}),
                    credential_binding_modes=list(registration.credential_modes),
                    relay_port_min=49_152,
                    relay_port_max=65_535,
                    allocation_limit=1,
                    bps_limit=1,
                    cost_profile="unknown",
                    config_version=registration.config_version,
                    config_digest=registration.config_digest,
                    observer_identity_id=registration.observer_identity_id,
                    observer_identity_version=registration.observer_identity_version,
                    trust_policy_version=registration.trust_policy_version,
                    certificate_fingerprint="unavailable",
                    status="active",
                    health_status="unknown",
                    relay_status="unknown",
                    capacity_status="stop",
                    cost_units=registration.cost_units,
                    observation_fencing_token=0,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(node)
            else:
                node.region = registration.region
                node.endpoint_urls = [dict(value) for value in registration.endpoints]
                node.transports = sorted({str(value.get("transport", "udp")) for value in registration.endpoints})
                node.credential_binding_modes = list(registration.credential_modes)
                node.config_version = registration.config_version
                node.config_digest = registration.config_digest
                node.observer_identity_id = registration.observer_identity_id
                node.observer_identity_version = registration.observer_identity_version
                node.trust_policy_version = registration.trust_policy_version
                node.cost_units = registration.cost_units
                node.health_status = "unknown"
                node.relay_status = "unknown"
                node.capacity_status = "stop"
                node.fresh_until = None
                node.version += 1
                node.updated_at = now
                session.add(node)
            session.flush()
            response = self._mapping_from_db(node)
            session.add(
                TurnPoolNodeMutationDB(
                    pool_id=registration.pool_id,
                    instance_id=registration.instance_id,
                    actor=actor_id,
                    operation="register",
                    expected_version=expected_version,
                    idempotency_key_digest=idempotency_digest,
                    request_digest=request_digest,
                    response_json=response,
                    result_version=node.version,
                    reason_code="turn_pool_registered",
                    audited_at=now,
                    expires_at=now + 86_400,
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise TurnPoolRepositoryError("turn_pool_mutation_conflict") from exc
            return self._record_from_mapping(response)

    def apply_observation(self, *, cursor: Any, normalized: Mapping[str, Any]) -> TurnPoolNode:
        now = time.time()
        with self._session_factory() as session:
            node = session.exec(
                select(TurnPoolNodeDB)
                .where(
                    TurnPoolNodeDB.pool_id == cursor.pool_id,
                    TurnPoolNodeDB.instance_id == cursor.instance_id,
                )
                .with_for_update()
            ).first()
            if node is None:
                raise TurnPoolRepositoryError("turn_pool_instance_unregistered")
            if (
                node.observer_identity_id != cursor.observer_identity_id
                or node.observer_identity_version != cursor.observer_identity_version
            ):
                self._stop(node, now, "turn_pool_observer_fence_mismatch")
                session.commit()
                raise TurnPoolRepositoryError("turn_pool_observer_fence_mismatch")
            if (
                normalized.get("config_digest") != node.config_digest
                or int(normalized.get("contract_version", 0)) != 1
            ):
                self._stop(node, now, "turn_pool_config_fence_mismatch")
                session.commit()
                raise TurnPoolRepositoryError("turn_pool_config_fence_mismatch")
            if cursor.fencing_token < node.observation_fencing_token:
                raise TurnPoolRepositoryError("turn_pool_observation_fence_stale")

            normalized = TurnPoolObservationDocument.from_mapping(
                {
                    **normalized,
                    "node_id": f"{cursor.pool_id}:{cursor.instance_id}",
                    "observation_fencing_token": cursor.fencing_token,
                    "observation_version": node.observation_version + 1,
                    "observed_at": cursor.last_measured_at,
                    "fresh_until": cursor.fresh_until,
                }
            ).repository_mapping()

            health_status = normalized.get("health_status")
            node.health_status = health_status if health_status in {"healthy", "degraded", "unhealthy"} else "unknown"
            relay_status = normalized.get("relay_status", "unknown")
            node.relay_status = relay_status if relay_status in {"ready", "not_ready", "unknown"} else "unknown"
            node.capacity_status = normalized.get("capacity_status", "stop")
            if node.capacity_status not in {"accept", "reduce", "stop"}:
                node.capacity_status = "stop"
            node.last_observation_id = cursor.last_observation_id
            node.last_observed_at = cursor.last_measured_at
            node.fresh_until = cursor.fresh_until
            node.observation_fencing_token = cursor.fencing_token
            node.observation_version += 1
            node.version += 1
            node.updated_at = now
            session.add(node)
            session.commit()
            session.refresh(node)
            return self._record_from_db(node)

    def list_pool(self, *, pool_id: str, region: str) -> Sequence[TurnPoolNode]:
        with self._session_factory() as session:
            rows = session.exec(
                select(TurnPoolNodeDB).where(
                    TurnPoolNodeDB.pool_id == pool_id,
                    TurnPoolNodeDB.region == region,
                )
            ).all()
            return tuple(self._record_from_db(row) for row in rows)

    def stop_observer(self, *, observer_identity_id: str, reason_code: str) -> int:
        now = time.time()
        changed = 0
        with self._session_factory() as session:
            rows = session.exec(
                select(TurnPoolNodeDB)
                .where(TurnPoolNodeDB.observer_identity_id == observer_identity_id)
                .with_for_update()
            ).all()
            for row in rows:
                self._stop(row, now, reason_code)
                session.add(row)
                changed += 1
            session.commit()
        return changed

    @staticmethod
    def _stop(node: TurnPoolNodeDB, now: float, reason_code: str) -> None:
        node.health_status = "unhealthy"
        node.relay_status = "not_ready"
        node.capacity_status = "stop"
        node.fresh_until = None
        node.last_reason_code = reason_code
        node.version += 1
        node.updated_at = now

    @classmethod
    def _record_from_db(cls, node: TurnPoolNodeDB) -> TurnPoolNode:
        return cls._record_from_mapping(cls._mapping_from_db(node))

    @staticmethod
    def _mapping_from_db(node: TurnPoolNodeDB) -> dict[str, Any]:
        return {
            "pool_id": node.pool_id,
            "contract_version": node.contract_version,
            "instance_id": node.instance_id,
            "region": node.region,
            "endpoints": node.endpoint_urls,
            "credential_modes": node.credential_binding_modes,
            "config_version": node.config_version,
            "config_digest": node.config_digest,
            "observer_identity_id": node.observer_identity_id,
            "observer_identity_version": node.observer_identity_version,
            "trust_policy_version": node.trust_policy_version,
            "lifecycle_state": node.status,
            "health_status": node.health_status,
            "relay_ready": node.relay_status == "ready",
            "capacity_status": node.capacity_status,
            "cost_units": node.cost_units,
            "fresh_until": (
                datetime.fromtimestamp(node.fresh_until, tz=UTC).isoformat()
                if node.fresh_until is not None else None
            ),
            "observation_fencing_token": node.observation_fencing_token,
            "version": node.version,
        }

    @staticmethod
    def _record_from_mapping(value: Mapping[str, Any]) -> TurnPoolNode:
        value = TurnPoolNodeDocument.from_mapping(value).repository_mapping()
        fresh_until = value.get("fresh_until")
        if isinstance(fresh_until, str):
            fresh_until = datetime.fromisoformat(fresh_until.replace("Z", "+00:00"))
        return TurnPoolNode(
            pool_id=str(value["pool_id"]),
            instance_id=str(value["instance_id"]),
            region=str(value["region"]),
            endpoints=tuple(dict(item) for item in value["endpoints"]),
            credential_modes=tuple(str(item) for item in value["credential_modes"]),
            config_version=str(value["config_version"]),
            config_digest=str(value["config_digest"]),
            observer_identity_id=str(value["observer_identity_id"]),
            observer_identity_version=int(value["observer_identity_version"]),
            trust_policy_version=str(value["trust_policy_version"]),
            lifecycle_state=str(value["lifecycle_state"]),
            health_status=str(value["health_status"]),
            relay_ready=bool(value["relay_ready"]),
            capacity_status=str(value["capacity_status"]),
            cost_units=int(value["cost_units"]),
            fresh_until=fresh_until,
            observation_fencing_token=int(value["observation_fencing_token"]),
            version=int(value["version"]),
        )
