import base64
import hashlib
import hmac
import json

import pytest

from agent.services.sfu_broadcast_operations_read_model import (
    InMemorySfuBroadcastOperationsSnapshotPort,
    SfuBroadcastOperationsError,
    SfuBroadcastOperationsPrincipal,
    SfuBroadcastOperationsQuery,
    SfuBroadcastOperationsReadModel,
    SfuBroadcastOperationsRecord,
    SfuBroadcastOperationsSnapshot,
)


def _record(room="room-a", owner="user-a", receiver="receiver-a", tenant="tenant-a", cohort=10):
    return SfuBroadcastOperationsRecord(
        1000, tenant, "eu-1", room, owner, receiver, cohort,
        "active", "applied", "current", "sfu", "healthy",
        "high", "medium", "medium", {"low": 4, "medium": 6}, 3, "none",
        500_000, 1_000_000, 0, "converged", "none", "broadcast_25", "observe_only",
    )


def _model(records, now=None, **kwargs):
    clock = now or [1100.0]
    source = InMemorySfuBroadcastOperationsSnapshotPort(SfuBroadcastOperationsSnapshot("snapshot-1", tuple(records)))
    return SfuBroadcastOperationsReadModel(source=source, diagnostic_secret=b"o" * 32, clock=lambda: clock[0], **kwargs), source


def test_user_sees_only_owned_room_and_output_has_no_original_refs_or_counts():
    model, _ = _model([_record(), _record(room="room-b", owner="user-b", receiver="receiver-b")])
    page = model.query(SfuBroadcastOperationsPrincipal("user-a", "user", ("tenant-a",)), SfuBroadcastOperationsQuery())
    encoded = json.dumps(page.public(), sort_keys=True)
    assert len(page.items) == 1
    for secret in ("room-a", "receiver-a", "tenant-a", "user-a", "snapshot-1"):
        assert secret not in encoded
    assert page.items[0]["traffic"]["egress_bucket"] == "le_1m"


def test_operator_scope_suppression_pagination_and_snapshot_cursor_are_enforced():
    clock = [1100.0]
    model, source = _model(
        [_record(receiver="receiver-a"), _record(receiver="receiver-b"), _record(receiver="small", cohort=9)],
        now=clock,
    )
    principal = SfuBroadcastOperationsPrincipal("operator-a", "operator", ("tenant-a",), ("eu-1",))
    first = model.query(principal, SfuBroadcastOperationsQuery(page_size=1))
    assert len(first.items) == 1 and first.next_cursor
    second = model.query(principal, SfuBroadcastOperationsQuery(page_size=1, cursor=first.next_cursor))
    assert len(second.items) == 1 and second.next_cursor is None
    source.replace(SfuBroadcastOperationsSnapshot("snapshot-2", (_record(),)))
    with pytest.raises(SfuBroadcastOperationsError, match="cursor_stale"):
        model.query(principal, SfuBroadcastOperationsQuery(page_size=1, cursor=first.next_cursor))
    with pytest.raises(SfuBroadcastOperationsError, match="tenant_forbidden"):
        model.query(principal, SfuBroadcastOperationsQuery(tenant_ref="tenant-b"))


def test_query_rate_limit_is_principal_bound_and_content_free():
    model, _ = _model([_record()], max_queries_per_minute=1)
    principal = SfuBroadcastOperationsPrincipal("user-a", "user", ("tenant-a",))
    model.query(principal, SfuBroadcastOperationsQuery())
    with pytest.raises(SfuBroadcastOperationsError, match="query_rate_exceeded") as exc:
        model.query(principal, SfuBroadcastOperationsQuery())
    assert "user-a" not in str(exc.value)


def test_signed_cursor_with_non_numeric_view_time_or_invalid_utf8_fails_closed():
    model, _ = _model([_record(receiver="receiver-a"), _record(receiver="receiver-b")])
    principal = SfuBroadcastOperationsPrincipal("user-a", "user", ("tenant-a",))
    first = model.query(principal, SfuBroadcastOperationsQuery(page_size=1))
    raw = base64.urlsafe_b64decode(first.next_cursor + "=" * (-len(first.next_cursor) % 4))
    value = json.loads(raw[:-32])
    value["view_at"] = "later"
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(b"o" * 32, b"sfu-operations-cursor-v1\0" + payload, hashlib.sha256).digest()
    invalid_time = base64.urlsafe_b64encode(payload + signature).decode("ascii").rstrip("=")
    with pytest.raises(SfuBroadcastOperationsError, match="cursor_invalid"):
        model.query(principal, SfuBroadcastOperationsQuery(page_size=1, cursor=invalid_time))

    invalid_utf8_payload = b"\xff"
    signature = hmac.new(
        b"o" * 32,
        b"sfu-operations-cursor-v1\0" + invalid_utf8_payload,
        hashlib.sha256,
    ).digest()
    invalid_utf8 = base64.urlsafe_b64encode(invalid_utf8_payload + signature).decode("ascii").rstrip("=")
    with pytest.raises(SfuBroadcastOperationsError, match="cursor_invalid"):
        model.query(principal, SfuBroadcastOperationsQuery(page_size=1, cursor=invalid_utf8))


def test_authorized_scope_is_applied_before_source_record_limit():
    model, _ = _model(
        [
            _record(room="room-b", owner="user-b", tenant="tenant-b"),
            _record(room="room-a", owner="user-a", tenant="tenant-a"),
        ],
        max_source_records=1,
    )
    principal = SfuBroadcastOperationsPrincipal(
        "operator-a", "operator", ("tenant-a",), ("eu-1",)
    )
    page = model.query(principal, SfuBroadcastOperationsQuery())
    assert len(page.items) == 1
