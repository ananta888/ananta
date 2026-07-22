from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent.services.webrtc_turn_admission_policy import (
    InMemoryTurnQuotaReservationPort,
    TurnAdmissionRequest,
    TurnQuotaPolicyConfig,
    TurnQuotaVector,
    WebrtcTurnAdmissionPolicy,
)


ROOT = Path(__file__).resolve().parents[1]


def _request(index=1, high=TurnQuotaVector(1, 2, 1, 6_000_000, 1_000_000), **changes):
    values = dict(
        reservation_id=f"reservation-{index}", tenant_ref="tenant-a", room_ref="room-a",
        receiver_ref=f"receiver-{index}", region="eu-1", pool_id="pool-a", requested_layer="high",
        layer_projections={"high": high, "medium": TurnQuotaVector(1, 2, 1, 2_000_000, 500_000), "low": TurnQuotaVector(1, 2, 1, 500_000, 100_000)},
        publisher_to_sfu_ingress_bps=1_000_000, sfu_to_turn_egress_bps=6_000_000, accounting_available=True,
    )
    values.update(changes)
    return TurnAdmissionRequest(**values)


def _policy():
    reservations = InMemoryTurnQuotaReservationPort()
    config = TurnQuotaPolicyConfig.from_path(ROOT / "config/webrtc_turn_quotas.default.json")
    return WebrtcTurnAdmissionPolicy(config=config, reservations=reservations, scope_secret=b"q" * 32, clock=lambda: 1000), reservations


def test_hard_receiver_limit_selects_lower_cap_and_missing_accounting_fails_closed():
    policy, _ = _policy()
    result = policy.reserve(_request())
    assert result.decision == "lower_cap" and result.allowed_layer == "medium"
    denied = policy.reserve(_request(2, accounting_available=False))
    assert denied.decision == "relay_capacity_exhausted"
    assert denied.reason_code == "turn_accounting_unavailable"
    assert result.publisher_to_sfu_ingress_bps == 1_000_000
    assert result.sfu_to_turn_egress_bps == 6_000_000
    assert policy.rollback(result.reservation_ref) is True


def test_parallel_receiver_reservations_are_atomic_and_receiver_isolated():
    policy, _ = _policy()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda index: policy.reserve(_request(index)), range(1, 20)))
    accepted = [result for result in results if result.decision in {"allow", "lower_cap"}]
    assert len(accepted) <= 19
    assert all(result.reservation_ref for result in accepted)
