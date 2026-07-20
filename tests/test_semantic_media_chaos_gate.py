from __future__ import annotations

import json
from pathlib import Path

from scripts.run_semantic_media_chaos_gate import evaluate_external_live_failover

ROOT = Path(__file__).resolve().parents[1]


def test_chaos_gate_accepts_only_recomputed_external_failover_evidence() -> None:
    verified, reasons, report = evaluate_external_live_failover(
        ROOT / "artifacts/domain/semantic-sfu-live-failover.json"
    )
    assert verified is True
    assert reasons == ()
    assert report["external_live_failover_verified"] is True


def test_chaos_gate_rejects_tampered_top_level_pass(tmp_path: Path) -> None:
    source = ROOT / "artifacts/domain/semantic-sfu-live-failover.json"
    report = json.loads(source.read_text(encoding="utf-8"))
    report["engines"][0]["recovery"]["stale_key_probe_decoded_samples"] = 1
    report["external_live_failover_verified"] = True
    report["verdict"] = "pass"
    target = tmp_path / "tampered.json"
    target.write_text(json.dumps(report), encoding="utf-8")

    verified, reasons, _ = evaluate_external_live_failover(target)
    assert verified is False
    assert "chromium_fresh_epoch_recovery_missing" in reasons
    assert "external_live_failover_decision_stale" in reasons
