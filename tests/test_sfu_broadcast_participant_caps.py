from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from agent.services.semantic_sfu_admission_service import (
    SemanticSfuAdmissionService,
    SfuAdmissionError,
    SfuMembership,
)
from agent.services.sfu_broadcast_capacity_profile_resolver import (
    SfuBroadcastCapacityProfileError,
    SfuBroadcastCapacityProfileResolver,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = ROOT / "config" / "sfu_broadcast_capacity.default.json"
DEFAULT_LIVEKIT = ROOT / "config" / "livekit.semantic-media.yaml"
SECRET = "capacity-test-secret-with-32-bytes"


class Memberships:
    def __init__(self, participants: list[str]) -> None:
        self.rows = {
            participant: SfuMembership(
                "tenant-a", "session-a", participant, "participant", 1, frozenset({"chat", "view_tui"}),
            )
            for participant in participants
        }

    def member(self, *, tenant_id: str, session_id: str, participant_id: str):
        if tenant_id != "tenant-a" or session_id != "session-a":
            return None
        return self.rows.get(participant_id)


def _profile_files(tmp_path: Path, mutate) -> tuple[Path, Path]:
    raw = json.loads(DEFAULT_PROFILE.read_text(encoding="utf-8"))
    mutate(raw)
    profile = tmp_path / "capacity.json"
    livekit = tmp_path / "livekit.yaml"
    profile.write_text(json.dumps(raw), encoding="utf-8")
    livekit.write_text("room:\n  max_participants: 250\n", encoding="utf-8")
    return profile, livekit


def _service(participants: list[str]) -> SemanticSfuAdmissionService:
    return SemanticSfuAdmissionService(
        Memberships(participants),
        enabled=True,
        public_ws_url="wss://sfu.example.test",
        api_key="capacity-test",
        api_secret=SECRET,
        capacity_profile=SfuBroadcastCapacityProfileResolver(DEFAULT_PROFILE, DEFAULT_LIVEKIT),
    )


def _join(service: SemanticSfuAdmissionService, participant: str, revision: int):
    return service.join(
        {
            "session_id": "session-a",
            "membership_epoch": 1,
            "expected_revision": revision,
            "idempotency_key": f"join-{participant}-{revision}",
            "strict_e2ee": True,
            "e2ee_supported": True,
        },
        actor_id=participant,
        tenant_id="tenant-a",
    )


def test_default_profile_is_legacy_safe_and_contains_no_data_destination_limit():
    raw = json.loads(DEFAULT_PROFILE.read_text(encoding="utf-8"))
    resolved = SfuBroadcastCapacityProfileResolver(DEFAULT_PROFILE, DEFAULT_LIVEKIT).resolve()
    assert resolved.angular_display_cap <= resolved.room_admission_cap
    assert resolved.room_admission_cap <= resolved.active_hub_room_cap <= resolved.livekit_hard_cap
    assert resolved.activation_state == "legacy"
    assert resolved.gate_passed is False
    assert raw["test_tiers"] == [10, 25, 50, 100, 250]
    assert not any("destination" in key for key in raw)


@pytest.mark.parametrize(
    ("count", "admitted"),
    [(0, True), (1, True), (7, True), (8, True), (9, False), (10, False),
     (25, False), (50, False), (100, False), (250, False), (251, False)],
)
def test_legacy_boundary_counts(count: int, admitted: bool):
    profile = SfuBroadcastCapacityProfileResolver(DEFAULT_PROFILE, DEFAULT_LIVEKIT).resolve()
    assert profile.allows_participant_count(count) is admitted


def test_livekit_or_angular_config_drift_fails_closed(tmp_path: Path):
    profile, livekit = _profile_files(tmp_path, lambda raw: raw.update(angular_display_cap=9))
    with pytest.raises(SfuBroadcastCapacityProfileError, match="capacity_cap_order_invalid"):
        SfuBroadcastCapacityProfileResolver(profile, livekit).resolve()
    profile, livekit = _profile_files(tmp_path, lambda raw: None)
    livekit.write_text("room:\n  max_participants: 249\n", encoding="utf-8")
    with pytest.raises(SfuBroadcastCapacityProfileError, match="capacity_livekit_config_drift"):
        SfuBroadcastCapacityProfileResolver(profile, livekit).resolve()


def test_candidate_requires_versioned_gate_approval_and_cas(tmp_path: Path):
    def unapproved(raw):
        raw.update(profile_revision=2, active_hub_room_cap=10, room_admission_cap=10, angular_display_cap=10)

    profile, livekit = _profile_files(tmp_path, unapproved)
    with pytest.raises(SfuBroadcastCapacityProfileError, match="capacity_legacy_activation_invalid"):
        SfuBroadcastCapacityProfileResolver(profile, livekit).resolve()

    def approved(raw):
        unapproved(raw)
        raw["activation"] = {
            "state": "candidate", "gate_id": "SFB-GATE-009", "gate_passed": True,
            "candidate_cap": 10, "approval_id": "approval-cap-10",
            "expected_previous_revision": 1, "approved_revision": 2,
        }

    profile, livekit = _profile_files(tmp_path, approved)
    assert SfuBroadcastCapacityProfileResolver(profile, livekit).resolve().active_hub_room_cap == 10


def test_versioned_rollback_restores_legacy_cap(tmp_path: Path):
    def rollback(raw):
        raw["profile_revision"] = 3
        raw["activation"] = {
            "state": "rollback", "gate_id": "SFB-GATE-009", "gate_passed": True,
            "candidate_cap": 10, "approval_id": "approval-cap-10",
            "expected_previous_revision": 2, "approved_revision": 2,
        }

    profile, livekit = _profile_files(tmp_path, rollback)
    resolved = SfuBroadcastCapacityProfileResolver(profile, livekit).resolve()
    assert resolved.activation_state == "rollback" and resolved.active_hub_room_cap == 8


def test_oversize_join_is_rejected_before_token_and_without_partial_state():
    participants = [f"member-{index}" for index in range(9)]
    service = _service(participants)
    revision = 0
    for participant in participants[:8]:
        revision = _join(service, participant, revision)["revision"]
    with pytest.raises(SfuAdmissionError, match="capacity_cap_exceeded"):
        _join(service, participants[8], revision)
    state = service.read_state(
        session_id="session-a", membership_epoch=1, actor_id=participants[8], tenant_id="tenant-a",
    )
    assert state["revision"] == revision and state["joined"] is False


def test_concurrent_join_never_exceeds_active_cap():
    participants = [f"member-{index}" for index in range(9)]
    service = _service(participants)
    revision = 0
    for participant in participants[:7]:
        revision = _join(service, participant, revision)["revision"]
    outcomes: list[tuple[str, object]] = []

    def attempt(participant: str) -> None:
        try:
            outcomes.append((participant, _join(service, participant, revision)))
        except SfuAdmissionError as exc:
            outcomes.append((participant, exc.reason_code))

    threads = [threading.Thread(target=attempt, args=(participant,)) for participant in participants[7:]]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    admitted = [participant for participant, result in outcomes if isinstance(result, dict)]
    assert len(admitted) == 1
    rejected = next(participant for participant in participants[7:] if participant not in admitted)
    with pytest.raises(SfuAdmissionError, match="capacity_cap_exceeded"):
        _join(service, rejected, revision + 1)
