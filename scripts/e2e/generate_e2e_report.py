from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.e2e.e2e_artifacts import sanitize_report_payload, summarize_flows
except ModuleNotFoundError:
    import sys

    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from scripts.e2e.e2e_artifacts import sanitize_report_payload, summarize_flows

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_ROOT = ROOT / "artifacts" / "e2e"


def _root_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def _load_run_reports(artifact_root: Path) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for report_path in sorted(artifact_root.glob("*/report.json")):
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        payload["__report_path"] = _root_relative(report_path)
        reports.append(payload)
    return reports


def _flatten_flows(run_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for run_report in run_reports:
        run_id = str(run_report.get("run_id", "unknown"))
        report_path = str(run_report.get("__report_path", ""))
        for flow in list(run_report.get("flows") or []):
            flattened.append(
                {
                    "run_id": run_id,
                    "report_path": report_path,
                    "flow_id": str(flow.get("flow_id", "")),
                    "status": str(flow.get("status", "")),
                    "blocking": bool(flow.get("blocking")),
                    "logs": list(flow.get("logs") or []),
                    "snapshots": list(flow.get("snapshots") or []),
                    "screenshots": list(flow.get("screenshots") or []),
                    "videos": list(flow.get("videos") or []),
                    "trace_bundle_refs": list(flow.get("trace_bundle_refs") or []),
                    "artifact_refs": list(flow.get("artifact_refs") or []),
                    "notes": list(flow.get("notes") or []),
                }
            )
    return flattened


def _optional_video_summary(flows: list[dict[str, Any]]) -> dict[str, int]:
    recorded = sum(1 for flow in flows if flow.get("videos"))
    skipped = sum(
        1
        for flow in flows
        if not flow.get("videos")
        and any(
            "optional video" in str(note).lower() or "video capture disabled" in str(note).lower()
            for note in flow.get("notes", [])
        )
    )
    return {"recorded": recorded, "skipped": skipped}


def generate_report_payload(artifact_root: Path) -> dict[str, Any]:
    run_reports = _load_run_reports(artifact_root)
    flows = _flatten_flows(run_reports)
    summary = summarize_flows(flows)
    video_summary = _optional_video_summary(flows)
    payload = {
        "schema": "e2e_visual_report_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifact_root": _root_relative(artifact_root),
        "run_report_count": len(run_reports),
        "summary": summary,
        "optional_videos": video_summary,
        "flows": flows,
    }
    return sanitize_report_payload(payload)


def generate_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    optional_videos = payload.get("optional_videos", {})
    lines = [
        "# E2E Visual Evidence Report",
        "",
        f"- generated_at: {payload.get('generated_at')}",
        f"- run_report_count: {payload.get('run_report_count')}",
        (
            f"- summary: total={summary.get('total', 0)} passed={summary.get('passed', 0)} "
            f"failed={summary.get('failed', 0)} skipped={summary.get('skipped', 0)} "
            f"advisory={summary.get('advisory', 0)} blocking_failed={summary.get('blocking_failed', 0)}"
        ),
        (
            f"- optional_videos: recorded={optional_videos.get('recorded', 0)} "
            f"skipped={optional_videos.get('skipped', 0)}"
        ),
        "",
        "| run_id | flow_id | status | blocking | snapshots | screenshots | videos | report_path |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for flow in payload.get("flows", []):
        lines.append(
            (
                "| {run_id} | {flow_id} | {status} | {blocking} | {snapshots} | "
                "{screenshots} | {videos} | {report_path} |"
            ).format(
                run_id=flow.get("run_id", ""),
                flow_id=flow.get("flow_id", ""),
                status=flow.get("status", ""),
                blocking="yes" if flow.get("blocking") else "no",
                snapshots=len(flow.get("snapshots", [])),
                screenshots=len(flow.get("screenshots", [])),
                videos=len(flow.get("videos", [])),
                report_path=flow.get("report_path", ""),
            )
        )
    lines.append("")
    lines.append("## Highlights")
    lines.append("")
    lines.append(f"- Blocking failures: {summary.get('blocking_failed', 0)}")
    lines.append(f"- Advisory visual diffs/skips: {summary.get('advisory', 0)}")
    lines.append(f"- Optional videos skipped: {optional_videos.get('skipped', 0)}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate compact aggregate E2E visual evidence report.")
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--out-json", default="artifacts/e2e/aggregate_report.json")
    parser.add_argument("--out-md", default="artifacts/e2e/aggregate_report.md")
    args = parser.parse_args()

    artifact_root = Path(args.artifact_root)
    if not artifact_root.is_absolute():
        artifact_root = ROOT / artifact_root
    artifact_root.mkdir(parents=True, exist_ok=True)

    payload = generate_report_payload(artifact_root)
    markdown = generate_markdown(payload)

    out_json = Path(args.out_json)
    if not out_json.is_absolute():
        out_json = ROOT / out_json
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    out_md = Path(args.out_md)
    if not out_md.is_absolute():
        out_md = ROOT / out_md
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(markdown, encoding="utf-8")

    print(f"aggregate_json={out_json.relative_to(ROOT)}")
    print(f"aggregate_md={out_md.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
