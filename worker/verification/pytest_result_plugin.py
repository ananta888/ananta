"""Internal Pytest plugin emitting one bounded verification result record."""

from __future__ import annotations

import json

from worker.verification.pytest_results import RESULT_PREFIX


def pytest_terminal_summary(terminalreporter) -> None:
    stats = terminalreporter.stats

    def reports(name: str):
        return list(stats.get(name, ()))

    failed_reports = [report for report in reports("failed") if getattr(report, "when", "call") == "call"]
    passed_reports = [report for report in reports("passed") if getattr(report, "when", "call") == "call"]
    error_reports = reports("error")
    skipped_reports = reports("skipped")
    payload = {
        "collected": int(getattr(terminalreporter, "_numcollected", 0)),
        "passed": len({getattr(report, "nodeid", "") for report in passed_reports}),
        "failed": len({getattr(report, "nodeid", "") for report in failed_reports}),
        "errors": len(error_reports),
        "skipped": len({getattr(report, "nodeid", "") for report in skipped_reports}),
        "failed_node_ids": sorted({str(getattr(report, "nodeid", ""))[:512] for report in failed_reports}),
    }
    terminalreporter.write_line(RESULT_PREFIX + json.dumps(payload, sort_keys=True, separators=(",", ":")))


__all__ = ["pytest_terminal_summary"]
