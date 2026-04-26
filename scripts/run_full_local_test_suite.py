#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOGFOOD_OUT = "artifacts/e2e/dogfood_gate_report.json"
DEFAULT_REPORT_OUT = "artifacts/test-gates/full_local_test_suite_report.json"


def _python_executable() -> str:
    venv_python = ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _run_command(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=str(ROOT), check=False, capture_output=True, text=True, env=env)


def _record_step(
    *,
    name: str,
    command: list[str],
    env: dict[str, str],
    checks: list[dict[str, Any]],
    category: str = "main",
) -> bool:
    result = _run_command(command, env=env)
    ok = result.returncode == 0
    output_tail = (result.stdout + "\n" + result.stderr)[-3000:]
    checks.append(
        {
            "name": name,
            "category": category,
            "ok": ok,
            "returncode": result.returncode,
            "command": " ".join(command),
            "output_tail": output_tail,
        }
    )
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}: {' '.join(command)}")
    if not ok and output_tail.strip():
        print(output_tail.strip())
    return ok


def run_full_test_suite(
    *,
    skip_deep_checks: bool = False,
    skip_compose_tests: bool = False,
    skip_dogfood: bool = False,
    skip_live_click: bool = False,
    live_click_mode: str = "single",
    reuse_running_stack: bool = False,
    keep_stack_running: bool = False,
    use_wsl_vulkan: bool = True,
    dogfood_out: str = DEFAULT_DOGFOOD_OUT,
) -> dict[str, Any]:
    python_exec = _python_executable()
    env = dict(os.environ)
    env["ANANTA_USE_WSL_VULKAN"] = "1" if use_wsl_vulkan else "0"

    checks: list[dict[str, Any]] = []
    should_continue = True
    overall_ok = True
    stack_started = False
    firefox_started = False

    def run_required_step(name: str, command: list[str], *, category: str = "main") -> bool:
        nonlocal should_continue, overall_ok
        if not should_continue:
            return False
        ok = _record_step(name=name, command=command, env=env, checks=checks, category=category)
        overall_ok = overall_ok and ok
        if not ok:
            should_continue = False
        return ok

    try:
        if not skip_deep_checks:
            run_required_step("check-pipeline-deep", [python_exec, "scripts/check_pipeline.py", "--mode", "deep"])

        if should_continue and not reuse_running_stack:
            if run_required_step("compose-up", ["bash", "scripts/compose-test-stack.sh", "up"]):
                stack_started = True

        if should_continue and not skip_compose_tests:
            compose_test_commands = (
                ("backend-test", ["bash", "scripts/compose-test-stack.sh", "run-backend-test"]),
                ("backend-live-llm-test", ["bash", "scripts/compose-test-stack.sh", "run-backend-live-llm-test"]),
                ("frontend-test", ["bash", "scripts/compose-test-stack.sh", "run-frontend-test"]),
                ("frontend-live-llm-test", ["bash", "scripts/compose-test-stack.sh", "run-frontend-live-llm-test"]),
            )
            for step_name, command in compose_test_commands:
                run_required_step(step_name, command)
                if not should_continue:
                    break

        if should_continue and not skip_dogfood:
            run_required_step(
                "e2e-dogfood-checks",
                [python_exec, "scripts/run_e2e_dogfood_checks.py", "--out", dogfood_out],
            )

        if should_continue and not skip_live_click:
            if run_required_step("firefox-vnc-start", ["bash", "scripts/start-firefox-vnc.sh", "start"]):
                firefox_started = True
            if should_continue:
                if live_click_mode == "dual":
                    run_required_step(
                        "live-click-dual-benchmark", [python_exec, "scripts/run_live_click_dual_benchmark.py"]
                    )
                else:
                    run_required_step(
                        "live-click-extended",
                        [
                            python_exec,
                            "scripts/firefox_live_click_extended.py",
                            "--phases",
                            "all",
                            "--require-followup",
                            "--require-artifact-summary",
                            "--require-multi-file-output",
                            "--min-distinct-files",
                            "3",
                            "--min-distinct-dirs",
                            "2",
                        ],
                    )
    finally:
        if firefox_started:
            cleanup_ok = _record_step(
                name="firefox-vnc-stop",
                command=["bash", "scripts/start-firefox-vnc.sh", "stop"],
                env=env,
                checks=checks,
                category="cleanup",
            )
            overall_ok = overall_ok and cleanup_ok

        should_stop_stack = stack_started and not keep_stack_running
        if should_stop_stack:
            cleanup_ok = _record_step(
                name="compose-down",
                command=["bash", "scripts/compose-test-stack.sh", "down"],
                env=env,
                checks=checks,
                category="cleanup",
            )
            overall_ok = overall_ok and cleanup_ok

    return {
        "schema": "full_local_test_suite_v1",
        "ok": overall_ok,
        "wsl_vulkan_enabled": use_wsl_vulkan,
        "live_click_mode": live_click_mode,
        "reuse_running_stack": reuse_running_stack,
        "keep_stack_running": keep_stack_running,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the full local test gate in one command: deep checks, compose backend/frontend tests, "
            "dogfood gate, and live-click tests."
        )
    )
    parser.add_argument("--skip-deep-checks", action="store_true")
    parser.add_argument("--skip-compose-tests", action="store_true")
    parser.add_argument("--skip-dogfood", action="store_true")
    parser.add_argument("--skip-live-click", action="store_true")
    parser.add_argument("--live-click-mode", choices=("single", "dual"), default="single")
    parser.add_argument("--reuse-running-stack", action="store_true")
    parser.add_argument("--keep-stack-running", action="store_true")
    parser.add_argument("--cpu", action="store_true", help="Disable WSL2/Vulkan overlay for Ollama.")
    parser.add_argument("--dogfood-out", default=DEFAULT_DOGFOOD_OUT)
    parser.add_argument("--out", default=DEFAULT_REPORT_OUT)
    args = parser.parse_args()

    report = run_full_test_suite(
        skip_deep_checks=args.skip_deep_checks,
        skip_compose_tests=args.skip_compose_tests,
        skip_dogfood=args.skip_dogfood,
        skip_live_click=args.skip_live_click,
        live_click_mode=args.live_click_mode,
        reuse_running_stack=args.reuse_running_stack,
        keep_stack_running=args.keep_stack_running,
        use_wsl_vulkan=not args.cpu,
        dogfood_out=args.dogfood_out,
    )

    print(f"ok={report['ok']}")
    print(f"wsl_vulkan_enabled={report['wsl_vulkan_enabled']}")
    print(f"live_click_mode={report['live_click_mode']}")

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    out_label = out_path.relative_to(ROOT) if ROOT in out_path.parents else out_path
    print(f"full_test_suite_report={out_label}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
