from __future__ import annotations

from types import SimpleNamespace

from agent.services.semantic_media_program_evidence import source_hash
from agent.services.speech_privacy_lifecycle_service import SPEECH_DATA_PHASES
from agent.services.speech_privacy_production_composition import PRODUCTION_SPEECH_PRIVACY_PHASES
from scripts import run_speech_privacy_gate as gate


def test_gate_is_source_bound_to_exact_product_phase_contract() -> None:
    evidence = gate.run(execute=False)

    assert evidence.status == "unverified"
    assert evidence.source_sha256 == source_hash(gate.ROOT, gate.SOURCES)
    assert gate.CONFIG["phase_count"] == 11
    assert set(gate.CONFIG["phases"]) == SPEECH_DATA_PHASES == PRODUCTION_SPEECH_PRIVACY_PHASES
    assert gate._composition_reason_codes() == []


def test_executed_gate_reports_real_suite_results_without_subprocess_content(monkeypatch) -> None:
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=b"private canary", stderr=b""),
    )

    evidence = gate.run(execute=True)

    assert evidence.status == "passed"
    assert evidence.measurements["phase_count"] == 11
    assert evidence.measurements["python_suite_count"] == len(gate.PYTHON_SUITES)
    assert "private canary" not in repr(evidence.as_document())


def test_gate_fails_closed_when_product_composition_is_unavailable(monkeypatch) -> None:
    def unavailable() -> None:
        raise RuntimeError("composition missing")

    monkeypatch.setattr(gate, "assert_speech_privacy_production_composition", unavailable)
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
    )

    evidence = gate.run(execute=True)

    assert evidence.status == "failed"
    assert evidence.reason_codes == ("speech_privacy_product_composition_missing",)
