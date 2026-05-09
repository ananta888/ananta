#!/usr/bin/env python3
"""
CLI for goals, diagnostics and artifacts in Ananta.

Usage:
    ananta ask "Implement user authentication"
    ananta first-run
    ananta goal --goal "Add API endpoint" --context "Using Flask" --team dev
    ananta goal --goals
    ananta status
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import requests

from agent.config import settings
from agent.tui_contract import sanitize_terminal_text

SHORTCUT_GOALS = {
    "ask": {
        "mode": None,
        "prefix": "Beantworte diese Frage und nenne bei Unsicherheit die naechsten pruefbaren Schritte:",
        "context": "Kurzkommando: Frage. Fokus auf klare Antwort, Annahmen und naechste pruefbare Schritte.",
    },
    "plan": {
        "mode": None,
        "prefix": "Plane konkrete naechste Schritte fuer:",
        "context": "Kurzkommando: Planen. Fokus auf Ziel, Aufgaben, Reihenfolge und Pruefung.",
    },
    "analyze": {
        "mode": "repo_analysis",
        "prefix": "Analysiere und fasse die wichtigsten Befunde zusammen:",
        "context": "Kurzkommando: Analyse. Fokus auf Verstaendnis, Risiken und naechste Schritte.",
    },
    "review": {
        "mode": "code_review",
        "prefix": "Fuehre ein Review durch und priorisiere konkrete Risiken:",
        "context": "Kurzkommando: Review. Fokus auf Bugs, Regressionen, Tests und klare Findings.",
    },
    "diagnose": {
        "mode": "docker_compose_repair",
        "prefix": "Diagnostiziere das Problem und schlage eine robuste Start- oder Reparatursequenz vor:",
        "context": "Kurzkommando: Diagnose. Fokus auf Logs, Compose, Ports, Health-Checks und naechste Pruefung.",
    },
    "patch": {
        "mode": "code_fix",
        "prefix": "Plane einen kleinen, testbaren Patch fuer:",
        "context": "Kurzkommando: Patch. Fokus auf kleine Aenderung, Regressionstest und minimale Nebenwirkungen.",
    },
    "new-project": {
        "mode": "new_software_project",
        "prefix": "Lege ein neues Softwareprojekt kontrolliert an aus dieser Idee:",
        "context": "Kurzkommando: Neues Projekt. Fokus auf Scope, Architekturvorschlag, initiales Backlog, Tests und sichere Defaults.",
    },
    "evolve-project": {
        "mode": "project_evolution",
        "prefix": "Plane eine kontrollierte Weiterentwicklung fuer ein bestehendes Projekt:",
        "context": "Kurzkommando: Projekt weiterentwickeln. Fokus auf betroffene Bereiche, Risiken, Tests und kleine reviewbare Schritte.",
    },
    "repair-admin": {
        "mode": "admin_repair",
        "prefix": "Plane eine bounded Admin-Reparatur als Shared Foundation fuer:",
        "context": "Kurzkommando: Admin Repair. Fokus auf bounded evidence, dry-run-first, advisory Klassifikation und verifizierbare Repair-Schritte.",
    },
}

# repair-script: like repair-admin but synchronous + outputs clean script to stdout
_REPAIR_SCRIPT_CFG = {
    "mode": "admin_repair",
    "prefix": "Analysiere das Problem und gib praezise Shell-Befehle als ausfuehrbares Bash-Script aus:",
    "context": (
        "Kurzkommando: Repair Script. "
        "Ausgabe: nur Shell-Befehle als ausfuehrbares Bash-Script. "
        "Jeden Befehl einzeln, Kommentare mit #, dry-run-first, minimale Nebenwirkungen. "
        "Kein Prosatext ausserhalb von Bash-Code-Bloecken. "
        "WICHTIG: Kein 'sudo', kein 'su', keine Privilege-Escalation — "
        "Ausfuehrung erfolgt als normaler Nutzer in einem Docker-Container ohne Root-Rechte. "
        "PLANUNG: Alle Teilaufgaben sind unabhaengig — setze 'depends_on': [] bei ALLEN Tasks. "
        "Jede Teilaufgabe schreibt ihr Ergebnis als Datei in das Workspace-Verzeichnis (tool_call write_file), "
        "anstatt Systembefehle direkt auszufuehren die moeglicherweise nicht im Container verfuegbar sind."
    ),
}

_TERMINAL_GOAL_STATUSES = {"completed", "failed", "cancelled", "archived", "aborted"}


def _poll_goal_status(goal_id: str, *, timeout: int = 300, interval: int = 5) -> str:
    deadline = time.monotonic() + timeout
    dots = 0
    detail_tick = 0
    while time.monotonic() < deadline:
        res = _request("GET", f"/goals/{goal_id}", timeout=10)
        if res.status_code == 200:
            data = _api_data(res)
            status = str(data.get("status") or "").lower()
            if status in _TERMINAL_GOAL_STATUSES:
                print(file=sys.stderr)
                return status
            dots = (dots + 1) % 4
            print(f"\r  [{status}]{'.' * dots}   ", end="", file=sys.stderr, flush=True)

            # Goal may stay "planned" even when all tasks are done (lifecycle bug workaround).
            # Every 4 polls, check via detail if all tasks are in terminal states.
            detail_tick += 1
            if detail_tick % 4 == 0:
                detail_res = _request("GET", f"/goals/{goal_id}/detail", timeout=15)
                if detail_res.status_code == 200:
                    detail_data = _api_data(detail_res)
                    # result_summary lives under data.artifacts.result_summary
                    summary = (
                        (detail_data.get("artifacts") or {}).get("result_summary")
                        or detail_data.get("result_summary")
                        or {}
                    )
                    total = int(summary.get("task_count") or 0)
                    done = int(summary.get("completed_tasks") or 0) + int(summary.get("failed_tasks") or 0)
                    # Tasks stuck in `proposing` with no LLM output (latency_ms=None) are
                    # stalled (worker crash) — count them as failures to avoid blocking forever.
                    cost_items = summary.get("cost_summary", {}).get("items") or []
                    stalled = sum(
                        1 for it in cost_items
                        if isinstance(it, dict)
                        and str(it.get("status") or "").lower() == "proposing"
                        and it.get("latency_ms") is None
                    )
                    if total > 0 and (done + stalled) >= total:
                        print(file=sys.stderr)
                        failed = int(summary.get("failed_tasks") or 0) + stalled
                        return "completed" if failed == 0 else "partially_failed"
        time.sleep(interval)
    print(file=sys.stderr)
    return "timeout"


def _fetch_task_full_output(task_id: str) -> str:
    res = _request("GET", f"/tasks/{task_id}", timeout=15)
    if res.status_code == 200:
        return str(_api_data(res).get("last_output") or "").strip()
    return ""


def _fetch_goal_outputs(goal_id: str) -> list[tuple[str, str]]:
    res = _request("GET", f"/goals/{goal_id}/detail", timeout=20)
    if res.status_code != 200:
        return []
    data = _api_data(res)
    results = []
    # "artifacts" in detail is the build_artifact_summary() dict; the actual list is nested.
    artifact_summary = data.get("artifacts") or {}
    if isinstance(artifact_summary, dict):
        artifact_list = artifact_summary.get("artifacts") or []
    else:
        artifact_list = artifact_summary  # fallback if API changes
    for artifact in artifact_list:
        if not isinstance(artifact, dict):
            continue
        task_id = artifact.get("task_id")
        if task_id:
            output = _fetch_task_full_output(task_id)
            if output:
                results.append((str(artifact.get("title") or "task"), output))
    return results


def _extract_script_blocks(text: str) -> str:
    blocks = re.findall(
        r"```(?:bash|sh|shell|zsh|cmd|console|powershell)?\s*\n(.*?)```",
        text,
        re.DOTALL,
    )
    if blocks:
        return "\n\n".join(b.strip() for b in blocks)
    lines = [l for l in text.splitlines() if l.strip() and not l.strip().startswith("#!")]
    return "\n".join(lines)


# ── Host-Diagnose (läuft lokal, vor LLM-Submission) ─────────────────────────

_SCAN_CMD_TIMEOUT = 5  # Sekunden pro Befehl

_SCAN_BASE: list[tuple[str, str]] = [
    ("Uptime / Load",          "uptime"),
    ("Disk",                   "df -h"),
    ("Memory",                 "free -h"),
    ("Failed systemd units",   "systemctl --failed --no-pager 2>/dev/null"),
    ("Open TCP listeners",     "ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null"),
    ("Recent kernel/svc errors","journalctl -p err -n 20 --no-pager 2>/dev/null"),
]

_SCAN_SERVICE_MAP: list[tuple[str, list[tuple[str, str]]]] = [
    (r"\bnginx\b", [
        ("nginx: status",      "systemctl status nginx --no-pager -l 2>/dev/null"),
        ("nginx: config test", "nginx -t 2>&1"),
        ("nginx: config",      "cat /etc/nginx/nginx.conf 2>/dev/null"),
        ("nginx: sites",       "ls -la /etc/nginx/sites-enabled/ 2>/dev/null || ls -la /etc/nginx/conf.d/ 2>/dev/null"),
        ("nginx: error log",   "journalctl -u nginx -n 40 --no-pager 2>/dev/null"),
    ]),
    (r"\bapache2?\b|\bhttpd\b", [
        ("apache: status",     "systemctl status apache2 --no-pager -l 2>/dev/null || systemctl status httpd --no-pager -l 2>/dev/null"),
        ("apache: configtest", "apache2ctl configtest 2>&1 || apachectl configtest 2>&1"),
        ("apache: error log",  "journalctl -u apache2 -n 40 --no-pager 2>/dev/null"),
    ]),
    (r"\bdocker\b", [
        ("docker: containers", "docker ps -a 2>/dev/null"),
        ("docker: compose",    "docker compose ps 2>/dev/null || docker-compose ps 2>/dev/null"),
        ("docker: recent logs","docker compose logs --tail=25 2>/dev/null | head -60"),
    ]),
    (r"\bpostgres(?:ql)?\b", [
        ("postgres: status",   "systemctl status postgresql --no-pager 2>/dev/null"),
        ("postgres: ready",    "pg_isready 2>/dev/null"),
        ("postgres: log",      "journalctl -u postgresql -n 40 --no-pager 2>/dev/null"),
    ]),
    (r"\bmysql\b|\bmariadb\b", [
        ("mysql: status",      "systemctl status mysql --no-pager 2>/dev/null || systemctl status mariadb --no-pager 2>/dev/null"),
        ("mysql: log",         "journalctl -u mysql -n 40 --no-pager 2>/dev/null"),
    ]),
    (r"\bsshd?\b", [
        ("ssh: status",        "systemctl status ssh --no-pager 2>/dev/null || systemctl status sshd --no-pager 2>/dev/null"),
        ("ssh: config",        "grep -Ev '^(#|\\s*$)' /etc/ssh/sshd_config 2>/dev/null"),
        ("ssh: log",           "journalctl -u ssh -n 20 --no-pager 2>/dev/null"),
    ]),
    (r"\bufw\b|\bfirewall\b|\biptables\b", [
        ("firewall: ufw",      "ufw status verbose 2>/dev/null"),
        ("firewall: iptables", "iptables -L -n 2>/dev/null | head -40"),
    ]),
    (r"\bsystemd?\b|\bservice\b|\bunit\b", [
        ("systemd: failed",    "systemctl list-units --state=failed --no-pager 2>/dev/null"),
        ("systemd: log",       "journalctl -n 40 --no-pager 2>/dev/null"),
    ]),
    (r"\bdns\b|\bresolvd?\b|\bnameserver\b", [
        ("dns: resolv.conf",   "cat /etc/resolv.conf 2>/dev/null"),
        ("dns: systemd-resolved","systemctl status systemd-resolved --no-pager 2>/dev/null"),
    ]),
    (r"\bcron\b|\bcrontab\b", [
        ("cron: status",       "systemctl status cron --no-pager 2>/dev/null || systemctl status crond --no-pager 2>/dev/null"),
        ("cron: log",          "journalctl -u cron -n 30 --no-pager 2>/dev/null"),
    ]),
]

# Wörter, die NICHT als Service-Name erkannt werden sollen
_SCAN_SKIP_WORDS = {
    "der", "die", "das", "ein", "eine", "nicht", "startet", "geht", "kaputt", "funktioniert",
    "error", "fehler", "problem", "issue", "help", "crash", "fail", "failed", "bitte",
    "the", "is", "not", "does", "won", "can", "running", "service", "system", "server",
    "nach", "beim", "seit", "immer", "wieder", "alle", "port", "ports", "log", "logs",
}


def _run_scan_cmd(cmd: str) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=_SCAN_CMD_TIMEOUT)
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        return (out + ("\n" + err if err and not out else "")).strip()
    except Exception:
        return ""


def _host_scan(topic: str, *, max_chars: int = 6000) -> str:
    """Führe read-only Diagnose-Befehle lokal auf dem Host aus.

    Liefert einen formatierten String, der als LLM-Kontext verwendet wird.
    """
    topic_lc = topic.lower()
    cmds: list[tuple[str, str]] = list(_SCAN_BASE)

    # Bekannte Services aus dem Topic erkennen
    matched = False
    for pattern, service_cmds in _SCAN_SERVICE_MAP:
        if re.search(pattern, topic_lc, re.IGNORECASE):
            cmds.extend(service_cmds)
            matched = True

    # Fallback: Freie Service-Name-Erkennung für unbekannte Services
    if not matched:
        words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_-]{2,}\b", topic)
        for word in words:
            if word.lower() not in _SCAN_SKIP_WORDS:
                cmds.append((f"{word}: status", f"systemctl status {word} --no-pager 2>/dev/null"))
                cmds.append((f"{word}: log", f"journalctl -u {word} -n 30 --no-pager 2>/dev/null"))
                break

    sections: list[str] = ["=== HOST-DIAGNOSE (live, vor LLM-Analyse) ==="]
    total = 0
    for label, cmd in cmds:
        if total >= max_chars:
            sections.append(f"[... weitere Ausgaben abgeschnitten (Limit {max_chars} Zeichen)]")
            break
        out = _run_scan_cmd(cmd)
        if not out:
            continue
        remaining = max_chars - total
        if len(out) > remaining:
            out = out[:remaining] + "\n[...abgeschnitten]"
        sections.append(f"--- {label} ---\n{out}")
        total += len(out)

    if len(sections) == 1:
        sections.append("(Keine Scan-Ergebnisse — Systembefehle nicht verfügbar)")
    return "\n\n".join(sections)


def _submit_repair_goal(
    text: str,
    *,
    extra_context: str = "",
    team_id: str | None = None,
    timeout: int = 300,
    planning_mode: str | None = None,
    allow_partial: bool = False,
) -> list[tuple[str, str]] | None:
    """Submit a repair goal and return (task_title, output) pairs, or None on hard failure."""
    prefix = _REPAIR_SCRIPT_CFG["prefix"]
    goal_text = f"{prefix} {text.strip()}"
    mode_data = _shortcut_mode_data("repair-admin", text.strip())

    base_context = _REPAIR_SCRIPT_CFG["context"]
    context = f"{base_context}\n\n{extra_context}".strip() if extra_context else base_context

    payload: dict = {
        "goal": goal_text,
        "context": context,
        "create_tasks": True,
        "mode": _REPAIR_SCRIPT_CFG["mode"],
        "mode_data": mode_data,
    }
    if team_id:
        payload["team_id"] = team_id
    use_template = _planning_mode_to_use_template(planning_mode)
    if use_template is not None:
        payload["use_template"] = use_template

    response = _request("POST", "/goals", body=payload, timeout=60)
    if response.status_code != 201:
        _print_error(response)
        return None

    rdata = _api_data(response)
    goal_id = (rdata.get("goal") or {}).get("id")
    task_ids = rdata.get("created_task_ids") or []
    if not goal_id:
        print("Error: No goal ID returned.", file=sys.stderr)
        return None

    print(f"Goal ID: {goal_id}  Tasks: {len(task_ids)}", file=sys.stderr)
    print(f"Waiting (max {timeout}s)...", file=sys.stderr)

    final_status = _poll_goal_status(goal_id, timeout=timeout)
    print(f"Final status: {final_status}", file=sys.stderr)

    terminal_fail = final_status in {"failed", "cancelled", "aborted", "timeout"}
    if terminal_fail and not allow_partial:
        print(
            f"Goal did not complete (status={final_status}). "
            f"Use 'ananta goal --goal-detail {goal_id}' for details.",
            file=sys.stderr,
        )
        return None
    if final_status == "partially_failed" or (terminal_fail and allow_partial):
        print("Warning: some tasks failed — extracting output from completed tasks.", file=sys.stderr)

    outputs = _fetch_goal_outputs(goal_id)
    if not outputs:
        print("No output found.", file=sys.stderr)
        return None
    return outputs


def repair_script_cmd(
    text: str,
    *,
    team_id: str | None = None,
    script_out: str | None = None,
    exec_flag: bool = False,
    tui_flag: bool = False,
    loop_flag: bool = False,
    max_iterations: int = 3,
    timeout: int = 300,
    planning_mode: str | None = None,
    scan: bool = False,
) -> None:
    # ── Phase 0: Host-Diagnose (einmalig, vor dem ersten LLM-Aufruf) ─
    scan_context = ""
    if scan:
        print("Sammle Systemdiagnose vom Host...", file=sys.stderr)
        scan_context = _host_scan(text)
        print(f"Diagnose abgeschlossen ({len(scan_context)} Zeichen).", file=sys.stderr)

    # ── TUI loop mode ────────────────────────────────────────────────
    if tui_flag or loop_flag:
        from agent.repair_tui import RepairTuiResult, build_retry_context, run_repair_tui

        iterations = max_iterations if loop_flag else 1
        history: list[RepairTuiResult] = []

        for i in range(1, iterations + 1):
            print(
                f"\n{'─' * 40}\nRepair TUI  Versuch {i}/{iterations}\n{'─' * 40}",
                file=sys.stderr,
            )
            retry_ctx = build_retry_context(history)
            extra_ctx = "\n\n".join(filter(None, [scan_context, retry_ctx]))
            outputs = _submit_repair_goal(
                text,
                extra_context=extra_ctx,
                team_id=team_id,
                timeout=timeout,
                planning_mode=planning_mode,
                allow_partial=True,
            )
            if outputs is None:
                print("Kein Output — Schleife abgebrochen.", file=sys.stderr)
                sys.exit(1)

            result = run_repair_tui(
                outputs,
                goal_title=text.strip()[:60],
                iteration=i,
                max_iterations=iterations,
            )

            if result.verdict == "fixed":
                print("\nProblem behoben.", file=sys.stderr)
                sys.exit(0)
            if result.verdict == "abort":
                print("\nAbgebrochen.", file=sys.stderr)
                sys.exit(0)

            # verdict == "retry"
            history.append(result)
            if i == iterations:
                print(f"\nMaximale Iterationen ({iterations}) erreicht.", file=sys.stderr)

        sys.exit(0)

    # ── Non-TUI mode (pipe-friendly) ─────────────────────────────────
    outputs = _submit_repair_goal(
        text,
        extra_context=scan_context,
        team_id=team_id,
        timeout=timeout,
        planning_mode=planning_mode,
        allow_partial=False,
    )
    if outputs is None:
        sys.exit(1)

    combined = "\n\n".join(txt for _, txt in outputs)
    script_content = _extract_script_blocks(combined)

    if script_out:
        with open(script_out, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/bash\n")
            fh.write(script_content)
            if not script_content.endswith("\n"):
                fh.write("\n")
        print(f"Script saved: {script_out}", file=sys.stderr)
        print(f"Review : cat {script_out}", file=sys.stderr)
        print(f"Execute: bash {script_out}", file=sys.stderr)
        return

    if exec_flag:
        print("\n--- Script ---", file=sys.stderr)
        print(script_content, file=sys.stderr)
        print("--- End ---", file=sys.stderr)
        try:
            answer = input("\nExecute this script? [y/N]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.", file=sys.stderr)
            sys.exit(0)
        if answer in {"y", "yes"}:
            import subprocess
            result = subprocess.run(["bash", "-c", script_content])
            sys.exit(result.returncode)
        else:
            print("Execution skipped.", file=sys.stderr)
        return

    # Default: clean stdout (pipe-friendly)
    print(script_content)


def get_base_url():
    configured = os.environ.get("ANANTA_BASE_URL")
    if configured:
        return configured.rstrip("/")
    return f"http://localhost:{settings.port}"


def get_auth_token(base_url: str) -> str:
    username = os.environ.get("ANANTA_USER", "admin")
    password = os.environ.get("ANANTA_PASSWORD", "admin")

    try:
        response = requests.post(f"{base_url}/login", json={"username": username, "password": password}, timeout=10)
    except requests.RequestException as exc:
        _print_terminal("Error: Hub not reachable at {}", base_url)
        _print_terminal("Next step: start the hub or set ANANTA_BASE_URL. Details: {}", str(exc))
        sys.exit(1)

    if response.status_code != 200:
        _print_terminal("Error: Login failed - {}", response.status_code)
        print("Next step: check ANANTA_USER/ANANTA_PASSWORD or reset the local admin password.")
        sys.exit(1)

    data = response.json().get("data", {})
    return data.get("access_token", "")


def _request(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    params: dict | None = None,
    timeout: int = 30,
):
    base_url = get_base_url()
    token = get_auth_token(base_url)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        return requests.request(
            method=method,
            url=f"{base_url}{path}",
            headers=headers,
            json=body,
            params=params,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        _print_terminal("Error: Hub request failed for {}", path)
        _print_terminal("Next step: run `ananta first-run` and verify ANANTA_BASE_URL. Details: {}", str(exc))
        sys.exit(1)


def _read_json(response: requests.Response) -> dict:
    try:
        return response.json()
    except ValueError:
        return {}


def _api_data(response: requests.Response):
    payload = _read_json(response)
    if isinstance(payload, dict):
        return payload.get("data", payload)
    return {}


def _terminal(value, *, max_chars: int = 240) -> str:
    return sanitize_terminal_text(value, max_chars=max_chars)


def _print_terminal(template: str, *values) -> None:
    print(template.format(*(_terminal(value) for value in values)))


def _print_error(response: requests.Response):
    payload = _read_json(response)
    message = payload.get("message") if isinstance(payload, dict) else None
    if message:
        _print_terminal("Error: {} - {}", response.status_code, message)
    else:
        _print_terminal("Error: {} - {}", response.status_code, response.text)
    _print_terminal("Next step: {}", _next_step_for_status(response.status_code, message or response.text))


def _next_step_for_status(status_code: int, message: str | None = None) -> str:
    text = str(message or "").lower()
    if status_code in {401, 403}:
        return "check ANANTA_USER/ANANTA_PASSWORD and governance permissions."
    if status_code == 404:
        return "check that the hub version exposes this endpoint and that ANANTA_BASE_URL points to the hub."
    if status_code == 409 or "policy" in text or "governance" in text or "blocked" in text:
        return "review the governance mode or narrow the goal before retrying."
    if status_code >= 500:
        return "check hub logs, then run `ananta status` after the hub is healthy."
    return "retry with a narrower goal or run `ananta status` for readiness."


def show_first_run():
    base_url = get_base_url()
    print("Ananta CLI First Run")
    print("=====================")
    _print_terminal("Hub URL: {}", base_url)
    print("\n1. Optional environment:")
    _print_terminal("   export ANANTA_BASE_URL={}", base_url)
    print("   export ANANTA_USER=admin")
    print("   export ANANTA_PASSWORD=<password>")
    print("\n2. Readiness check:")
    print("   ananta status")
    print("\n3. Official first goal:")
    print('   ananta plan "Analysiere dieses Repository und schlage die naechsten Schritte vor"')
    print("\nSuccess signal:")
    print("   - Goal ID is printed")
    print("   - Status is printed")
    print("   - Tasks created is greater than 0 for a planned goal")
    print("\nAfter success:")
    print("   ananta goal --tasks --task-status todo")
    print("   ananta goal --goal-detail <goal_id>")
    print("\nIf it fails:")
    print("   - login error: check ANANTA_USER/ANANTA_PASSWORD")
    print("   - connection error: start the hub or set ANANTA_BASE_URL")
    print("   - governance/policy block: narrow the goal or inspect the governance mode")


def _planning_mode_to_use_template(planning_mode: str | None) -> bool | None:
    """Map --planning-mode flag to use_template API field."""
    if not planning_mode:
        return None
    m = planning_mode.strip().lower()
    if m == "llm":
        return False
    if m in {"template", "auto"}:
        return True
    return None


def submit_goal(
    goal: str,
    context: str | None = None,
    team_id: str | None = None,
    create_tasks: bool = True,
    mode: str | None = None,
    mode_data: dict | None = None,
    output_dir: str | None = None,
    planning_mode: str | None = None,
):
    payload = {"goal": goal, "create_tasks": create_tasks}
    if context:
        payload["context"] = context
    if team_id:
        payload["team_id"] = team_id
    if mode:
        payload["mode"] = mode
    if mode_data:
        payload["mode_data"] = mode_data
    if output_dir:
        payload.setdefault("execution_preferences", {})["output_dir"] = output_dir
    use_template = _planning_mode_to_use_template(planning_mode)
    if use_template is not None:
        payload["use_template"] = use_template

    response = _request("POST", "/goals", body=payload, timeout=60)
    if response.status_code == 201:
        data = _api_data(response)
        goal_payload = data.get("goal", {})
        created_task_ids = data.get("created_task_ids", [])
        _print_terminal("Goal submitted: {}", goal_payload.get("goal", goal))
        _print_terminal("Goal ID: {}", goal_payload.get("id", "N/A"))
        _print_terminal("Status: {}", goal_payload.get("status", "N/A"))
        print(f"Tasks created: {len(created_task_ids)}")
        for task_id in created_task_ids:
            _print_terminal("  - {}", task_id)
        reference_profile = dict(goal_payload.get("reference_profile") or {})
        if reference_profile:
            _print_terminal("Reference profile: {}", reference_profile.get("profile_id") or "-")
            _print_terminal("Reference fit: {}", reference_profile.get("fit_level") or "n/a")
            if reference_profile.get("reason_summary"):
                _print_terminal("Reference reason: {}", reference_profile.get("reason_summary"))
        goal_id = goal_payload.get("id")
        if goal_id:
            _print_terminal("Next step: ananta goal --goal-detail {}", goal_id)
        print("Success signal: Goal ID, status and task count are visible.")
        return created_task_ids
    _print_error(response)
    return []


def submit_shortcut(kind: str, text: str, *, team_id: str | None = None, create_tasks: bool = True, output_dir: str | None = None, planning_mode: str | None = None):
    shortcut = SHORTCUT_GOALS.get(kind)
    if not shortcut:
        _print_terminal("Error: Unknown shortcut '{}'. Available: {}", kind, ", ".join(sorted(SHORTCUT_GOALS)))
        return []
    shortcut_text = text.strip()
    return submit_goal(
        goal=f"{shortcut['prefix']} {shortcut_text}",
        context=shortcut["context"],
        team_id=team_id,
        create_tasks=create_tasks,
        mode=shortcut.get("mode"),
        mode_data=_shortcut_mode_data(kind, shortcut_text),
        output_dir=output_dir,
        planning_mode=planning_mode,
    )


def _shortcut_mode_data(kind: str, text: str) -> dict:
    data = {"shortcut": kind, "shortcut_text": text}
    if kind == "patch":
        data["issue_description"] = text
    elif kind == "review":
        data["scope"] = text
    elif kind == "diagnose":
        data["issue_symptom"] = text
    elif kind == "analyze":
        data["scope"] = text
    elif kind == "new-project":
        data["project_idea"] = text
    elif kind == "evolve-project":
        data["change_goal"] = text
    elif kind == "repair-admin":
        data["issue_symptom"] = text
        data["platform_target"] = "auto"
        data["execution_scope"] = "bounded_repair"
        data["dry_run"] = True
        data["no_task_dependencies"] = True
    return data


def show_status():
    readiness_res = _request("GET", "/goals/readiness", timeout=10)
    if readiness_res.status_code == 200:
        readiness = _api_data(readiness_res)
        print("Goal Readiness:")
        print(f"  Happy path ready: {readiness.get('happy_path_ready', False)}")
        print(f"  Planning available: {readiness.get('planning_available', False)}")
        print(f"  Worker available: {readiness.get('worker_available', False)}")
        _print_terminal("  Active team: {}", readiness.get("active_team_id") or "-")
    else:
        _print_error(readiness_res)

    planner_res = _request("GET", "/tasks/auto-planner/status", timeout=10)
    if planner_res.status_code == 200:
        data = _api_data(planner_res)
        stats = data.get("stats", {})
        print("\nAuto-Planner Status:")
        print(f"  Enabled: {data.get('enabled', False)}")
        print(f"  Goals processed: {stats.get('goals_processed', 0)}")
        print(f"  Tasks created: {stats.get('tasks_created', 0)}")
        print(f"  Follow-ups created: {stats.get('followups_created', 0)}")
        print(f"  Errors: {stats.get('errors', 0)}")
    else:
        _print_error(planner_res)


def list_tasks(status: str = None, limit: int = 20):
    params = {"limit": limit}
    if status:
        params["status"] = status

    response = _request("GET", "/tasks", params=params, timeout=10)

    if response.status_code == 200:
        tasks = _read_json(response)
        if isinstance(tasks, dict):
            tasks = tasks.get("data", [])

        print(f"Tasks ({len(tasks)}):")
        for task in tasks[:limit]:
            task_id = task.get("id", "N/A")
            title = task.get("title", "N/A")[:50]
            task_status = task.get("status", "N/A")
            _print_terminal("  [{:12}] {}: {}", task_status, task_id, title)
    else:
        _print_error(response)


def list_goals(limit: int = 20):
    response = _request("GET", "/goals", timeout=15)
    if response.status_code != 200:
        _print_error(response)
        return
    goals = _api_data(response)
    if not isinstance(goals, list):
        goals = []
    print(f"Goals ({min(limit, len(goals))}/{len(goals)}):")
    for goal in goals[:limit]:
        print(
            "  [{:10}] {} (team={}) {}".format(
                _terminal(goal.get("status", "N/A")),
                _terminal(goal.get("id", "N/A")),
                _terminal(goal.get("team_id") or "-"),
                _terminal(str(goal.get("goal", ""))[:90]),
            )
        )


def show_goal_detail(goal_id: str):
    response = _request("GET", f"/goals/{goal_id}/detail", timeout=20)
    if response.status_code != 200:
        _print_error(response)
        return
    data = _api_data(response)
    goal = data.get("goal", {})
    trace = data.get("trace", {})
    artifacts = data.get("artifacts", {})
    summary = artifacts.get("result_summary", {})
    _print_terminal("Goal: {}", goal.get("id", goal_id))
    _print_terminal("  Status: {}", goal.get("status", "N/A"))
    _print_terminal("  Team: {}", goal.get("team_id") or "-")
    _print_terminal("  Trace: {}", trace.get("trace_id") or "-")
    print(f"  Tasks: total={summary.get('task_count', 0)} completed={summary.get('completed_tasks', 0)} failed={summary.get('failed_tasks', 0)}")
    headline = artifacts.get("headline_artifact") or {}
    if headline.get("preview"):
        _print_terminal("  Headline artifact: {}", str(headline.get("preview"))[:120])


def list_modes():
    response = _request("GET", "/goals/modes", timeout=10)
    if response.status_code != 200:
        _print_error(response)
        return
    modes = _api_data(response)
    if not isinstance(modes, list):
        modes = []
    print(f"Goal modes ({len(modes)}):")
    for mode in modes:
        _print_terminal("  - {}: {}", mode.get("id"), mode.get("title"))


def list_artifacts(limit: int = 20):
    response = _request("GET", "/artifacts", timeout=10)
    if response.status_code != 200:
        _print_error(response)
        return
    artifacts = _api_data(response)
    if not isinstance(artifacts, list):
        artifacts = []
    print(f"Artifacts ({min(limit, len(artifacts))}/{len(artifacts)}):")
    for artifact in artifacts[:limit]:
        _print_terminal(
            "  - {} [{}] {}",
            artifact.get("id", "N/A"),
            artifact.get("status", "N/A"),
            artifact.get("latest_filename") or artifact.get("latest_media_type") or "-",
        )


def analyze_task_followups(task_id: str, output: str | None = None):
    payload = {}
    if output:
        payload["output"] = output
    response = _request("POST", f"/tasks/auto-planner/analyze/{task_id}", body=payload, timeout=45)
    if response.status_code != 200:
        _print_error(response)
        return
    data = _api_data(response)
    followups = data.get("followups_created") or []
    _print_terminal("Follow-up analysis completed for task {}", task_id)
    print(f"  Follow-ups created: {len(followups)}")
    for followup in followups:
        _print_terminal("  - {}: {}", followup.get("id", "N/A"), str(followup.get("title", ""))[:80])


def _parse_mode_data(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"Error: Invalid JSON for --mode-data ({exc})")
        sys.exit(2)
    if not isinstance(parsed, dict):
        print("Error: --mode-data must be a JSON object")
        sys.exit(2)
    return parsed


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="CLI for Ananta Goals, Tasks, Artifacts and Diagnostics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  First run:
    ananta first-run
    ananta status
    ananta plan "Analysiere dieses Repository und schlage die naechsten Schritte vor"

  Golden path (PRD-021): Short human-friendly commands:
    ananta ask "What should I do next?"
    ananta plan "Prepare a release checklist"
    ananta analyze "Find the riskiest frontend areas"
    ananta review "Review the auth changes"
    ananta diagnose "Docker frontend cannot reach hub"
    ananta patch "Fix failing login validation"
    ananta new-project "Build a small release-check tool for maintainers"
    ananta evolve-project "Add a guided project-start mode to the dashboard"
    ananta repair-admin "Service restart loop after update"

  Write output to a specific folder:
    ananta new-project --output-dir myproject "Build a small tool"
    ananta evolve-project --output-dir /app/myproject "Add auth module"
    ananta ask --output-dir ./out "Generate a README for this repo"
    (Docker: use paths relative to project root, e.g. myproject -> /app/myproject in container)

  Repair-script (synchronous, pipe-friendly):
    ananta repair-script "Nginx crashes on startup"
    ananta repair-script "Nginx crashes" > fix.sh && cat fix.sh
    ananta repair-script "Nginx crashes" | bash
    ananta repair-script "Nginx crashes" --script-out fix.sh
    ananta repair-script "Nginx crashes" --exec
    ananta repair-script "Nginx crashes" --tui          # interactive TUI: approve/run on host
    ananta repair-script "Nginx crashes" --loop         # TUI + automatische Retry-Schleife
    ananta repair-script "Nginx crashes" --loop --max-iterations 5
    ananta repair-script "Nginx crashes" --wait-timeout 120

  Planning strategy (default: llm — KI-gestützt):
    ananta ask "What next?" --planning-mode llm      # KI-Planung (Standard)
    ananta ask "What next?" --planning-mode template  # Template-Planung
    ananta repair-script "Nginx" --planning-mode llm
    ananta goal --goal "..." --planning-mode llm

  Profile/Governance (GOV-051/PRF-080):
    ananta goal --config-show
    ananta goal --set-runtime-profile demo --set-governance-mode safe

  Submit guided mode:
    ananta goal --goal "Container restart-loop" --mode docker_compose_repair --mode-data '{"service":"hub"}'

  List tasks:
    ananta goal --tasks

  List goals:
    ananta goal --goals

  Goal detail:
    ananta goal --goal-detail <goal_id>

  List guided modes:
    ananta goal --modes

  Analyze follow-ups for a task:
    ananta goal --analyze-task <task_id>

  Check status:
    ananta status
""",
    )

    parser.add_argument(
        "goal",
        nargs="?",
        help="Goal description to submit, or shortcut: ask/plan/analyze/review/diagnose/patch/new-project/evolve-project/repair-admin/repair-script",
    )
    parser.add_argument("extra", nargs="*", help="Additional words for shortcut goals")
    parser.add_argument("--goal", "-g", dest="goal_flag", help="Goal description (alternative)")
    parser.add_argument("--context", "-c", help="Additional context for the goal")
    parser.add_argument("--team", "-t", help="Team ID to assign tasks to")
    parser.add_argument("--mode", help="Guided goal mode ID (e.g. code_fix, docker_compose_repair)")
    parser.add_argument("--mode-data", help='JSON object for mode fields, e.g. \'{"service":"hub"}\'')
    parser.add_argument("--output-dir", "-o", help="Directory where generated files are written (default: isolated workspace)")
    parser.add_argument("--script-out", "-S", metavar="FILE", help="Save the extracted repair script to this file (repair-script only)")
    parser.add_argument("--exec", dest="exec_script", action="store_true", help="Review then optionally execute the generated script (repair-script only)")
    parser.add_argument("--tui", dest="tui_flag", action="store_true", help="Interactive TUI: review and approve commands for controlled host execution (repair-script only)")
    parser.add_argument("--loop", dest="loop_flag", action="store_true", help="TUI loop: plan → approve → execute → test → retry until fixed (repair-script only)")
    parser.add_argument("--max-iterations", type=int, default=3, metavar="N", help="Maximum loop iterations for --loop (default 3)")
    parser.add_argument("--scan", dest="scan_flag", action="store_true", help="Host-Diagnose vor LLM-Submission: sammelt Systemzustand lokal (repair-script only)")
    parser.add_argument("--wait-timeout", type=int, default=300, metavar="SECONDS", help="Max seconds to wait for goal completion, default 300 (repair-script only)")
    parser.add_argument("--no-create", action="store_true", help="Don't create tasks, just analyze")
    parser.add_argument("--status", "-s", action="store_true", help="Show Goal readiness + Auto-Planner status")
    parser.add_argument("--first-run", action="store_true", help="Show the official first CLI path, success signals and failure help")
    parser.add_argument("--goals", action="store_true", help="List goals")
    parser.add_argument("--goal-detail", help="Show detail for a goal ID")
    parser.add_argument("--modes", action="store_true", help="List guided goal modes")
    parser.add_argument("--tasks", action="store_true", help="List recent tasks")
    parser.add_argument("--task-status", help="Filter tasks by status")
    parser.add_argument("--artifacts", action="store_true", help="List recent artifacts")
    parser.add_argument("--analyze-task", help="Analyze a completed task for follow-up work")
    parser.add_argument("--output", help="Optional output text for --analyze-task")
    parser.add_argument("--limit", "-l", type=int, default=20, help="Limit number of results")
    parser.add_argument("--config-show", action="store_true", help="Show effective runtime_profile + governance_mode")
    parser.add_argument("--set-runtime-profile", default="", help="Update runtime_profile via POST /config")
    parser.add_argument("--set-governance-mode", default="", help="Update governance_mode via POST /config")
    parser.add_argument(
        "--planning-mode",
        choices=["llm", "template", "auto"],
        default=None,
        metavar="MODE",
        help="Planning strategy: llm (default), template, auto. Overrides server-side default.",
    )

    args = parser.parse_args(argv)

    if args.first_run:
        show_first_run()
        return

    if args.config_show or args.set_runtime_profile or args.set_governance_mode:
        patch = {}
        if args.set_runtime_profile:
            patch["runtime_profile"] = str(args.set_runtime_profile).strip()
        if args.set_governance_mode:
            patch["governance_mode"] = str(args.set_governance_mode).strip()
        if patch:
            res = _request("POST", "/config", body=patch, timeout=10)
            if res.status_code != 200:
                _print_error(res)
                sys.exit(1)
        res = _request("GET", "/config", timeout=10)
        if res.status_code != 200:
            _print_error(res)
            sys.exit(1)
        cfg = _api_data(res) or {}
        runtime = (cfg.get("runtime_profile_effective") or {}).get("effective") or cfg.get("runtime_profile") or "-"
        governance = (cfg.get("governance_mode_effective") or {}).get("effective") or cfg.get("governance_mode") or "-"
        _print_terminal("runtime_profile: {}", runtime)
        _print_terminal("governance_mode: {}", governance)
        return

    if args.status:
        show_status()
    elif args.goals:
        list_goals(limit=args.limit)
    elif args.goal_detail:
        show_goal_detail(args.goal_detail)
    elif args.modes:
        list_modes()
    elif args.tasks:
        list_tasks(status=args.task_status, limit=args.limit)
    elif args.artifacts:
        list_artifacts(limit=args.limit)
    elif args.analyze_task:
        analyze_task_followups(args.analyze_task, output=args.output)
    elif args.goal == "repair-script":
        shortcut_text = " ".join(args.extra).strip()
        if not shortcut_text:
            print("Error: 'repair-script' needs a short description", file=sys.stderr)
            sys.exit(2)
        repair_script_cmd(
            shortcut_text,
            team_id=args.team,
            script_out=args.script_out,
            exec_flag=args.exec_script,
            tui_flag=args.tui_flag,
            loop_flag=args.loop_flag,
            scan=args.scan_flag,
            max_iterations=args.max_iterations,
            timeout=args.wait_timeout,
            planning_mode=args.planning_mode,
        )
    elif args.goal in SHORTCUT_GOALS:
        shortcut_text = " ".join(args.extra).strip()
        if not shortcut_text:
            print(f"Error: '{args.goal}' needs a short description")
            sys.exit(2)
        output_dir = args.output_dir.strip() if args.output_dir else None
        submit_shortcut(args.goal, shortcut_text, team_id=args.team, create_tasks=not args.no_create, output_dir=output_dir, planning_mode=args.planning_mode)
    elif args.goal or args.goal_flag:
        goal_text = args.goal or args.goal_flag
        if args.extra:
            goal_text = " ".join([goal_text, *args.extra])
        create_tasks = not args.no_create
        output_dir = args.output_dir.strip() if args.output_dir else None
        submit_goal(
            goal=goal_text,
            context=args.context,
            team_id=args.team,
            create_tasks=create_tasks,
            mode=args.mode,
            mode_data=_parse_mode_data(args.mode_data),
            output_dir=output_dir,
            planning_mode=args.planning_mode,
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
