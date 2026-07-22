"""Privacy-bounded, RBAC-aware SFU broadcast operations read model."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol, Sequence


class SfuBroadcastOperationsError(ValueError):
    def __init__(self, reason_code: str, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuBroadcastOperationsPrincipal:
    subject: str
    role: str
    tenant_scopes: tuple[str, ...] = ()
    region_scopes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SfuBroadcastOperationsQuery:
    room_ref: str | None = None
    receiver_ref: str | None = None
    tenant_ref: str | None = None
    region: str | None = None
    page_size: int = 25
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class SfuBroadcastOperationsRecord:
    observed_at_seconds: float
    tenant_ref: str
    region: str
    room_ref: str
    owner_subject: str
    receiver_ref: str
    cohort_size: int
    group_status: str
    route_status: str
    epoch_class: str
    topology: str
    health: str
    requested_layer: str
    allowed_layer: str
    effective_layer: str
    layer_distribution: Mapping[str, int]
    queue_depth: int
    drop_reason: str
    ingress_bytes_per_second: int
    egress_bytes_per_second: int
    turn_bytes_per_second: int
    rekey_status: str
    failover_status: str
    capacity_profile: str
    gate_state: str


@dataclass(frozen=True, slots=True)
class SfuBroadcastOperationsSnapshot:
    version: str
    records: tuple[SfuBroadcastOperationsRecord, ...]


@dataclass(frozen=True, slots=True)
class SfuBroadcastOperationsSourceScope:
    tenant_refs: tuple[str, ...] | None = None
    region_refs: tuple[str, ...] | None = None
    owner_subject: str | None = None
    room_ref: str | None = None
    receiver_ref: str | None = None


class SfuBroadcastOperationsSnapshotPort(Protocol):
    def load(
        self,
        *,
        snapshot_version: str | None,
        max_records: int,
        scope: SfuBroadcastOperationsSourceScope | None = None,
    ) -> SfuBroadcastOperationsSnapshot: ...


@dataclass(frozen=True, slots=True)
class SfuBroadcastOperationsPage:
    snapshot_ref: str
    items: tuple[Mapping[str, object], ...]
    next_cursor: str | None
    reason_code: str = "sfu_operations_snapshot_read"

    def public(self) -> dict[str, object]:
        return {
            "ok": True,
            "reason_code": self.reason_code,
            "snapshot_ref": self.snapshot_ref,
            "items": [dict(item) for item in self.items],
            "next_cursor": self.next_cursor,
        }


class InMemorySfuBroadcastOperationsSnapshotPort:
    """Single-process test/development source; not a durable production read model."""

    def __init__(self, snapshot: SfuBroadcastOperationsSnapshot) -> None:
        self._snapshot = snapshot

    def replace(self, snapshot: SfuBroadcastOperationsSnapshot) -> None:
        self._snapshot = snapshot

    def load(
        self,
        *,
        snapshot_version: str | None,
        max_records: int,
        scope: SfuBroadcastOperationsSourceScope | None = None,
    ) -> SfuBroadcastOperationsSnapshot:
        if snapshot_version is not None and snapshot_version != self._snapshot.version:
            raise SfuBroadcastOperationsError("sfu_operations_cursor_stale", 409)
        records = self._snapshot.records
        if scope is not None:
            records = tuple(
                record for record in records if _record_in_source_scope(record, scope)
            )
        return SfuBroadcastOperationsSnapshot(
            self._snapshot.version,
            records[:max_records],
        )


class SfuBroadcastOperationsReadModel:
    _ROLES = frozenset({"user", "operator", "admin"})
    _GROUP = frozenset({"pending", "active", "reconciling", "revoked", "failed", "unknown"})
    _ROUTE = frozenset({"desired", "applied", "revoked", "failed", "unknown"})
    _EPOCH = frozenset({"current", "stale", "future", "unknown"})
    _TOPOLOGY = frozenset({"direct", "sfu", "turn", "unknown"})
    _HEALTH = frozenset({"healthy", "degraded", "draining", "unavailable", "unknown"})
    _LAYERS = frozenset({"none", "low", "medium", "high", "unknown"})
    _DROP = frozenset({"none", "quota_exceeded", "expired", "stale_epoch", "policy_denied", "backpressure", "unknown"})
    _REKEY = frozenset({"idle", "pending", "converged", "failed", "unknown"})
    _FAILOVER = frozenset({"none", "fenced", "recovering", "recovered", "failed", "unknown"})
    _CAPACITY = frozenset({"legacy_8", "broadcast_10", "broadcast_25", "broadcast_50", "broadcast_100", "broadcast_250", "unknown"})
    _GATES = frozenset({"go", "no_go", "observe_only", "blocked", "unknown"})

    def __init__(
        self,
        *,
        source: SfuBroadcastOperationsSnapshotPort,
        diagnostic_secret: bytes,
        clock: Callable[[], float] = time.time,
        min_cohort_size: int = 10,
        retention_seconds: int = 3600,
        cursor_ttl_seconds: int = 60,
        pseudonym_rotation_seconds: int = 900,
        max_page_size: int = 100,
        max_source_records: int = 2000,
        max_queries_per_minute: int = 30,
        max_rate_limit_subjects: int = 4096,
    ) -> None:
        if len(diagnostic_secret) < 32:
            raise SfuBroadcastOperationsError("sfu_operations_diagnostic_secret_invalid", 503)
        if min(min_cohort_size, retention_seconds, cursor_ttl_seconds, pseudonym_rotation_seconds) <= 0:
            raise SfuBroadcastOperationsError("sfu_operations_limits_invalid", 503)
        if min(max_page_size, max_source_records, max_queries_per_minute, max_rate_limit_subjects) <= 0:
            raise SfuBroadcastOperationsError("sfu_operations_limits_invalid", 503)
        self._source = source
        self._secret = bytes(diagnostic_secret)
        self._clock = clock
        self._min_cohort = min_cohort_size
        self._retention = retention_seconds
        self._cursor_ttl = cursor_ttl_seconds
        self._rotation = pseudonym_rotation_seconds
        self._max_page = max_page_size
        self._max_source = max_source_records
        self._max_queries = max_queries_per_minute
        self._max_subjects = max_rate_limit_subjects
        self._queries: OrderedDict[str, deque[float]] = OrderedDict()

    def query(
        self,
        principal: SfuBroadcastOperationsPrincipal,
        query: SfuBroadcastOperationsQuery,
    ) -> SfuBroadcastOperationsPage:
        self._validate(principal, query)
        now = float(self._clock())
        principal_key = self._pseudonym("principal", principal.subject, int(now))
        self._consume_query_budget(principal_key, now)
        query_digest = self._query_digest(principal, query)
        offset = 0
        snapshot_version: str | None = None
        view_at = now
        if query.cursor:
            cursor = self._decode_cursor(query.cursor, query_digest, now)
            offset = cursor["offset"]
            snapshot_version = cursor["snapshot"]
            view_at = cursor["view_at"]
        snapshot = self._source.load(
            snapshot_version=snapshot_version,
            max_records=self._max_source,
            scope=self._source_scope(principal, query),
        )
        if snapshot_version is not None and snapshot.version != snapshot_version:
            raise SfuBroadcastOperationsError("sfu_operations_cursor_stale", 409)
        authorized = [
            record
            for record in snapshot.records
            if self._authorized(principal, query, record)
            and 0 <= view_at - record.observed_at_seconds <= self._retention
            and record.cohort_size >= self._min_cohort
        ]
        public_items = [self._public_record(record, view_at) for record in authorized]
        public_items.sort(key=lambda item: (str(item["room_diagnostic_ref"]), str(item["receiver_diagnostic_ref"])))
        page = tuple(public_items[offset : offset + query.page_size])
        next_offset = offset + len(page)
        next_cursor = None
        if next_offset < len(public_items):
            next_cursor = self._encode_cursor(snapshot.version, next_offset, query_digest, view_at, now)
        return SfuBroadcastOperationsPage(
            snapshot_ref=self._pseudonym("snapshot", snapshot.version, int(view_at)),
            items=page,
            next_cursor=next_cursor,
        )

    @staticmethod
    def _source_scope(
        principal: SfuBroadcastOperationsPrincipal,
        query: SfuBroadcastOperationsQuery,
    ) -> SfuBroadcastOperationsSourceScope:
        def narrowed(scopes: tuple[str, ...], requested: str | None) -> tuple[str, ...] | None:
            if requested is not None:
                return (requested,)
            return None if "*" in scopes or not scopes else scopes

        return SfuBroadcastOperationsSourceScope(
            tenant_refs=narrowed(principal.tenant_scopes, query.tenant_ref),
            region_refs=narrowed(principal.region_scopes, query.region),
            owner_subject=principal.subject if principal.role == "user" else None,
            room_ref=query.room_ref,
            receiver_ref=query.receiver_ref,
        )

    def _validate(self, principal: SfuBroadcastOperationsPrincipal, query: SfuBroadcastOperationsQuery) -> None:
        if not self._safe_ref(principal.subject) or principal.role not in self._ROLES:
            raise SfuBroadcastOperationsError("sfu_operations_identity_invalid", 401)
        if isinstance(query.page_size, bool) or not 1 <= query.page_size <= self._max_page:
            raise SfuBroadcastOperationsError("sfu_operations_page_size_invalid")
        for value in (query.room_ref, query.receiver_ref, query.tenant_ref, query.region):
            if value is not None and not self._safe_ref(value):
                raise SfuBroadcastOperationsError("sfu_operations_filter_invalid")
        if query.cursor is not None and (not isinstance(query.cursor, str) or len(query.cursor) > 2048):
            raise SfuBroadcastOperationsError("sfu_operations_cursor_invalid")
        if principal.role in {"operator", "admin"}:
            if not principal.tenant_scopes or not principal.region_scopes:
                raise SfuBroadcastOperationsError("sfu_operations_scope_required", 403)
            if query.tenant_ref and not self._scope_contains(principal.tenant_scopes, query.tenant_ref):
                raise SfuBroadcastOperationsError("sfu_operations_tenant_forbidden", 403)
            if query.region and not self._scope_contains(principal.region_scopes, query.region):
                raise SfuBroadcastOperationsError("sfu_operations_region_forbidden", 403)

    def _authorized(
        self,
        principal: SfuBroadcastOperationsPrincipal,
        query: SfuBroadcastOperationsQuery,
        record: SfuBroadcastOperationsRecord,
    ) -> bool:
        if query.room_ref and record.room_ref != query.room_ref:
            return False
        if query.receiver_ref and record.receiver_ref != query.receiver_ref:
            return False
        if query.tenant_ref and record.tenant_ref != query.tenant_ref:
            return False
        if query.region and record.region != query.region:
            return False
        if principal.role == "user":
            return record.owner_subject == principal.subject
        return self._scope_contains(principal.tenant_scopes, record.tenant_ref) and self._scope_contains(
            principal.region_scopes, record.region
        )

    def _public_record(self, record: SfuBroadcastOperationsRecord, view_at: float) -> Mapping[str, object]:
        layers = {
            layer: self._count_bucket(record.layer_distribution.get(layer, 0))
            for layer in ("none", "low", "medium", "high")
        }
        return {
            "room_diagnostic_ref": self._pseudonym("room", record.room_ref, int(view_at)),
            "receiver_diagnostic_ref": self._pseudonym("receiver", record.receiver_ref, int(view_at)),
            "region_diagnostic_ref": self._pseudonym("region", record.region, int(view_at)),
            "group_status": self._closed(record.group_status, self._GROUP),
            "route_status": self._closed(record.route_status, self._ROUTE),
            "epoch_class": self._closed(record.epoch_class, self._EPOCH),
            "topology": self._closed(record.topology, self._TOPOLOGY),
            "health": self._closed(record.health, self._HEALTH),
            "layers": {
                "requested": self._closed(record.requested_layer, self._LAYERS),
                "allowed": self._closed(record.allowed_layer, self._LAYERS),
                "effective": self._closed(record.effective_layer, self._LAYERS),
                "distribution": layers,
            },
            "queue": {
                "depth_bucket": self._count_bucket(record.queue_depth),
                "drop_reason": self._closed(record.drop_reason, self._DROP),
            },
            "traffic": {
                "ingress_bucket": self._rate_bucket(record.ingress_bytes_per_second),
                "egress_bucket": self._rate_bucket(record.egress_bytes_per_second),
                "turn_bucket": self._rate_bucket(record.turn_bytes_per_second),
            },
            "rekey_status": self._closed(record.rekey_status, self._REKEY),
            "failover_status": self._closed(record.failover_status, self._FAILOVER),
            "capacity_profile": self._closed(record.capacity_profile, self._CAPACITY),
            "gate_state": self._closed(record.gate_state, self._GATES),
        }

    def _consume_query_budget(self, principal_key: str, now: float) -> None:
        values = self._queries.get(principal_key)
        if values is None:
            if len(self._queries) >= self._max_subjects:
                self._queries.popitem(last=False)
            values = deque()
            self._queries[principal_key] = values
        else:
            self._queries.move_to_end(principal_key)
        while values and values[0] <= now - 60:
            values.popleft()
        if len(values) >= self._max_queries:
            raise SfuBroadcastOperationsError("sfu_operations_query_rate_exceeded", 429)
        values.append(now)

    def _query_digest(
        self,
        principal: SfuBroadcastOperationsPrincipal,
        query: SfuBroadcastOperationsQuery,
    ) -> str:
        document = {
            "principal": self._digest(principal.subject),
            "role": principal.role,
            "room": self._digest(query.room_ref or ""),
            "receiver": self._digest(query.receiver_ref or ""),
            "tenant": self._digest(query.tenant_ref or ""),
            "region": self._digest(query.region or ""),
            "page_size": query.page_size,
        }
        return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()

    def _encode_cursor(
        self,
        snapshot: str,
        offset: int,
        query_digest: str,
        view_at: float,
        now: float,
    ) -> str:
        payload = json.dumps(
            {
                "snapshot": snapshot,
                "offset": offset,
                "query": query_digest,
                "view_at": view_at,
                "expires_at": now + self._cursor_ttl,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.new(self._secret, b"sfu-operations-cursor-v1\0" + payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload + signature).decode("ascii").rstrip("=")

    def _decode_cursor(self, encoded: str, query_digest: str, now: float) -> dict:
        try:
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            payload, signature = raw[:-32], raw[-32:]
            expected = hmac.new(self._secret, b"sfu-operations-cursor-v1\0" + payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            value = json.loads(payload)
            if set(value) != {"snapshot", "offset", "query", "view_at", "expires_at"}:
                raise ValueError
            if (
                not isinstance(value["query"], str)
                or value["query"] != query_digest
                or isinstance(value["view_at"], bool)
                or not isinstance(value["view_at"], (int, float))
                or not math.isfinite(float(value["view_at"]))
                or value["view_at"] < 0
                or isinstance(value["expires_at"], bool)
                or not isinstance(value["expires_at"], (int, float))
                or not math.isfinite(float(value["expires_at"]))
                or value["expires_at"] < now
            ):
                raise ValueError
            if (
                not self._safe_ref(value["snapshot"])
                or isinstance(value["offset"], bool)
                or not isinstance(value["offset"], int)
                or value["offset"] < 0
            ):
                raise ValueError
            return value
        except (ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SfuBroadcastOperationsError("sfu_operations_cursor_invalid", 409) from exc

    def _pseudonym(self, domain: str, value: str, now: int) -> str:
        epoch = now // self._rotation
        message = f"sfu-operations-{domain}-v1\0{epoch}\0{value}".encode("utf-8")
        digest = hmac.new(self._secret, message, hashlib.sha256).hexdigest()[:24]
        return f"sfo1.{epoch}.{digest}"

    def _digest(self, value: str) -> str:
        return hmac.new(self._secret, b"sfu-operations-filter-v1\0" + value.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _safe_ref(value: object) -> bool:
        return isinstance(value, str) and 0 < len(value.encode("utf-8")) <= 256 and not any(ord(char) < 32 for char in value)

    @staticmethod
    def _scope_contains(scopes: Sequence[str], value: str) -> bool:
        return "*" in scopes or value in scopes

    @staticmethod
    def _closed(value: object, allowed: frozenset[str]) -> str:
        return value if isinstance(value, str) and value in allowed else "unknown"

    @staticmethod
    def _count_bucket(value: object) -> str:
        number = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
        for upper, label in ((0, "0"), (9, "1-9"), (24, "10-24"), (49, "25-49"), (99, "50-99"), (249, "100-249")):
            if number <= upper:
                return label
        return "250+"

    @staticmethod
    def _rate_bucket(value: object) -> str:
        number = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
        for upper, label in ((0, "0"), (128_000, "le_128k"), (512_000, "le_512k"), (1_000_000, "le_1m"), (5_000_000, "le_5m"), (25_000_000, "le_25m")):
            if number <= upper:
                return label
        return "gt_25m"


def _record_in_source_scope(
    record: SfuBroadcastOperationsRecord,
    scope: SfuBroadcastOperationsSourceScope,
) -> bool:
    return (
        (scope.tenant_refs is None or record.tenant_ref in scope.tenant_refs)
        and (scope.region_refs is None or record.region in scope.region_refs)
        and (scope.owner_subject is None or record.owner_subject == scope.owner_subject)
        and (scope.room_ref is None or record.room_ref == scope.room_ref)
        and (scope.receiver_ref is None or record.receiver_ref == scope.receiver_ref)
    )


__all__ = [
    "InMemorySfuBroadcastOperationsSnapshotPort",
    "SfuBroadcastOperationsError",
    "SfuBroadcastOperationsPage",
    "SfuBroadcastOperationsPrincipal",
    "SfuBroadcastOperationsQuery",
    "SfuBroadcastOperationsReadModel",
    "SfuBroadcastOperationsRecord",
    "SfuBroadcastOperationsSnapshot",
    "SfuBroadcastOperationsSnapshotPort",
    "SfuBroadcastOperationsSourceScope",
]
