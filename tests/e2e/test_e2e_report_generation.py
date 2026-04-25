from __future__ import annotations

import json
from pathlib import Path

from scripts.e2e.e2e_artifacts import build_report, make_flow_entry, write_report
from scripts.e2e.generate_e2e_report import generate_markdown, generate_report_payload
from tests.e2e.harness import E2EHarness


def test_generate_e2e_report_aggregates_flows_and_highlights(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts" / "e2e"
    harness = E2EHarness(artifact_root=artifact_root)
    harness.run_cli_golden_path(run_id="agg-run-01")
    harness.run_web_ui_screenshots(run_id="agg-run-02", web_available=False)

    optional_video_flow = make_flow_entry(
        flow_id="tui-video-optional",
        status="advisory",
        blocking=False,
        logs=[],
        snapshots=[],
        screenshots=[],
        videos=[],
        trace_bundle_refs=[],
        artifact_refs=[],
        notes=["optional video skipped in CI"],
    )
    write_report("agg-run-03", build_report("agg-run-03", [optional_video_flow]), artifact_root=artifact_root)

    payload = generate_report_payload(artifact_root)
    markdown = generate_markdown(payload)

    assert payload["schema"] == "e2e_visual_report_v1"
    assert payload["summary"]["total"] == len(payload["flows"])
    assert payload["summary"]["passed"] >= 1
    assert payload["summary"]["advisory"] >= 1
    assert payload["optional_videos"]["skipped"] >= 1
    assert "Blocking failures:" in markdown
    assert "Optional videos skipped:" in markdown

    json.dumps(payload)
