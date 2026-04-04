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


def _docker_exec_hub_python(container_name: str, script_body: str) -> str:
    cmd = [
        "docker",
        "exec",
        container_name,
        "/bin/sh",
        "-lc",
        f"python - <<'PY'\n{script_body}\nPY",
    ]
    res = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return (res.stdout or "").strip()


def get_admin_from_hub_container(container_name: str) -> tuple[str, str]:
    out = _docker_exec_hub_python(
        container_name,
        "import os;print(os.getenv('INITIAL_ADMIN_USER','admin'));print(os.getenv('INITIAL_ADMIN_PASSWORD',''))",
    )
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    user = lines[0] if lines else "admin"
    password = lines[1] if len(lines) > 1 else ""
    return user, password


def set_config_via_hub_container(container_name: str, config_patch: dict) -> dict:
    payload = json.dumps(config_patch, ensure_ascii=True)
    script = f"""
import json, urllib.request, os
base = 'http://127.0.0.1:5000'
user = os.getenv('INITIAL_ADMIN_USER','admin')
pwd = os.getenv('INITIAL_ADMIN_PASSWORD','')
payload = json.loads({json.dumps(payload)})

def req(method, path, body=None, token=None):
    headers = {{'Content-Type': 'application/json'}}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = json.dumps(body).encode('utf-8') if body is not None else None
    r = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=60) as s:
        return json.loads(s.read().decode('utf-8'))

token = ((req('POST','/login',{{'username': user, 'password': pwd}}).get('data') or {{}}).get('access_token') or '')
req('POST','/config',payload,token)
cfg = req('GET','/config',token=token)
print(json.dumps(cfg.get('data') if isinstance(cfg.get('data'), dict) else cfg, ensure_ascii=True))
"""
    out = _docker_exec_hub_python(container_name, script)
    try:
        return json.loads(out)
    except Exception:
        return {}


def benchmark_step(report_path: Path) -> dict:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    steps = data.get("steps") or []
    for step in steps:
        if isinstance(step, dict) and step.get("phase") == "benchmark":
            return step
    raise RuntimeError(f"missing_benchmark_step_in_report: {report_path}")


def collect_benchmark_result(report_path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"report": str(report_path), "ok": False}
    if not report_path.exists():
        out["error"] = "report_not_found"
        return out
    try:
        step = benchmark_step(report_path)
    except Exception as exc:
        out["error"] = str(exc)
        return out
    details = step.get("details") or {}
    payload = details.get("benchmark_payload") or {}
    out["ok"] = bool(payload.get("success"))
    out["model"] = payload.get("model")
    out["details"] = {
        "followup_observed": details.get("followup_observed"),
        "followup_created": details.get("followup_created"),
        "artifacts_summary_present": details.get("artifacts_summary_present"),
        "multi_file_output_ok": details.get("multi_file_output_ok"),
        "distinct_file_count": ((details.get("file_output_evidence") or {}).get("distinct_file_count")),
        "distinct_dir_count": ((details.get("file_output_evidence") or {}).get("distinct_dir_count")),
        "task_status_after": details.get("task_status_after"),
    }
    return out


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
    parser.add_argument("--hub-container", default=os.getenv("ANANTA_HUB_CONTAINER", "ananta-ai-agent-hub-1"))
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

    use_container_config = False
    token = ""
    admin_user = args.admin_user
    admin_password = args.admin_password
    try:
        token = login_token(args.hub_base_url.rstrip("/"), admin_user, admin_password)
    except Exception:
        use_container_config = True
        container_user, container_password = get_admin_from_hub_container(args.hub_container)
        if not admin_user or admin_user == "admin":
            admin_user = container_user
        if not admin_password or admin_password == "AnantaAdminPassword123!":
            admin_password = container_password

    # Run 1: single model only
    single_cfg_payload = {
            "default_provider": "ollama",
            "default_model": args.single_model,
            "role_model_overrides": {},
            "template_model_overrides": {},
            "task_kind_model_overrides": {},
        }
    cfg_single = (
        set_config_via_hub_container(args.hub_container, single_cfg_payload)
        if use_container_config
        else set_config(args.hub_base_url.rstrip("/"), token, single_cfg_payload)
    )
    print("single_config_default_model", cfg_single.get("default_model"), flush=True)
    results: dict[str, Any] = {"single": {}, "mixed": {}}
    try:
        run_click_test(single_report, args.goal_text, admin_user, admin_password, [])
        results["single"] = collect_benchmark_result(single_report)
    except Exception as exc:
        results["single"] = collect_benchmark_result(single_report)
        results["single"]["error"] = str(exc)

    # Run 2: mixed model assignment by role + task-kind
    mixed_cfg_payload = {
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
        }
    cfg_mixed = (
        set_config_via_hub_container(args.hub_container, mixed_cfg_payload)
        if use_container_config
        else set_config(args.hub_base_url.rstrip("/"), token, mixed_cfg_payload)
    )
    print("mixed_config_default_model", cfg_mixed.get("default_model"), flush=True)
    print("mixed_config_role_model_overrides", cfg_mixed.get("role_model_overrides"), flush=True)
    print("mixed_config_task_kind_model_overrides", cfg_mixed.get("task_kind_model_overrides"), flush=True)
    try:
        run_click_test(mixed_report, args.goal_text, admin_user, admin_password, [])
        results["mixed"] = collect_benchmark_result(mixed_report)
    except Exception as exc:
        results["mixed"] = collect_benchmark_result(mixed_report)
        results["mixed"]["error"] = str(exc)

    print("dual_benchmark_results", json.dumps(results, ensure_ascii=True), flush=True)
    return 0 if bool(results.get("single", {}).get("ok")) and bool(results.get("mixed", {}).get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
