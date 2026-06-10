#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from test_env_cleanup import cleanup_ollama_runtime, cleanup_test_environment

from firefox_automation._benchmark import phase_benchmark
from firefox_automation._browser_utils import step_nav
from firefox_automation._config import (
    APP_BASE,
    BASE,
    DEFAULT_PHASES,
    DEFAULT_REPORT_DIR,
    HUB_BASE,
    HUB_CONTAINER,
    LOGIN_PASS,
    LOGIN_USER,
)
from firefox_automation._phases import phase_execution, phase_goal, phase_review, phase_setup
from firefox_automation._reporting import default_goal_text_for_benchmark, parse_phases
from firefox_automation._webdriver import wd


def run_phase(
    session_id: str,
    phase: str,
    report: dict,
    hard_fail: bool,
    step_delay_seconds: float,
    wait_tasks_seconds: float,
    goal_wait_seconds: float,
    bootstrap_setup: bool,
    goal_text: str,
    benchmark_ticks: int,
    benchmark_task_kind: str,
    require_followup: bool,
    require_artifact_summary: bool,
    require_multi_file_output: bool,
    min_distinct_files: int,
    min_distinct_dirs: int,
):
    if phase == "setup":
        phase_setup(session_id, report, hard_fail, step_delay_seconds, bootstrap_setup)
    elif phase == "goal":
        phase_goal(session_id, report, hard_fail, step_delay_seconds, goal_wait_seconds, goal_text)
    elif phase == "execution":
        phase_execution(session_id, report, hard_fail, step_delay_seconds, wait_tasks_seconds)
    elif phase == "benchmark":
        phase_benchmark(
            session_id,
            report,
            hard_fail,
            step_delay_seconds,
            benchmark_ticks=benchmark_ticks,
            benchmark_task_kind=benchmark_task_kind,
            require_followup=require_followup,
            require_artifact_summary=require_artifact_summary,
            require_multi_file_output=require_multi_file_output,
            min_distinct_files=min_distinct_files,
            min_distinct_dirs=min_distinct_dirs,
        )
    elif phase == "review":
        phase_review(session_id, report, hard_fail, step_delay_seconds)
    else:
        raise RuntimeError(f"Unsupported phase: {phase}")


