#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


TERMINAL = {"completed", "failed", "cancelled", "aborted", "timeout"}


@dataclass
class PhaseDurations:
    queued_to_assigned: float | None
    assigned_to_propose_done: float | None
    propose_to_execute_done: float | None
    execute_to_terminal: float | None
    total: float | None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def _first_event_ts(history: list[dict[str, Any]], names: set[str]) -> float | None:
    ts = []
    for e in history:
        if str(e.get("event_type") or "") in names:
            try:
                ts.append(float(e.get("timestamp") or 0.0))
            except Exception:
                pass
    return min(ts) if ts else None


def _last_event_ts(history: list[dict[str, Any]], names: set[str]) -> float | None:
    ts = []
    for e in history:
        if str(e.get("event_type") or "") in names:
            try:
                ts.append(float(e.get("timestamp") or 0.0))
            except Exception:
                pass
    return max(ts) if ts else None


def _safe_delta(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    d = b - a
    return d if d >= 0 else None


_REASON_CATEGORIES = (
    "llm_wait",
    "provider_unavailable",
    "worker_unavailable",
    "dependency_blocked",
    "policy_blocked",
    "unknown",
)


def _classify_idle_reason_from_history(history: list[dict[str, Any]], phase_seconds: float | None) -> str:
    """ARD-004: Classify the dominant reason for idle/queue time using event history."""
    if phase_seconds is None:
        return "unknown"
    event_types = {str(e.get("event_type") or "") for e in history}
    if "circuit_open" in event_types or "provider_circuit_breaker_open" in event_types:
        return "provider_unavailable"
    if "blocked_by_dependency" in event_types or "task_blocked" in event_types:
        return "dependency_blocked"
    if "no_workers_available" in event_types or "worker_unavailable" in event_types:
        return "worker_unavailable"
    if "policy_blocked" in event_types or "policy_denied" in event_types:
        return "policy_blocked"
    if "autopilot_strategy_attempt" in event_types or "autopilot_decision" in event_types:
        return "llm_wait"
    return "unknown"


def _build_reason_breakdown(
    phase_rows: list[PhaseDurations],
    task_histories: list[list[dict[str, Any]]],
) -> dict[str, Any]:
    """ARD-004: Aggregate idle time by classified reason category."""
    reason_totals: dict[str, float] = {r: 0.0 for r in _REASON_CATEGORIES}
    reason_counts: dict[str, int] = {r: 0 for r in _REASON_CATEGORIES}

    for phase, history in zip(phase_rows, task_histories):
        q2a = phase.queued_to_assigned
        reason = _classify_idle_reason_from_history(history, q2a)
        if q2a is not None:
            reason_totals[reason] = reason_totals.get(reason, 0.0) + q2a
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    return {
        r: {
            "total_seconds": round(reason_totals[r], 3),
            "task_count": reason_counts[r],
        }
        for r in _REASON_CATEGORIES
    }


def _summarize_llm_call_profile(entries: list[dict[str, Any]]) -> dict[str, Any]:
    llm_calls_total = 0
    llm_calls_real = 0
    llm_calls_synthetic = 0
    llm_calls_estimated = 0
    llm_latency_ms_real: list[int] = []
    llm_prompt_tokens_real: list[int] = []
    llm_completion_tokens_real: list[int] = []
    llm_success = 0
    llm_fail = 0
    llm_success_real = 0
    llm_fail_real = 0
    llm_by_model: dict[str, int] = {}
    llm_by_model_real: dict[str, int] = {}
    llm_by_model_synthetic: dict[str, int] = {}
    llm_by_source: dict[str, int] = {}

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        llm_calls_total += 1
        model = str(entry.get("model") or "").strip() or "<unknown>"
        llm_by_model[model] = int(llm_by_model.get(model) or 0) + 1
        source = str(entry.get("source") or "").strip() or "unknown"
        llm_by_source[source] = int(llm_by_source.get(source) or 0) + 1
        estimated = bool(entry.get("estimated"))
        if estimated:
            llm_calls_estimated += 1
        if source == "orchestrator_synthetic" or estimated:
            llm_calls_synthetic += 1
            llm_by_model_synthetic[model] = int(llm_by_model_synthetic.get(model) or 0) + 1
        else:
            llm_calls_real += 1
            llm_by_model_real[model] = int(llm_by_model_real.get(model) or 0) + 1

        if bool(entry.get("success")):
            llm_success += 1
            if not estimated:
                llm_success_real += 1
        else:
            llm_fail += 1
            if not estimated:
                llm_fail_real += 1
        ms = entry.get("latency_ms")
        if not estimated and isinstance(ms, int):
            llm_latency_ms_real.append(ms)
        pt = entry.get("prompt_tokens")
        ct = entry.get("completion_tokens")
        if not estimated and isinstance(pt, int):
            llm_prompt_tokens_real.append(pt)
        if not estimated and isinstance(ct, int):
            llm_completion_tokens_real.append(ct)

    return {
        "calls_seen_total": llm_calls_total,
        "calls_seen_real": llm_calls_real,
        "calls_seen_synthetic": llm_calls_synthetic,
        "calls_seen_estimated": llm_calls_estimated,
        "success_count": llm_success,
        "fail_count": llm_fail,
        "success_count_real": llm_success_real,
        "fail_count_real": llm_fail_real,
        "latency_ms_mean_real": _mean(llm_latency_ms_real) if llm_latency_ms_real else None,
        "latency_ms_median_real": _median(llm_latency_ms_real) if llm_latency_ms_real else None,
        "prompt_tokens_mean_real": _mean(llm_prompt_tokens_real) if llm_prompt_tokens_real else None,
        "completion_tokens_mean_real": _mean(llm_completion_tokens_real) if llm_completion_tokens_real else None,
        "by_model": llm_by_model,
        "by_model_real": llm_by_model_real,
        "by_model_synthetic": llm_by_model_synthetic,
        "by_source": llm_by_source,
    }


def _fetch_json(session: requests.Session, url: str, headers: dict[str, str]) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            r = session.get(url, headers=headers, timeout=20)
            r.raise_for_status()
            return dict(r.json() or {})
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < 3:
                time.sleep(min(2 * attempt, 5))
                continue
            raise
    if last_exc:
        raise last_exc
    return {}


def main() -> int:
    p = argparse.ArgumentParser(description="Goal latency diagnostics (E2E phases + LLM profile + autopilot throughput)")
    p.add_argument("--base-url", default="http://localhost:5000")
    p.add_argument("--user", default="admin")
    p.add_argument("--password", default="AnantaLocalDevAdmin123!")
    p.add_argument("--goal-id", default=None)
    p.add_argument("--out", default=str(Path("artifacts") / "goal_latency_diagnostics.json"))
    args = p.parse_args()

    base = args.base_url.rstrip("/")
    s = requests.Session()
    login_payload: dict[str, Any] | None = None
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            login = s.post(f"{base}/login", json={"username": args.user, "password": args.password}, timeout=20)
            login.raise_for_status()
            login_payload = dict(login.json() or {})
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < 3:
                time.sleep(min(2 * attempt, 5))
                continue
            raise
    if login_payload is None and last_exc:
        raise last_exc
    token = str(((login_payload or {}).get("data") or {}).get("access_token") or "")
    headers = {"Authorization": f"Bearer {token}"}

    goal_id = args.goal_id
    if not goal_id:
        goals = (_fetch_json(s, f"{base}/goals", headers).get("data") or [])
        if not goals:
            raise RuntimeError("no goals found")
        goal_id = str(goals[0].get("id") or "")
    if not goal_id:
        raise RuntimeError("goal_id unresolved")

    detail = _fetch_json(s, f"{base}/goals/{goal_id}/detail", headers).get("data") or {}
    tasks = list(detail.get("tasks") or [])
    task_details: list[dict[str, Any]] = []
    for t in tasks:
        tid = str(t.get("id") or "").strip()
        if not tid:
            continue
        task_details.append((_fetch_json(s, f"{base}/tasks/{tid}", headers).get("data") or {}))

    phase_rows: list[PhaseDurations] = []
    task_histories: list[list[dict[str, Any]]] = []
    assign_ts: list[float] = []
    propose_calls = 0
    no_candidates_ticks = 0
    llm_calls_total = 0
    llm_calls_real = 0
    llm_calls_synthetic = 0
    llm_calls_estimated = 0
    llm_latency_ms_real: list[int] = []
    llm_prompt_tokens_real: list[int] = []
    llm_completion_tokens_real: list[int] = []
    llm_success = 0
    llm_fail = 0
    llm_success_real = 0
    llm_fail_real = 0
    llm_by_model: dict[str, int] = {}
    llm_by_model_real: dict[str, int] = {}
    llm_by_model_synthetic: dict[str, int] = {}
    llm_by_source: dict[str, int] = {}
    llm_profile_entries: list[dict[str, Any]] = []

    for td in task_details:
        history = list(td.get("history") or [])
        created = float(td.get("created_at") or 0.0) or None
        assigned = _first_event_ts(history, {"task_assigned", "task_claimed", "task_delegated", "autopilot_handoff"})
        propose_done = _last_event_ts(history, {"autopilot_decision", "proposal_result", "autopilot_strategy_attempt"})
        execute_done = _last_event_ts(history, {"autopilot_result", "execution_result", "task_completed_with_gates"})
        terminal = float(td.get("updated_at") or 0.0) if str(td.get("status") or "") in TERMINAL else None
        if assigned:
            assign_ts.append(assigned)

        # LLM call profile from persisted proposal metadata
        proposal = dict(td.get("last_proposal") or {})
        cli_result = dict(proposal.get("cli_result") or {})
        prof_entries = list(cli_result.get("llm_call_profile") or [])
        for entry in prof_entries:
            if isinstance(entry, dict):
                llm_profile_entries.append(dict(entry))

        for e in history:
            et = str(e.get("event_type") or "")
            if et == "autopilot_strategy_attempt":
                propose_calls += 1
            if et == "autopilot_no_candidates":
                no_candidates_ticks += 1

        phase_rows.append(
            PhaseDurations(
                queued_to_assigned=_safe_delta(created, assigned),
                assigned_to_propose_done=_safe_delta(assigned, propose_done),
                propose_to_execute_done=_safe_delta(propose_done, execute_done),
                execute_to_terminal=_safe_delta(execute_done, terminal),
                total=_safe_delta(created, terminal),
            )
        )
        task_histories.append(history)

    q2a = [x.queued_to_assigned for x in phase_rows if x.queued_to_assigned is not None]
    a2p = [x.assigned_to_propose_done for x in phase_rows if x.assigned_to_propose_done is not None]
    p2e = [x.propose_to_execute_done for x in phase_rows if x.propose_to_execute_done is not None]
    e2t = [x.execute_to_terminal for x in phase_rows if x.execute_to_terminal is not None]
    tot = [x.total for x in phase_rows if x.total is not None]

    throughput_per_min = None
    idle_gap_seconds = None
    if len(assign_ts) >= 2:
        span = max(assign_ts) - min(assign_ts)
        if span > 0:
            throughput_per_min = len(assign_ts) / (span / 60.0)
        assign_ts_sorted = sorted(assign_ts)
        gaps = [assign_ts_sorted[i] - assign_ts_sorted[i - 1] for i in range(1, len(assign_ts_sorted))]
        idle_gap_seconds = _median(gaps) if gaps else None

    llm_summary = _summarize_llm_call_profile(llm_profile_entries)
    reason_breakdown = _build_reason_breakdown(phase_rows, task_histories)

    out = {
        "goal_id": goal_id,
        "generated_at": time.time(),
        "task_count": len(task_details),
        "phase_breakdown_seconds": {
            "queued_to_assigned": {"mean": _mean(q2a), "median": _median(q2a), "n": len(q2a)},
            "assigned_to_propose_done": {"mean": _mean(a2p), "median": _median(a2p), "n": len(a2p)},
            "propose_to_execute_done": {"mean": _mean(p2e), "median": _median(p2e), "n": len(p2e)},
            "execute_to_terminal": {"mean": _mean(e2t), "median": _median(e2t), "n": len(e2t)},
            "total": {"mean": _mean(tot), "median": _median(tot), "n": len(tot)},
        },
        "llm_call_profile": {
            "calls_seen_total": llm_summary["calls_seen_total"],
            "calls_seen_real": llm_summary["calls_seen_real"],
            "calls_seen_synthetic": llm_summary["calls_seen_synthetic"],
            "calls_seen_estimated": llm_summary["calls_seen_estimated"],
            "propose_events_seen": propose_calls,
            "success_count": llm_summary["success_count"],
            "fail_count": llm_summary["fail_count"],
            "success_count_real": llm_summary["success_count_real"],
            "fail_count_real": llm_summary["fail_count_real"],
            "latency_ms_mean_real": llm_summary["latency_ms_mean_real"],
            "latency_ms_median_real": llm_summary["latency_ms_median_real"],
            "prompt_tokens_mean_real": llm_summary["prompt_tokens_mean_real"],
            "completion_tokens_mean_real": llm_summary["completion_tokens_mean_real"],
            "by_model": llm_summary["by_model"],
            "by_model_real": llm_summary["by_model_real"],
            "by_model_synthetic": llm_summary["by_model_synthetic"],
            "by_source": llm_summary["by_source"],
            "note": "synthetic entries prove orchestration activity but are excluded from real latency/token statistics",
        },
        "autopilot_throughput_idle": {
            "tasks_assigned_count": len(assign_ts),
            "tasks_assigned_per_min": throughput_per_min,
            "median_idle_gap_seconds": idle_gap_seconds,
            "no_candidates_ticks_seen": no_candidates_ticks,
        },
        "reason_breakdown_seconds": reason_breakdown,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
