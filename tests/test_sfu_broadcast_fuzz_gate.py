from __future__ import annotations

import json
from types import SimpleNamespace

from scripts import run_sfu_broadcast_fuzz_gate as fuzz_gate


def test_duplicate_key_rejection_is_a_successful_protection(tmp_path) -> None:
    output = tmp_path / "fuzz-gate.json"

    report = fuzz_gate.run_gate(
        profile_path=fuzz_gate.DEFAULT_PROFILE,
        output=output,
        plan_only=True,
    )

    assert report["status"] == "unverified"
    assert report["release_blocking"] is True
    assert report["known_blockers"] == []
    assert json.loads(output.read_text(encoding="utf-8")) == report
    artifact_text = output.read_text(encoding="utf-8")
    assert '"first"' not in artifact_text
    assert '"second"' not in artifact_text


def test_duplicate_key_acceptance_fails_closed_and_writes_artifact(
    tmp_path, monkeypatch
) -> None:
    class _PermissiveMaterializer:
        def materialize(self, raw_document, descriptor, compatibility):
            return SimpleNamespace(document={"scope": "second"})

    monkeypatch.setattr(
        fuzz_gate, "SfuBroadcastContractMaterializer", _PermissiveMaterializer
    )
    monkeypatch.setattr(
        fuzz_gate,
        "run_bounded_command",
        lambda *args, **kwargs: SimpleNamespace(
            elapsed_ms=1,
            cpu_seconds=0.1,
            peak_rss_bytes=1024,
            exit_code=0,
            timed_out=False,
        ),
    )
    output = tmp_path / "fuzz-gate.json"

    report = fuzz_gate.run_gate(
        profile_path=fuzz_gate.DEFAULT_PROFILE,
        output=output,
        plan_only=False,
    )

    assert report["status"] == "failed"
    assert report["release_blocking"] is True
    assert report["reason_codes"] == ["duplicate_json_key_not_rejected"]
    assert report["known_blockers"] == ["duplicate_json_key_not_rejected"]
    assert json.loads(output.read_text(encoding="utf-8")) == report
