"""Adaptive test-fix rounds: an opencode test lane feeding bounded failures back into the loop.

One round is one test-lane run against a read-only snapshot (a workspace *copy*, so
read-only is enforced by construction, not by prompt promises):

- The Hub picks the minimum test program from existing levers (policy ``test_profiles``
  plus optional ``minimum_args``) and hands the runner its adaptive rights in the task
  description: run the minimum first, expand on suspicion, abort early on first real
  failures.
- The runner answers exactly one JSON report (``ananta_test_report.v1``). The report is
  the only cross-role contract: parse-validated, bounded, task-fenced.
- ``next_round_action`` is the whole state machine: ``pass`` resumes the original item
  (acceptance review / publication), ``fix`` dispatches an ananta-worker fix round on the
  *carried* workspace with the failure feedback as its brief, ``exhausted`` fails the item
  with a precise reason after ``max_rounds`` test runs.

Everything here is policy-pre-authorized configuration or pure logic; the round plumbing
lives in the coordinator.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Mapping

_MAX_ANSWER_CHARS = 60_000
_MAX_LIST_ITEMS = 60
_MAX_FAILURES = 50
_MAX_FEEDBACK_CHARS = 1200
_REPORT_DIR = ".ananta"
_REPORT_FILE = "test-report.json"
_REPORT_SCHEMA = "ananta_test_report.v1"
_MARKER = "Run exactly:"


def minimum_command(loop_cfg: Mapping[str, Any], test_profiles: Mapping[str, Any]) -> str:
    """Assembled minimum program: profile command plus operator-approved extra args."""
    profile_name = str(loop_cfg.get("minimum_profile") or "all-tests")
    profile = (test_profiles or {}).get(profile_name) or {}
    command = str(profile.get("command") or "").strip()
    if not command:
        raise ValueError("test_fix_loop_minimum_profile_unavailable")
    extra = [str(arg) for arg in (loop_cfg.get("minimum_args") or [])]
    return " ".join([command, *extra])


def build_runner_description(
    loop_cfg: Mapping[str, Any],
    *,
    todo_id: str,
    title: str,
    minimum_cmd: str,
) -> str:
    """Task description of the runner task: declared command, adaptive rights, report schema.

    The ``Run exactly:`` line must stay a single line: the declared-command marker span
    ends at the newline, and the propose fallback needs that span to produce a proposal
    command for the runner task.
    """
    example = {
        "passed": False,
        "aborted_early": True,
        "ran": ["python -m pytest -q -o addopts= -n 4 tests"],
        "expanded": ["python -m pytest -q -o addopts= -n 4 tests/agent"],
        "failures": [
            {"file": "tests/example/test_sample.py", "test": "test_something", "error_tail": "AssertionError: ..."}
        ],
    }
    rounds = int(loop_cfg.get("max_rounds") or 3)
    return (
        f"Adaptive test lane for TODO {todo_id}: {title}\n"
        "Du bist der testende Freilaeufer auf einem lesbaren Snapshot der Arbeit. "
        "Nimm keine Aenderungen an Dateien vor; nur lesen und Tests ausfuehren.\n"
        f"{_MARKER} {minimum_cmd}\n"
        "Reihenfolge und Rechte:\n"
        "1. Fuehre zuerst exakt den deklarierten Mindestbefehl aus.\n"
        "2. Bei Verdacht (geaenderte Module, Fehler mit Reichweite) darfst du ERWEITERN: "
        "weitere passende Bereiche mit demselben Runner fahren; alles in 'ran' bzw. 'expanded' notieren.\n"
        "3. Bei ersten echten Fehlern darfst du VORZEITIG ABRECHEN (aborted_early=true) statt die "
        "gesamte Suite zu fahren, und die bereits gefundenen Fehler sofort melden - sie werden vor "
        f"dem naechsten Lauf korrigiert. Maximal {rounds} Test-Laeufe.\n"
        "4. Beende mit GENAU EINEM JSON-Objekt als letzter Antwort (sonst nichts):\n"
        f"{json.dumps(example, ensure_ascii=False)}\n"
        "Felder: passed (bool), aborted_early (bool), ran (gefahren Befehle/Roots), "
        "expanded (zusaetzlich erweiterte), failures (max 50 Eintraege, error_tail max 600 Zeichen)."
    )


def parse_test_report(text: str) -> dict | None:
    """Bounded parse of the runner's answer; anything contradictory yields ``None``."""
    raw = str(text or "")[:_MAX_ANSWER_CHARS]
    candidates = [raw.strip()]
    positions = [match.start() for match in re.finditer(r"\{", raw)][-16:]
    candidates.extend(raw[start:] for start in reversed(positions))
    payload = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            payload = parsed
            break
    if payload is None:
        return None
    passed = payload.get("passed")
    aborted = payload.get("aborted_early", False)
    ran = payload.get("ran")
    expanded = payload.get("expanded", [])
    failures = payload.get("failures")
    if not isinstance(passed, bool) or not isinstance(aborted, bool):
        return None
    for name, items in (("ran", ran), ("expanded", expanded)):
        if not isinstance(items, list) or len(items) > _MAX_LIST_ITEMS:
            return None
        if not all(isinstance(entry, str) and len(entry) <= 200 for entry in items):
            return None
    if len(ran) < 1:
        return None
    if not isinstance(failures, list) or len(failures) > _MAX_FAILURES:
        return None
    normalized: list[dict[str, str]] = []
    for failure in failures:
        if not isinstance(failure, dict):
            return None
        file = failure.get("file")
        test = failure.get("test")
        if not isinstance(file, str) or not isinstance(test, str):
            return None
        normalized.append(
            {
                "file": file[:400],
                "test": test[:400],
                "error_tail": str(failure.get("error_tail") or "")[:600],
            }
        )
    if passed and normalized:
        return None
    if not passed and not normalized:
        return None
    if aborted and not normalized:
        return None
    outcome = "passed" if passed else ("aborted_early" if aborted else "failures")
    return {
        "passed": passed,
        "aborted_early": aborted,
        "ran": list(ran),
        "expanded": list(expanded),
        "failures": normalized,
        "outcome": outcome,
    }


