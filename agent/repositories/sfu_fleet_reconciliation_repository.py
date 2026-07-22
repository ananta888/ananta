"""Durable adapters for Hub-owned Fleet and Route reconciliation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Callable

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models.sfu_broadcast import SfuFanoutRouteDB
from agent.db_models.sfu_capacity_reservations import SfuCapacityReservationDB
from agent.db_models.sfu_nodes import SfuNodeDB
from agent.repositories.sfu_capacity_reservation_repository import (
    SqlSfuCapacityReservationRepository,
)
from agent.repositories.sfu_node_repository import SqlSfuNodeRepository
from agent.services.sfu_broadcast_reconciliation_jobs import (
    SfuRouteReconciliationScopeCandidate,
    SfuRouteReconciliationScopePage,
)
from agent.services.sfu_broadcast_repository_ports import (
    SfuBroadcastRoomScope,
    SfuFanoutRoute,
    SfuFanoutRouteRepositoryPort,
)
from agent.services.sfu_broadcast_route_port import RouteKeyV1, RouteVersionV1
from agent.services.sfu_fanout_reconciliation_service import (
    ReconciliationDesiredState,
    ReconciliationPhase,
    RouteReconciliationAuthority,
    RouteReconciliationCandidate,
    RouteReconciliationLease,
    RouteReconciliationPage,
    RouteReconciliationScope,
)
from agent.services.sfu_fleet_reconciliation_ports import (
    SfuFleetRuntimeRouteMutationPort,
    SfuFleetRuntimeRouteStatePort,
)
from agent.services.sfu_fleet_reconciliation_service import (
    SfuFleetReconciliationItem,
    SfuFleetReconciliationPage,
)
from agent.services.sfu_route_reconciliation_projection_port import (
    SfuRouteReconciliationProjectionPort,
)


class SfuFleetReconciliationRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class _CursorCodec:
    def __init__(self, secret: bytes, kind: str) -> None:
        if len(secret) < 32:
            raise ValueError("sfu_reconciliation_cursor_secret_invalid")
        self._secret = bytes(secret)
        self._kind = kind

    def encode(self, values: tuple[str, ...]) -> str:
        raw = json.dumps(
            {"kind": self._kind, "values": values},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.new(self._secret, raw, hashlib.sha256).digest()
        return _b64(raw) + "." + _b64(signature)

    def decode(self, value: str) -> tuple[str, ...]:
        try:
            payload, supplied = value.split(".", 1)
            raw = _unb64(payload)
            expected = hmac.new(self._secret, raw, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _unb64(supplied)):
                raise ValueError
            document = json.loads(raw.decode("utf-8"))
            if document.get("kind") != self._kind:
                raise ValueError
            values = document.get("values")
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise ValueError
            return tuple(values)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise SfuFleetReconciliationRepositoryError(
                "sfu_reconciliation_cursor_invalid"
            ) from exc


class SqlSfuFleetReconciliationStateRepository:
    def __init__(
        self,
        *,
        runtime_routes: SfuFleetRuntimeRouteStatePort,
        cursor_signing_key: bytes,
        db_engine=default_engine,
    ) -> None:
        self._runtime_routes = runtime_routes
        self._engine = db_engine
        self._cursors = _CursorCodec(cursor_signing_key, "fleet-state")

    def scan(
        self,
        *,
        partition: str,
        cursor: str | None,
        limit: int,
        now_ms: int,
    ) -> SfuFleetReconciliationPage:
        if not partition or not 1 <= limit <= 500:
            raise SfuFleetReconciliationRepositoryError("sfu_fleet_scan_scope_invalid")
        after = self._cursors.decode(cursor) if cursor else None
        try:
            with Session(self._engine) as db:
                statement = (
                    select(SfuFanoutRouteDB)
                    .where(SfuFanoutRouteDB.retention_status != "purged")
                    .order_by(
                        SfuFanoutRouteDB.tenant_id,
                        SfuFanoutRouteDB.session_id,
                        SfuFanoutRouteDB.id,
                    )
                    .limit(limit + 1)
                )
                if partition != "global":
                    statement = statement.where(SfuFanoutRouteDB.tenant_id == partition)
                if after is not None:
                    if len(after) != 3:
                        raise SfuFleetReconciliationRepositoryError(
                            "sfu_reconciliation_cursor_invalid"
                        )
                    statement = statement.where(
                        sa.tuple_(
                            SfuFanoutRouteDB.tenant_id,
                            SfuFanoutRouteDB.session_id,
                            SfuFanoutRouteDB.id,
                        ) > after
                    )
                rows = tuple(db.exec(statement).all())
                selected = rows[:limit]
                items = tuple(self._item(db, row, now_ms) for row in selected)
                next_cursor = None
                if len(rows) > limit and selected:
                    last = selected[-1]
                    next_cursor = self._cursors.encode(
                        (last.tenant_id, last.session_id, last.id)
                    )
                return SfuFleetReconciliationPage(items, next_cursor)
        except SfuFleetReconciliationRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise SfuFleetReconciliationRepositoryError(
                "sfu_fleet_state_store_unavailable"
            ) from exc

    def _item(
        self, db: Session, route: SfuFanoutRouteDB, now_ms: int
    ) -> SfuFleetReconciliationItem:
        reservation = db.exec(
            select(SfuCapacityReservationDB).where(
                SfuCapacityReservationDB.tenant_id == route.tenant_id,
                SfuCapacityReservationDB.room_id == route.session_id,
            )
        ).first()
        node = None
        if reservation is not None and reservation.observed_node_id:
            node = db.exec(
                select(SfuNodeDB).where(
                    SfuNodeDB.tenant_id == route.tenant_id,
                    SfuNodeDB.node_id == reservation.observed_node_id,
                )
            ).first()
        runtime = self._runtime_routes.observe(
            tenant_id=route.tenant_id,
            room_id=route.session_id,
            route_id=route.id,
            desired_route_version=route.route_epoch,
        )
        desired = route.status == "active" and int(route.expires_at * 1_000) > now_ms
        reservation_active = bool(
            reservation is not None
            and reservation.status == "active"
            and int(reservation.lease_expires_at * 1_000) > now_ms
        )
        node_fresh_until = int((node.observation_expires_at or 0) * 1_000) if node else 0
        node_health = (
            node.health_status
            if node is not None and node.revoked_at is None and node_fresh_until > now_ms
            else "unknown"
        )
        consistent = bool(
            runtime.control_plane_consistent
            and reservation is not None
            and reservation.tenant_id == route.tenant_id
            and reservation.room_id == route.session_id
            and (
                node is None
                or (
                    node.cluster_id == reservation.cluster_id
                    and node.region == reservation.region
                )
            )
        )
        reservation_expires = (
            int(reservation.lease_expires_at * 1_000) if reservation else now_ms
        )
        return SfuFleetReconciliationItem(
            item_id=route.id,
            cursor_after=self._cursors.encode(
                (route.tenant_id, route.session_id, route.id)
            ),
            expected_state_version=route.version,
            desired_route=desired,
            desired_route_version=route.route_epoch,
            active_route=runtime.active,
            active_route_version=runtime.route_version,
            route_intent_expires_at_ms=int(route.expires_at * 1_000),
            reservation_active=reservation_active,
            reservation_orphaned=bool(
                reservation_active
                and (not desired or (reservation.observed_node_id and node is None))
            ),
            reservation_expires_at_ms=reservation_expires,
            observation_fresh_until_ms=node_fresh_until,
            stale_access_expires_at_ms=min(
                int(route.expires_at * 1_000),
                reservation_expires,
                node_fresh_until or now_ms,
            ),
            node_health=node_health,
            admission_ready=bool(
                reservation_active
                and node_health == "healthy"
                and node is not None
                and node.drain_state == "active"
            ),
            control_plane_consistent=consistent,
        )


class SqlSfuFleetReconciliationMutationRepository:
    def __init__(
        self,
        *,
        runtime_routes: SfuFleetRuntimeRouteMutationPort,
        capacity: SqlSfuCapacityReservationRepository,
        nodes: SqlSfuNodeRepository,
        db_engine=default_engine,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._runtime_routes = runtime_routes
        self._capacity = capacity
        self._nodes = nodes
        self._engine = db_engine
        self._clock = clock

    def fence_route(self, *, item, fencing_token: int, reason_code: str) -> bool:
        if not self._runtime_routes.fence_route(
            item=item, fencing_token=fencing_token, reason_code=reason_code
        ):
            return False
        now = float(self._clock())
        try:
            with Session(self._engine) as db:
                row = db.get(SfuFanoutRouteDB, item.item_id)
                if row is None or row.status in {"revoked", "expired", "tombstoned"}:
                    return False
                if row.version != item.expected_state_version:
                    raise SfuFleetReconciliationRepositoryError(
                        "sfu_fleet_route_version_conflict"
                    )
                result = db.exec(
                    sa.update(SfuFanoutRouteDB)
                    .where(
                        SfuFanoutRouteDB.id == row.id,
                        SfuFanoutRouteDB.version == row.version,
                    )
                    .values(
                        status="revoked",
                        retention_status="retained",
                        fencing_token=max(row.fencing_token + 1, fencing_token),
                        version=row.version + 1,
                        audit_reason=reason_code,
                        updated_at=now,
                        audited_at=now,
                    )
                )
                if result.rowcount != 1:
                    raise SfuFleetReconciliationRepositoryError(
                        "sfu_fleet_route_version_conflict"
                    )
                db.commit()
                return True
        except SfuFleetReconciliationRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise SfuFleetReconciliationRepositoryError(
                "sfu_fleet_route_store_unavailable"
            ) from exc

    def release_reservation(self, *, item, fencing_token: int, reason_code: str) -> bool:
        route = self._route(item.item_id)
        reservation = self._capacity.get(
            tenant_id=route.tenant_id, room_id=route.session_id
        )
        if reservation is None or reservation.status != "active":
            return False
        return self._capacity.release_for_room(
            tenant_id=route.tenant_id,
            room_id=route.session_id,
            expected_version=reservation.version,
            reason_code=reason_code,
            now=self._clock(),
        )

    def mark_node_unknown(self, *, item, fencing_token: int, reason_code: str) -> bool:
        route = self._route(item.item_id)
        reservation = self._capacity.get(
            tenant_id=route.tenant_id, room_id=route.session_id
        )
        if reservation is None or not reservation.observed_node_id:
            return False
        node = self._nodes.get_node(
            tenant_id=route.tenant_id,
            cluster_id=reservation.cluster_id,
            node_id=reservation.observed_node_id,
        )
        if node is None or node.health_status == "unknown":
            return False
        self._nodes.mark_unknown(
            tenant_id=node.tenant_id,
            cluster_id=node.cluster_id,
            node_id=node.node_id,
            reason=reason_code,
            expected_version=node.version,
            fencing_token=max(node.fencing_token + 1, fencing_token),
        )
        return True

    def reconcile_desired_route(
        self, *, item, fencing_token: int, access_expires_at_ms: int
    ) -> bool:
        return bool(
            self._runtime_routes.reconcile_desired_route(
                item=item,
                fencing_token=fencing_token,
                access_expires_at_ms=access_expires_at_ms,
            )
        )

    def _route(self, route_id: str) -> SfuFanoutRouteDB:
        try:
            with Session(self._engine) as db:
                row = db.get(SfuFanoutRouteDB, route_id)
                if row is None:
                    raise SfuFleetReconciliationRepositoryError(
                        "sfu_fleet_route_not_found"
                    )
                return row
        except SfuFleetReconciliationRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise SfuFleetReconciliationRepositoryError(
                "sfu_fleet_route_store_unavailable"
            ) from exc


class SfuFanoutRouteReconciliationRepositoryAdapter:
    """Maps the durable route repository to page and authority ports."""

    def __init__(
        self,
        *,
        routes: SfuFanoutRouteRepositoryPort,
        projections: SfuRouteReconciliationProjectionPort,
    ) -> None:
        self._routes = routes
        self._projections = projections

    def page(
        self,
        *,
        scope: RouteReconciliationScope,
        phase: ReconciliationPhase,
        cursor: str | None,
        page_size: int,
        lease_fencing_token: str,
        now_ms: int,
    ) -> RouteReconciliationPage:
        raw = self._routes.page(
            SfuBroadcastRoomScope(scope.tenant_ref, scope.room_ref),
            page_size=1,
            cursor=cursor,
        )
        if not raw.items:
            return RouteReconciliationPage((), raw.next_cursor)
        route = raw.items[0]
        should_revoke = route.status != "active" or int(route.expires_at * 1_000) <= now_ms
        expected_phase = (
            ReconciliationPhase.REVOKE if should_revoke else ReconciliationPhase.ENSURE
        )
        if phase is not expected_phase:
            return RouteReconciliationPage((), raw.next_cursor)
        candidate = RouteReconciliationCandidate(
            candidate_ref=route.id,
            key=RouteKeyV1(route.tenant_id, route.session_id, route.route_ref),
            phase=phase,
            resume_cursor=raw.next_cursor,
        )
        return RouteReconciliationPage((candidate,), raw.next_cursor)

    def resolve(
        self,
        *,
        candidate: RouteReconciliationCandidate,
        observation,
        lease: RouteReconciliationLease,
        now_ms: int,
    ) -> RouteReconciliationAuthority:
        route = self._routes.get(
            SfuBroadcastRoomScope(
                candidate.key.tenant_ref, candidate.key.room_ref
            ),
            candidate.candidate_ref,
        )
        expected = observation.projection.version if observation.projection else observation.tombstone_version
        if route is None:
            return RouteReconciliationAuthority(
                candidate_ref=candidate.candidate_ref,
                key=candidate.key,
                desired_state=ReconciliationDesiredState.UNKNOWN,
                desired=None,
                expected_version=expected,
                revoke_version=None,
                operation_id=_operation_id(candidate.candidate_ref, lease.fencing_token),
                lease_fencing_token=lease.fencing_token,
                authorized=False,
                parent_active=False,
                epochs_current=False,
                route_fencing_current=False,
                reason_code="sfu_route_authority_missing",
            )
        state = self._projections.resolve(route=route, now_ms=now_ms)
        revoke_version = None
        if expected is not None and state.desired_state is not ReconciliationDesiredState.ACTIVE:
            revoke_version = RouteVersionV1(
                projection_version=expected.projection_version + 1,
                route_epoch=max(expected.route_epoch + 1, route.route_epoch + 1, 1),
                topology_epoch=max(expected.topology_epoch, route.topology_epoch, 1),
                key_epoch=max(expected.key_epoch, route.key_epoch, 1),
                fencing_token=lease.fencing_token,
            )
        return RouteReconciliationAuthority(
            candidate_ref=candidate.candidate_ref,
            key=candidate.key,
            desired_state=state.desired_state,
            desired=state.desired,
            expected_version=expected,
            revoke_version=revoke_version,
            operation_id=state.operation_id,
            lease_fencing_token=lease.fencing_token,
            authorized=state.authorized,
            parent_active=state.parent_active,
            epochs_current=state.epochs_current,
            route_fencing_current=state.route_fencing_current,
            reason_code=state.reason_code,
        )


class SqlSfuRouteReconciliationScopeRepository:
    def __init__(
        self, *, cursor_signing_key: bytes, db_engine=default_engine
    ) -> None:
        self._engine = db_engine
        self._cursors = _CursorCodec(cursor_signing_key, "route-scopes")

    def page(
        self, *, cursor: str | None, limit: int
    ) -> SfuRouteReconciliationScopePage:
        if not 1 <= limit <= 1_000:
            raise SfuFleetReconciliationRepositoryError(
                "sfu_route_scope_page_limit_invalid"
            )
        after = self._cursors.decode(cursor) if cursor else None
        try:
            with Session(self._engine) as db:
                statement = (
                    select(SfuFanoutRouteDB.tenant_id, SfuFanoutRouteDB.session_id)
                    .where(SfuFanoutRouteDB.retention_status != "purged")
                    .group_by(SfuFanoutRouteDB.tenant_id, SfuFanoutRouteDB.session_id)
                    .order_by(SfuFanoutRouteDB.tenant_id, SfuFanoutRouteDB.session_id)
                    .limit(limit + 1)
                )
                if after is not None:
                    if len(after) != 2:
                        raise SfuFleetReconciliationRepositoryError(
                            "sfu_reconciliation_cursor_invalid"
                        )
                    statement = statement.where(
                        sa.tuple_(
                            SfuFanoutRouteDB.tenant_id,
                            SfuFanoutRouteDB.session_id,
                        ) > after
                    )
                rows = tuple(db.exec(statement).all())
                selected = rows[:limit]
                items = tuple(
                    SfuRouteReconciliationScopeCandidate(
                        scope=RouteReconciliationScope(row[0], row[1]),
                        cursor_after=self._cursors.encode((row[0], row[1])),
                    )
                    for row in selected
                )
                next_cursor = items[-1].cursor_after if len(rows) > limit and items else None
                return SfuRouteReconciliationScopePage(items, next_cursor)
        except SfuFleetReconciliationRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise SfuFleetReconciliationRepositoryError(
                "sfu_route_scope_store_unavailable"
            ) from exc


def _operation_id(candidate_ref: str, fencing_token: str) -> str:
    digest = hashlib.sha256(f"{candidate_ref}\0{fencing_token}".encode()).hexdigest()
    return f"reconcile-{digest[:32]}"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


__all__ = [
    "SfuFanoutRouteReconciliationRepositoryAdapter",
    "SfuFleetReconciliationRepositoryError",
    "SqlSfuFleetReconciliationMutationRepository",
    "SqlSfuFleetReconciliationStateRepository",
    "SqlSfuRouteReconciliationScopeRepository",
]
