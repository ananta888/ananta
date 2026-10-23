"""Recover a finished workspace-mutation run whose dispatch handshake was lost.

The worker writes its durable result into the workspace (``.ananta/mutation-report.json``
and ``.ananta/last-final-answer.json``) before the Hub ever sees it. A transport
deadline, a restart or a lost response must never discard completed work: when the
artifacts prove the loop finished, the Hub replays the lost verification step and
completes the dispatch bookkeeping from ground truth.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPORT_SCHEMA = "ananta_worker_mutation_report.v1"
ANSWER_SCHEMA = "ananta_worker_final_answer.v1"
COMPLETED_OUTCOMES = frozenset({"final_answer"})


def _read_artifact(path: Path, schema: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != schema:
        return None
    return payload


def completed_run_artifacts(workspace: Path) -> dict[str, Any] | None:
    """The durable result of a completed run, or ``None`` when nothing usable exists.

    Only a loop that reached its natural ``final_answer`` counts: aborted outcomes
    (max_iterations, blocked, repeated rejections) are failures to re-run, never
    work to salvage.
    """
    report = _read_artifact(workspace / ".ananta" / "mutation-report.json", REPORT_SCHEMA)
    if report is None or str(report.get("outcome")) not in COMPLETED_OUTCOMES:
        return None
    answer = _read_artifact(workspace / ".ananta" / "last-final-answer.json", ANSWER_SCHEMA)
    if answer is None:
        return None
    return {"report": report, "answer": answer}
