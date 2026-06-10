"""repair-script command: host scan, synchronous repair goals, script extraction (SPLIT-013).

All hub I/O goes through the agent.cli_goals facade (`_cli.*`) so that
tests can keep monkeypatching agent.cli_goals attributes.
"""

import re
import subprocess
import sys
import time

from agent import cli_goals as _cli

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
        res = _cli._request("GET", f"/goals/{goal_id}", timeout=10)
        if res.status_code == 200:
            data = _cli._api_data(res)
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
                detail_res = _cli._request("GET", f"/goals/{goal_id}/detail", timeout=15)
                if detail_res.status_code == 200:
                    detail_data = _cli._api_data(detail_res)
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
    res = _cli._request("GET", f"/tasks/{task_id}", timeout=15)
    if res.status_code == 200:
        return str(_cli._api_data(res).get("last_output") or "").strip()
    return ""


def _fetch_goal_outputs(goal_id: str) -> list[tuple[str, str]]:
    res = _cli._request("GET", f"/goals/{goal_id}/detail", timeout=20)
    if res.status_code != 200:
        return []
    data = _cli._api_data(res)
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
        out = _cli._run_scan_cmd(cmd)
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


def _switch_autopilot_to_goal(goal_id: str) -> None:
    """Point the running autopilot at this goal and force an immediate tick."""
    try:
        resp = _cli._request("POST", "/tasks/autopilot/start", body={"goal": goal_id}, timeout=30)
        if resp.status_code != 200:
            print(f"Warning: autopilot start returned {resp.status_code}", file=sys.stderr)
            return
        _cli._request("POST", "/tasks/autopilot/tick", body={}, timeout=30)
    except SystemExit:
        print("Warning: autopilot switch failed (hub unreachable?)", file=sys.stderr)


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
    mode_data = _cli._shortcut_mode_data("repair-admin", text.strip())

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
    use_template = _cli._planning_mode_to_use_template(planning_mode)
    if use_template is not None:
        payload["use_template"] = use_template

    response = _cli._request("POST", "/goals", body=payload, timeout=60)
    if response.status_code != 201:
        _cli._print_error(response)
        return None

    rdata = _cli._api_data(response)
    goal_id = (rdata.get("goal") or {}).get("id")
    task_ids = rdata.get("created_task_ids") or []
    if not goal_id:
        print("Error: No goal ID returned.", file=sys.stderr)
        return None

    print(f"Goal ID: {goal_id}  Tasks: {len(task_ids)}", file=sys.stderr)

    # Ensure the autopilot switches to this goal immediately.
    # The server-side create_goal handler also calls _ensure_autopilot_running,
    # but we do it here explicitly for robustness (in case auto_planner is
    # disabled or the server-side path fails).
    _switch_autopilot_to_goal(goal_id)

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
            outputs = _cli._submit_repair_goal(
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
    outputs = _cli._submit_repair_goal(
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
            result = subprocess.run(["bash", "-c", script_content])
            sys.exit(result.returncode)
        else:
            print("Execution skipped.", file=sys.stderr)
        return

    # Default: clean stdout (pipe-friendly)
    print(script_content)
