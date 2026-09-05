from __future__ import annotations

import json
from pathlib import Path

from scripts.hub_browser_test_evidence import HubBrowserTestRun, canonical_digest, source_digest
from scripts.run_peer_media_alternative_gate import EXPECTED_ENGINES, _evaluation_complete

ROOT = Path(__file__).parents[1]


def test_hub_browser_test_run_issues_test_only_identity_before_result(tmp_path: Path) -> None:
    reservation = HubBrowserTestRun.reserve(
        root=ROOT,
        registry_db=tmp_path / "evidence.sqlite3",
        task_id="DPM-POC-003",
        source_paths=(Path("AGENTS.md"),),
        execution_profile={"browser": "headless"},
        environment={"sandbox": "test"},
    )
    assert reservation.assignment["run_id"] == reservation.run_id
    assert reservation.assignment["evidence_scope"] == "test"
    evidence = reservation.complete({"status": "measured"}, succeeded=True)
    assert evidence["run_id"] == reservation.run_id
    assert evidence["synthetic"] is True
    assert evidence["production_release_eligible"] is False
    assert evidence["production_release_reason"] == "evidence_run_test_scope_forbidden"


def test_alternative_gate_requires_both_engines_and_bounded_decisions() -> None:
    measurements = [
        {"engine": engine, "decision": "bounded_experiment", "humanInterventionRequired": False}
        for engine in sorted(EXPECTED_ENGINES)
    ]
    assert _evaluation_complete(0, measurements) is True
    assert _evaluation_complete(1, measurements) is False
    assert _evaluation_complete(0, measurements[:1]) is False
    assert _evaluation_complete(0, [{**measurements[0], "decision": "go"}, measurements[1]]) is False


def test_committed_alternative_report_is_test_scoped_and_not_promotable() -> None:
    report = json.loads(
        (ROOT / "artifacts/test-gates/peer-media-alternative.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "passed"
    assert report["decision"] in {"bounded_experiment", "no_go"}
    assert {row["engine"] for row in report["measurements"]} == EXPECTED_ENGINES
    assert all(row["humanInterventionRequired"] is False for row in report["measurements"])
    assert report["evidence"]["scope"] == "test"
    assert report["evidence"]["synthetic"] is True
    assert report["evidence"]["production_release_eligible"] is False


def test_source_digest_is_path_bound_and_canonical() -> None:
    digest = source_digest(ROOT, (Path("AGENTS.md"),))
    assert len(digest) == 64
    assert digest != source_digest(ROOT, (Path("docs/decisions/ADR-decentralized-peer-overlay.md"),))
    assert canonical_digest({"a": 1, "b": 2}) == canonical_digest({"b": 2, "a": 1})
