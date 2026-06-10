#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from firefox_automation._config import DEFAULT_PHASES
from firefox_automation._browser_utils import list_visible_errors


def parse_phases(raw: str) -> List[str]:
    allowed = {"setup", "goal", "execution", "benchmark", "review", "all"}
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    if not parts:
        return DEFAULT_PHASES[:]
    unknown = [p for p in parts if p not in allowed]
    if unknown:
        raise ValueError(f"Unknown phases: {', '.join(unknown)}")
    if "all" in parts:
        return DEFAULT_PHASES[:]
    return parts


def default_goal_text_for_benchmark(task_kind: str) -> str:
    normalized = str(task_kind or "").strip().lower()
    if normalized == "analysis":
        return "Analyze a small Python Fibonacci helper, identify two concrete improvements, and provide a short implementation plan."
    if normalized == "planning":
        return "Create a concrete implementation plan for a Python Fibonacci helper with tests, validation steps, and a short delivery summary."
    if normalized == "review":
        return "Review a small Python Fibonacci helper change, identify issues, propose fixes, and summarize verification steps."
    return (
        "Implement a small Python Fibonacci helper, add unit tests, and provide a short summary of the changed files and validation."
    )


def record_step(report: dict, phase: str, step: str, started_at: float, ok: bool, details: Optional[dict] = None):
    ended = time.time()
    report["steps"].append(
        {
            "phase": phase,
            "step": step,
            "status": "ok" if ok else "failed",
            "started_at": datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat(),
            "ended_at": datetime.fromtimestamp(ended, tz=timezone.utc).isoformat(),
            "duration_ms": int((ended - started_at) * 1000),
            "details": details or {},
        }
    )


def gate_visible_errors(session_id: str, report: dict, phase: str, hard_fail: bool):
    visible_errors = list_visible_errors(session_id)
    if not visible_errors:
        return
    has_401 = any("401" in item["text"] for item in visible_errors)
    report["ui_signals"]["visible_errors"].extend(visible_errors)
    report["ui_signals"]["visible_errors_contains_401"] = report["ui_signals"]["visible_errors_contains_401"] or has_401
    print("visible_errors", len(visible_errors), "contains_401", has_401, flush=True)
    print("visible_error_texts", json.dumps(visible_errors, ensure_ascii=True), flush=True)
    if hard_fail:
        raise RuntimeError(f"Visible UI errors detected in phase '{phase}' (401={has_401})")

