from __future__ import annotations

import json
from pathlib import Path

from agent.services.helpcenter_analyzer_service import analyze_helpcenter_message
from agent.services.helpcenter_manual_input_service import create_manual_helpcenter_message
from agent.services.helpcenter_report_writer_service import write_helpcenter_report


def _message() -> dict:
    return create_manual_helpcenter_message(
        title="CI failure",
        text="pytest failed in tests/test_api.py",
        severity="error",
        source_ref="manual://ci/1",
    )


def _analysis(message: dict) -> dict:
    return analyze_helpcenter_message(message, log_text="FAILURES\nAssertionError\ntests/test_api.py")


def test_report_writer_creates_markdown_with_expected_sections(tmp_path: Path) -> None:
    message = _message()
    analysis = _analysis(message)
    result = write_helpcenter_report(message=message, analysis=analysis, repo_root=tmp_path)
    md_path = tmp_path / result["markdown_ref"]
    content = md_path.read_text(encoding="utf-8")
    assert "## Source" in content
    assert "## Failure Summary" in content
    assert "## Likely Causes" in content
    assert "analysis only, no auto fix" in content


def test_report_writer_writes_json_valid_payload(tmp_path: Path) -> None:
    message = _message()
    analysis = _analysis(message)
    result = write_helpcenter_report(message=message, analysis=analysis, repo_root=tmp_path)
    json_path = tmp_path / result["json_ref"]
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["analysis_id"] == analysis["analysis_id"]
    assert payload["no_auto_fix"] is True
    assert payload["source_kind"] == message["source_kind"]
    assert payload["redaction_status"] == message["redaction_status"]


def test_report_writer_updates_index_and_duplicate_versioning(tmp_path: Path) -> None:
    message = _message()
    first = write_helpcenter_report(message=message, analysis=_analysis(message), repo_root=tmp_path)
    write_helpcenter_report(message=message, analysis=_analysis(message), repo_root=tmp_path)
    index_path = tmp_path / first["index_ref"]
    index_payload = json.loads(index_path.read_text(encoding="utf-8"))
    rows = [dict(item) for item in list(index_payload.get("reports") or []) if isinstance(item, dict)]
    assert len(rows) == 2
    assert rows[-1]["version"] == 2
    assert rows[-1]["duplicate_of_analysis_id"] == first["analysis_id"]
    assert rows[-1]["json_ref"].endswith(".json")
