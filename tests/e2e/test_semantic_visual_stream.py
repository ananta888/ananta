"""Two-client lifecycle acceptance for fail-closed semantic visual control.

This deterministic harness exercises browser-local executor states and the Hub
authority boundary without pretending that the not-yet-wired M3 SFU port is a
deployed browser transport.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts.run_semantic_visual_gate import evaluate_visual_gate

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ("static_ui", "text_scroll", "cursor_animation", "camera", "scene_cut", "strong_noise")


@dataclass
class BrowserExecutor:
    peer_id: str
    epoch: int = 1
    mode: str = "ordinary"
    consent: bool = True
    connected: bool = True
    reference: bool = False

    def apply_hub_contract(self, *, release: bool, lease: bool, active: bool) -> None:
        self.mode = "semantic" if release and lease and active and self.consent and self.connected else "ordinary"

    def observe(self) -> None:
        self.mode = "observe_only" if self.consent and self.connected else "ordinary"

    def recover(self, stage: str) -> None:
        self.mode = {"region": "semantic", "reference": "semantic"}.get(stage, "ordinary")
        if stage == "reference":
            self.reference = True

    def revoke(self) -> None:
        self.consent = False
        self.reference = False
        self.mode = "ordinary"

    def reconnect(self) -> None:
        self.epoch += 1
        self.reference = False
        self.mode = "ordinary"


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_two_client_observe_active_recovery_revoke_reconnect_and_fallback(scenario: str) -> None:
    sender, receiver = BrowserExecutor("sender"), BrowserExecutor("receiver")
    sender.observe()
    receiver.observe()
    assert (sender.mode, receiver.mode) == ("observe_only", "observe_only")

    # Hypothetical passed-gate authority fixture exercises active lifecycle;
    # production activation below remains denied by the real NO-GO artifact.
    sender.reference = receiver.reference = True
    sender.apply_hub_contract(release=True, lease=True, active=True)
    receiver.apply_hub_contract(release=True, lease=True, active=True)
    assert (sender.mode, receiver.mode) == ("semantic", "semantic")
    receiver.recover("reference" if scenario in {"scene_cut", "camera"} else "region")
    assert receiver.mode == "semantic"
    receiver.recover("ordinary")
    assert receiver.mode == "ordinary"
    receiver.revoke()
    assert not receiver.consent and receiver.mode == "ordinary" and not receiver.reference
    sender.reconnect()
    assert sender.epoch == 2 and sender.mode == "ordinary" and not sender.reference


def test_real_no_go_gate_denies_active_but_preserves_ordinary_for_both_clients() -> None:
    spike = json.loads((ROOT / "artifacts/domain/semantic-visual-feasibility.json").read_text())
    benchmark = json.loads((ROOT / "artifacts/domain/semantic-visual-benchmark.json").read_text())
    gate = evaluate_visual_gate(spike, benchmark)
    assert not gate["passed"] and gate["ordinary_fallback_required"]
    peers = [BrowserExecutor("sender"), BrowserExecutor("receiver")]
    for peer in peers:
        peer.apply_hub_contract(release=gate["passed"], lease=True, active=True)
        assert peer.mode == "ordinary"


def test_missing_sfu_admission_port_fails_closed_without_affecting_ordinary() -> None:
    peers = [BrowserExecutor("sender"), BrowserExecutor("receiver")]
    sfu_admission_available = False
    for peer in peers:
        peer.apply_hub_contract(release=True, lease=sfu_admission_available, active=True)
        assert peer.mode == "ordinary"
