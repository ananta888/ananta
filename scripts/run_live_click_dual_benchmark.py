#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request


def req(base: str, method: str, path: str, body: Any | None = None, token: str | None = None, timeout: int = 60) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req_obj = request.Request(base + path, data=data, headers=headers, method=method)
    with request.urlopen(req_obj, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def login_token(base: str, username: str, password: str) -> str:
    payload = req(base, "POST", "/login", {"username": username, "password": password}, timeout=45)
    token = str((payload.get("data") or {}).get("access_token") or "")
    if not token:
        raise RuntimeError("login_failed_no_access_token")
    return token


def set_config(base: str, token: str, config_patch: dict) -> dict:
    res = req(base, "POST", "/config", config_patch, token=token, timeout=60)
    if str(res.get("status") or "").lower() not in {"success", "ok"}:
        raise RuntimeError(f"config_post_failed: {res}")
    cfg = req(base, "GET", "/config", token=token, timeout=60)
    data = cfg.get("data") if isinstance(cfg.get("data"), dict) else cfg
    return data if isinstance(data, dict) else {}


def benchmark_step(report_path: Path) -> dict:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    steps = data.get("steps") or []
    for step in steps:
        if isinstance(step, dict) and step.get("phase") == "benchmark":
            return step
    raise RuntimeError(f"missing_benchmark_step_in_report: {report_path}")


def run_click_test(report_path: Path, goal_text: str, admin_user: str, admin_password: str, extra_args: list[str]) -> None:
    cmd = [
        sys.executable,
        "scripts/firefox_live_click_extended.py",
        "--phases",
        "all",
        "--step-delay-seconds",
        "1.2",
        "--goal-wait-seconds",
        "180",
        "--wait-tasks-seconds",
        "240",
        "--benchmark-ticks",
        "12",
        "--benchmark-task-kind",
        "coding",
        "--goal-text",
        goal_text,
        "--report-file",
        str(report_path),
        "--require-followup",
        "--require-artifact-summary",
        "--require-multi-file-output",
        "--min-distinct-files",
        "3",
        "--min-distinct-dirs",
        "2",
    ]
    cmd.extend(extra_args)
    env = dict(os.environ)
    env["E2E_ADMIN_USER"] = admin_user
    env["E2E_ADMIN_PASSWORD"] = admin_password
    subprocess.run(cmd, check=True, env=env)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full live click benchmark in two modes: single-model and mixed-model.")
    parser.add_argument("--hub-base-url", default=os.getenv("HUB_BASE_URL", "http://127.0.0.1:5000"))
    parser.add_argument("--admin-user", default=os.getenv("E2E_ADMIN_USER", os.getenv("INITIAL_ADMIN_USER", "admin")))
    parser.add_argument(
        "--admin-password",
        default=os.getenv("E2E_ADMIN_PASSWORD", os.getenv("INITIAL_ADMIN_PASSWORD", "AnantaAdminPassword123!")),
    )
    parser.add_argument(
        "--goal-text",
        default=(
            "Erstelle eine kleine Fullstack-Fibonacci-Demo: Backend-Endpoint zur Fibonacci-Berechnung, "
            "Frontend-Ansicht mit Eingabefeld und Ergebnisanzeige, plus kurzer Test/Validierung."
        ),
    )
    parser.add_argument(
        "--single-model",
        default="lfm2.5-1.2b-glm-4.7-flash-thinking-i1:latest",
    )
    parser.add_argument(
        "--mixed-planning-model",
        default="lfm2.5-1.2b-glm-4.7-flash-thinking-i1:latest",
    )
    parser.add_argument(
        "--mixed-coding-model",
        default="tensorblock-deepseek-coder-v2-lite-instruct-gguf-deepseek-coder-v2-443776354e4e:latest",
    )
    parser.add_argument(
        "--mixed-review-model",
        default="lmstudio-community-phi-4-mini-reasoning-gguf-phi-4-mini-reasoning-q4_k_m:latest",
    )
    parser.add_argument("--report-dir", default="test-reports/live-click")
    args = parser.parse_args()

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    single_report = report_dir / f"live-click-dual-single-{stamp}.json"
    mixed_report = report_dir / f"live-click-dual-mixed-{stamp}.json"

    token = login_token(args.hub_base_url.rstrip("/"), args.admin_user, args.admin_password)

    # Run 1: single model only
    cfg_single = set_config(
        args.hub_base_url.rstrip("/"),
        token,
        {
            "default_provider": "ollama",
            "default_model": args.single_model,
            "role_model_overrides": {},
            "template_model_overrides": {},
            "task_kind_model_overrides": {},
        },
    )
    print("single_config_default_model", cfg_single.get("default_model"), flush=True)
    run_click_test(single_report, args.goal_text, args.admin_user, args.admin_password, [])
    single_bench = benchmark_step(single_report)
    single_details = single_bench.get("details") or {}
    single_payload = single_details.get("benchmark_payload") or {}
    if not bool(single_payload.get("success")):
        raise RuntimeError(f"single_model_benchmark_failed: {single_report}")

    # Run 2: mixed model assignment by role + task-kind
    cfg_mixed = set_config(
        args.hub_base_url.rstrip("/"),
        token,
        {
            "default_provider": "ollama",
            "default_model": args.mixed_planning_model,
            "role_model_overrides": {
                "implementer": args.mixed_coding_model,
                "reviewer": args.mixed_review_model,
            },
            "task_kind_model_overrides": {
                "planning": args.mixed_planning_model,
                "coding": args.mixed_coding_model,
                "review": args.mixed_review_model,
            },
        },
    )
    print("mixed_config_default_model", cfg_mixed.get("default_model"), flush=True)
    print("mixed_config_role_model_overrides", cfg_mixed.get("role_model_overrides"), flush=True)
    print("mixed_config_task_kind_model_overrides", cfg_mixed.get("task_kind_model_overrides"), flush=True)
    run_click_test(mixed_report, args.goal_text, args.admin_user, args.admin_password, [])
    mixed_bench = benchmark_step(mixed_report)
    mixed_details = mixed_bench.get("details") or {}
    mixed_payload = mixed_details.get("benchmark_payload") or {}
    if not bool(mixed_payload.get("success")):
        raise RuntimeError(f"mixed_model_benchmark_failed: {mixed_report}")

    print("dual_benchmark_reports", json.dumps({"single": str(single_report), "mixed": str(mixed_report)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
