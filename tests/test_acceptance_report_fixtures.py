"""RCA-003: Tests validating the structure of minimal acceptance report fixtures."""
from __future__ import annotations

import json
import pathlib

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "reports"

REPORT_FIXTURES = [
    "acceptance_report_success.json",
    "acceptance_report_provider_unavailable.json",
    "acceptance_report_stalled.json",
]


def _load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


class TestFixtureStructure:
    def test_all_fixtures_exist(self):
        for name in REPORT_FIXTURES:
            assert (FIXTURES_DIR / name).exists(), f"Missing fixture: {name}"

    def test_fixtures_have_summary_and_runs(self):
        for name in REPORT_FIXTURES:
            doc = _load(name)
            assert "summary" in doc, f"{name}: missing summary"
            assert "runs" in doc, f"{name}: missing runs"
            assert isinstance(doc["runs"], list), f"{name}: runs must be a list"
            assert len(doc["runs"]) >= 1, f"{name}: runs must be non-empty"

    def test_success_fixture_passes(self):
        doc = _load("acceptance_report_success.json")
        assert doc["summary"]["completed_runs"] == 1
        assert doc["runs"][0]["passed"] is True
        assert doc["runs"][0]["final_goal_status"] == "completed"

    def test_provider_unavailable_fixture_fails(self):
        doc = _load("acceptance_report_provider_unavailable.json")
        assert doc["summary"]["completed_runs"] == 0
        assert doc["runs"][0]["passed"] is False
        assert doc["runs"][0].get("failure_reason") == "provider_unavailable"

    def test_stalled_fixture_is_in_planning(self):
        doc = _load("acceptance_report_stalled.json")
        assert doc["runs"][0]["final_goal_status"] == "planning"
        assert doc["runs"][0]["passed"] is False
        assert "stalled" in (doc["runs"][0].get("failure_reason") or "")

    def test_fixtures_are_deterministic_no_timestamps(self):
        for name in REPORT_FIXTURES:
            doc = _load(name)
            text = (FIXTURES_DIR / name).read_text()
            assert "generated_at" not in text, f"{name}: volatile timestamp found"