def looks_like_test_report(text: str) -> bool:
    return parse_test_report(text) is not None


def write_test_report(workspace: Path, *, task_id: str, answer: str) -> bool:
    """Persist a parsed report into the task workspace; ``False`` keeps the honest failure."""
    report = parse_test_report(answer)
    if report is None:
        return False
    target = Path(workspace) / _REPORT_DIR / _REPORT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {"schema": _REPORT_SCHEMA, "task_id": str(task_id), "at": int(time.time()), "report": report},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return True


def read_test_report(workspace: Path, *, task_id: str) -> dict | None:
    """Read back a report fenced to this task (a stale artifact can never answer for a retry)."""
    try:
        data = json.loads((Path(workspace) / _REPORT_DIR / _REPORT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != _REPORT_SCHEMA:
        return None
    if str(data.get("task_id") or "") != str(task_id):
        return None
    report = data.get("report")
    if not isinstance(report, dict) or not isinstance(report.get("passed"), bool):
        return None
    if not isinstance(report.get("failures"), list) or not isinstance(report.get("ran"), list):
        return None
    return report


def next_round_action(item: Mapping[str, Any], report: Mapping[str, Any], loop_cfg: Mapping[str, Any]) -> str:
    """``pass`` / ``fix`` / ``exhausted``.

    ``test_fix_rounds`` counts *completed test runs* and is incremented before this call,
    so ``max_rounds`` bounds test runs: the last failing run exhausts the loop instead of
    queueing another fix.
    """
    if report.get("passed"):
        return "pass"
    rounds = int(item.get("test_fix_rounds") or 0)
    if rounds >= max(1, int(loop_cfg.get("max_rounds") or 3)):
        return "exhausted"
    return "fix"


def failure_feedback(item: Mapping[str, Any], report: Mapping[str, Any]) -> dict:
    """Bounded memory of the test-lane result that travels with the fix round."""
    return {
        "todo_id": item.get("todo_id"),
        "round": int(item.get("test_fix_rounds") or 0),
        "aborted_early": bool(report.get("aborted_early")),
        "ran": list(report.get("ran") or [])[:10],
        "expanded": list(report.get("expanded") or [])[:10],
        "failures": list(report.get("failures") or [])[:10],
    }


def fix_description(item: Mapping[str, Any], report: Mapping[str, Any]) -> str:
    """Base task text plus the failure block, mirroring ``describe_for_retry`` bounds."""
    base = str(item.get("description") or "")
    block = (
        "\n\nPrevious test-lane failures (fix these on the carried workspace, then the lane runs again):\n"
        + json.dumps(failure_feedback(item, report), ensure_ascii=True, sort_keys=True)[:_MAX_FEEDBACK_CHARS]
    )
    return (base + block)[:4000]
