from __future__ import annotations

import base64
import uuid

from sqlmodel import SQLModel

from agent.database import engine
from agent.repositories.webrtc_epoch_repository import WebrtcEpochRepository
from agent.services.webrtc_epoch_service import WebrtcEpochService


def test_epoch_handover_is_monotonic_and_rejects_split_brain() -> None:
    SQLModel.metadata.create_all(engine)
    now = [1000.0]
    scope = f"session-{uuid.uuid4()}"
    service = WebrtcEpochService(WebrtcEpochRepository(), clock=lambda: now[0])
    first = service.claim_epoch(scope_kind="session", scope_id=scope, hub_id="hub-a", lease_seconds=30)
    assert first.ok and first.epoch == 1
    conflict = service.claim_epoch(scope_kind="session", scope_id=scope, hub_id="hub-b", lease_seconds=30)
    assert (conflict.ok, conflict.reason) == (False, "epoch_split_brain")
    now[0] += 31
    takeover = service.claim_epoch(scope_kind="session", scope_id=scope, hub_id="hub-b", lease_seconds=30)
    assert takeover.ok and takeover.epoch == 2
    assert takeover.ownership_changed is True


def test_sender_traffic_class_windows_survive_service_recreation() -> None:
    SQLModel.metadata.create_all(engine)
    scope = f"session-{uuid.uuid4()}"
    service = WebrtcEpochService(WebrtcEpochRepository(), clock=lambda: 1000)
    assert service.claim_epoch(scope_kind="session", scope_id=scope, hub_id="hub").ok
    assert (
        service.accept_sequence(
            scope_kind="session",
            scope_id=scope,
            epoch=1,
            sender_id="alice",
            authenticated_sender_id="alice",
            traffic_class="semantic",
            sequence=1,
            nonce_b64=base64.b64encode(b"n" * 12).decode(),
        ).reason_code
        == "ok"
    )
    restarted = WebrtcEpochService(WebrtcEpochRepository(), clock=lambda: 1001)
    assert (
        restarted.accept_sequence(
            scope_kind="session",
            scope_id=scope,
            epoch=1,
            sender_id="alice",
            authenticated_sender_id="alice",
            traffic_class="semantic",
            sequence=1,
            nonce_b64=base64.b64encode(b"n" * 12).decode(),
        ).reason_code
        == "sequence_duplicate"
    )
    assert (
        restarted.accept_sequence(
            scope_kind="session",
            scope_id=scope,
            epoch=1,
            sender_id="alice",
            authenticated_sender_id="alice",
            traffic_class="semantic",
            sequence=2,
            nonce_b64=base64.b64encode(b"n" * 12).decode(),
        ).reason_code
        == "nonce_reuse"
    )
    assert (
        restarted.accept_sequence(
            scope_kind="session",
            scope_id=scope,
            epoch=1,
            sender_id="alice",
            authenticated_sender_id="mallory",
            traffic_class="semantic",
            sequence=2,
        ).reason_code
        == "sender_mismatch"
    )
    assert (
        restarted.accept_sequence(
            scope_kind="session",
            scope_id=scope,
            epoch=2,
            sender_id="alice",
            authenticated_sender_id="alice",
            traffic_class="semantic",
            sequence=2,
        ).reason_code
        == "epoch_future"
    )
    restarted.close_scope("session", scope)
    restarted.close_scope("session", scope)
    assert (
        restarted.accept_sequence(
            scope_kind="session",
            scope_id=scope,
            epoch=1,
            sender_id="alice",
            authenticated_sender_id="alice",
            traffic_class="semantic",
            sequence=2,
        ).reason_code
        == "scope_not_active"
    )
