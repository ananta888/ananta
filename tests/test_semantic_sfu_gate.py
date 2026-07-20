from __future__ import annotations

import json
from pathlib import Path

from scripts.run_semantic_sfu_gate import evidence_binding, recompute_evidence, static_reasons

ROOT = Path(__file__).resolve().parents[1]


def evidence():
    spike = json.loads((ROOT / "artifacts/domain/semantic-sfu-three-peer.json").read_text(encoding="utf-8"))
    load = json.loads((ROOT / "artifacts/domain/semantic-sfu-load.json").read_text(encoding="utf-8"))
    failover = json.loads((ROOT / "artifacts/domain/semantic-sfu-live-failover.json").read_text(encoding="utf-8"))
    group = json.loads((ROOT / "artifacts/e2e/semantic-media-group-report.json").read_text(encoding="utf-8"))
    return spike, load, failover, group


def test_sfu_gate_recomputes_raw_evidence_and_static_boundaries():
    spike, load, failover, group = evidence()
    report = json.loads((ROOT / "artifacts/test-gates/semantic-sfu.json").read_text(encoding="utf-8"))
    reasons = sorted(set(recompute_evidence(spike, load, failover, group) + static_reasons(ROOT)))
    assert report["reasons"] == reasons
    assert report["verdict"] == ("pass" if not reasons else "fail")
    source_digest, config_digest = evidence_binding(spike, load, failover, group)
    assert report["source_sha256"] == source_digest
    assert report["config_sha256"] == config_digest


def test_tampered_top_level_verdict_cannot_open_gate():
    spike, load, failover, group = evidence()
    spike["verdict"] = "pass"
    spike["engines"][0]["peers"] = [row for row in spike["engines"][0]["peers"] if row["identity"] != "wrong-key-probe"]
    reasons = recompute_evidence(spike, load, failover, group)
    assert any(reason.endswith("e2ee_negative_probe_failed") for reason in reasons)


def test_load_overclaim_or_missing_level_fails_gate():
    spike, load, failover, group = evidence()
    load["scope"] = "production capacity"
    load["levels"] = load["levels"][:1]
    assert {"load_scope_overclaimed", "load_levels_missing"}.issubset(recompute_evidence(spike, load, failover, group))


def test_live_failover_top_level_pass_cannot_hide_a_stale_key_decode() -> None:
    spike, load, failover, group = evidence()
    failover["verdict"] = "pass"
    failover["external_live_failover_verified"] = True
    failover["engines"][0]["recovery"]["stale_key_probe_decoded_samples"] = 1
    reasons = recompute_evidence(spike, load, failover, group)
    assert "chromium_fresh_epoch_recovery_missing" in reasons
    assert "live_failover_report_decision_stale" in reasons


def test_missing_turn_capture_cannot_open_sfu_gate() -> None:
    spike, load, failover, group = evidence()
    group["measurements"]["turn_boundary_packet_count"] = 0

    assert "sfu_turn_live_traffic_missing" in recompute_evidence(spike, load, failover, group)
