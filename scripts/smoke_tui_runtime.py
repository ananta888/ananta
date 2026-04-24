from __future__ import annotations

import subprocess
import sys


def run_smoke_once() -> tuple[bool, str]:
    baseline_command = [
        sys.executable,
        "-m",
        "client_surfaces.tui_runtime.ananta_tui",
        "--fixture",
    ]
    baseline = subprocess.run(baseline_command, check=False, capture_output=True, text=True)

    drilldown_command = [
        sys.executable,
        "-m",
        "client_surfaces.tui_runtime.ananta_tui",
        "--fixture",
        "--selected-goal-id",
        "G-1",
        "--selected-task-id",
        "T-1",
        "--selected-artifact-id",
        "A-1",
        "--live-refresh-target",
        "system_task_logs",
        "--live-refresh-cycles",
        "2",
        "--live-refresh-interval-seconds",
        "0.2",
    ]
    drilldown = subprocess.run(drilldown_command, check=False, capture_output=True, text=True)
    output = (
        f"[baseline]\n{baseline.stdout}\n{baseline.stderr}\n\n"
        f"[drilldown]\n{drilldown.stdout}\n{drilldown.stderr}"
    ).strip()
    required_markers = (
        "[NAVIGATION]",
        "[DASHBOARD]",
        "[GOALS]",
        "[GOAL-DETAIL]",
        "[TASK-WORKBENCH]",
        "[TASK-ORCHESTRATION]",
        "[ARCHIVED-TASKS]",
        "[ARTIFACT-EXPLORER]",
        "[KNOWLEDGE]",
        "[TEMPLATES]",
        "[CONFIG]",
        "[SYSTEM]",
        "[AUTOMATION]",
        "[AUDIT]",
        "[APPROVALS]",
        "[REPAIRS]",
        "[HELP]",
    )
    drilldown_markers = (
        "[GOAL-DETAIL]",
        "[TASK-DETAIL]",
        "selected_artifact=A-1",
        "[LIVE-REFRESH]",
        "cycle=1/2 health=healthy",
        "task_logs_state=healthy task_id=T-1",
    )
    markers_ok = all(marker in output for marker in required_markers) and all(
        marker in output for marker in drilldown_markers
    )
    ok = baseline.returncode == 0 and drilldown.returncode == 0 and markers_ok
    return ok, output


def main() -> int:
    ok, output = run_smoke_once()
    if ok:
        print("tui-runtime-smoke-ok")
        return 0
    print("tui-runtime-smoke-failed")
    print(output)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