def write_report(report: dict, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    print("report_written", str(path), flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Live Firefox click runner (modular phases + JSON report).")
    p.add_argument(
        "--phases",
        default="all",
        help="Comma-separated list: setup,goal,execution,benchmark,review or all",
    )
    p.add_argument(
        "--report-file",
        default="",
        help="Optional explicit JSON report path (default: test-reports/live-click/<timestamp>.json)",
    )
    p.add_argument(
        "--skip-created-resource-cleanup",
        action="store_true",
        help="Keep test-created templates/blueprints/teams/tasks/goals instead of cleaning them up.",
    )
    p.add_argument(
        "--allow-visible-errors",
        action="store_true",
        help="Do not fail hard when visible UI errors/toasts are detected.",
    )
    p.add_argument(
        "--step-delay-seconds",
        type=float,
        default=0.0,
        help="Optional delay added after each major step (slow-mode for live observation).",
    )
    p.add_argument(
        "--wait-tasks-seconds",
        type=float,
        default=90.0,
        help="How long execution phase waits on /board for visible task cards.",
    )
    p.add_argument(
        "--goal-wait-seconds",
        type=float,
        default=90.0,
        help="How long goal phase waits for a visible goal result panel.",
    )
    p.add_argument(
        "--skip-setup-bootstrap",
        action="store_true",
        help="Run only login in setup phase (no template/blueprint/team bootstrap).",
    )
    p.add_argument(
        "--replay-from-report",
        default="",
        help="Reuse phases/settings from an existing JSON report file.",
    )
    p.add_argument(
        "--goal-text",
        default="",
        help="Optional explicit goal text for the goal phase.",
    )
    p.add_argument(
        "--benchmark-ticks",
        type=int,
        default=6,
        help="How many manual autopilot ticks are executed in benchmark phase.",
    )
    p.add_argument(
        "--benchmark-task-kind",
        default="coding",
        help="Task-kind bucket for /llm/benchmarks record/list (analysis|coding|planning|review).",
    )
    p.add_argument(
        "--require-followup",
        action="store_true",
        help="Fail benchmark phase if no explicit follow-up/new task chain is visible.",
    )
    p.add_argument(
        "--require-artifact-summary",
        action="store_true",
        help="Fail benchmark phase if goal detail has no artifact summary/headline evidence.",
    )
    p.add_argument(
        "--require-multi-file-output",
        action="store_true",
        help="Fail benchmark phase if outputs do not show multiple file paths across directories.",
    )
    p.add_argument(
        "--min-distinct-files",
        type=int,
        default=3,
        help="Minimum distinct file paths required when --require-multi-file-output is set.",
    )
    p.add_argument(
        "--min-distinct-dirs",
        type=int,
        default=2,
        help="Minimum distinct directories required when --require-multi-file-output is set.",
    )
    return p


def main():
    args = build_parser().parse_args()
    replay_source = ""
    replay_report = None
    if args.replay_from_report:
        replay_source = args.replay_from_report
        replay_report = json.loads(Path(args.replay_from_report).read_text(encoding="utf-8"))

    phase_source = args.phases
    if replay_report and phase_source == "all":
        phase_source = ",".join(replay_report.get("phases_requested") or DEFAULT_PHASES)
    phases = parse_phases(phase_source)

    hard_fail = not args.allow_visible_errors
    if replay_report and not args.allow_visible_errors:
        # Preserve previous run semantics unless explicitly overridden.
        hard_fail = bool(replay_report.get("hard_fail_visible_errors", True))

    step_delay_seconds = max(0.0, float(args.step_delay_seconds))
    wait_tasks_seconds = max(5.0, float(args.wait_tasks_seconds))
    goal_wait_seconds = max(20.0, float(args.goal_wait_seconds))
    benchmark_ticks = max(0, int(args.benchmark_ticks))
    benchmark_task_kind = str(args.benchmark_task_kind or "coding").strip().lower() or "coding"
    if benchmark_task_kind not in {"analysis", "coding", "planning", "review"}:
        benchmark_task_kind = "coding"
    goal_text = str(args.goal_text or "").strip() or default_goal_text_for_benchmark(benchmark_task_kind)
    require_followup = bool(args.require_followup)
    require_artifact_summary = bool(args.require_artifact_summary)
    require_multi_file_output = bool(args.require_multi_file_output)
    min_distinct_files = max(1, int(args.min_distinct_files))
    min_distinct_dirs = max(1, int(args.min_distinct_dirs))
    bootstrap_setup = not args.skip_setup_bootstrap
    if replay_report and step_delay_seconds == 0.0:
        step_delay_seconds = float(replay_report.get("step_delay_seconds") or 0.0)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = Path(args.report_file) if args.report_file else (DEFAULT_REPORT_DIR / f"firefox-live-click-{ts}.json")

    report = {
        "run_id": ts,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "base": {"selenium": BASE, "frontend": APP_BASE},
        "phases_requested": phases,
        "hard_fail_visible_errors": hard_fail,
        "step_delay_seconds": step_delay_seconds,
        "wait_tasks_seconds": wait_tasks_seconds,
        "goal_wait_seconds": goal_wait_seconds,
        "goal_text": goal_text,
        "benchmark_ticks": benchmark_ticks,
        "benchmark_task_kind": benchmark_task_kind,
        "require_followup": require_followup,
        "require_artifact_summary": require_artifact_summary,
        "require_multi_file_output": require_multi_file_output,
        "min_distinct_files": min_distinct_files,
        "min_distinct_dirs": min_distinct_dirs,
        "bootstrap_setup": bootstrap_setup,
        "cleanup_created_resources": not args.skip_created_resource_cleanup,
        "replay_from_report": replay_source,
        "status": "running",
        "steps": [],
        "cleanup_targets": {
            "template_names": [],
            "blueprint_names": [],
            "team_names": [],
            "goal_ids": [],
            "team_ids": [],
        },
        "ui_signals": {
            "visible_errors": [],
            "visible_errors_contains_401": False,
            "final_route": "",
            "final_title": "",
        },
        "errors": [],
    }

    caps = {"capabilities": {"alwaysMatch": {"browserName": "firefox", "acceptInsecureCerts": True}}}
    created = wd("POST", "/session", caps)
    session_id = created.get("sessionId") or (created.get("value") or {}).get("sessionId")
    if not session_id:
        raise RuntimeError(f"No session id returned: {created}")
    try:
        wd(
            "POST",
            f"/session/{session_id}/timeouts",
            {"script": 180000, "pageLoad": 300000, "implicit": 0},
        )
    except Exception:
        # Continue even if timeout update fails; browser_api_json handles async call failures.
        pass

    report["session_id"] = session_id
    print("session", session_id, flush=True)
    try:
        for phase in phases:
            print("phase_start", phase, flush=True)
            run_phase(
                session_id,
                phase,
                report,
                hard_fail,
                step_delay_seconds,
                wait_tasks_seconds,
                goal_wait_seconds,
                bootstrap_setup,
                goal_text,
                benchmark_ticks,
                benchmark_task_kind,
                require_followup,
                require_artifact_summary,
                require_multi_file_output,
                min_distinct_files,
                min_distinct_dirs,
            )
            print("phase_done", phase, flush=True)
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["errors"].append({"message": str(exc)})
        print("run_failed", str(exc), flush=True)
        raise
    finally:
        report["ended_at"] = datetime.now(timezone.utc).isoformat()
        try:
            wd("DELETE", f"/session/{session_id}")
        except Exception as e:
            report["errors"].append({"message": f"quit_error: {e}"})
            print("quit_error", str(e), flush=True)
        if not args.skip_created_resource_cleanup:
            try:
                explicit_targets = report.get("cleanup_targets") if isinstance(report.get("cleanup_targets"), dict) else {}
                cleanup = cleanup_test_environment(
                    hub_base_url=HUB_BASE,
                    hub_container=HUB_CONTAINER,
                    admin_user=LOGIN_USER,
                    admin_password=LOGIN_PASS,
                    cleanup_live_prefixes=True,
                    explicit_targets=explicit_targets,
                )
                report["cleanup"] = cleanup
            except Exception as e:
                report["errors"].append({"message": f"cleanup_error: {e}"})
                report["cleanup"] = {"error": str(e)}
                print("cleanup_error", str(e), flush=True)
        try:
            report["ollama_cleanup"] = cleanup_ollama_runtime()
        except Exception as e:
            report["errors"].append({"message": f"ollama_cleanup_error: {e}"})
            report["ollama_cleanup"] = {"error": str(e)}
            print("ollama_cleanup_error", str(e), flush=True)
        write_report(report, report_path)


if __name__ == "__main__":
    main()
