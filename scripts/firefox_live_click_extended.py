#!/usr/bin/env python3
import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib import request

BASE = os.getenv("ANANTA_SELENIUM_URL", "http://127.0.0.1:4444/wd/hub")
APP_BASE = os.getenv("ANANTA_FRONTEND_URL", "http://angular-frontend:4200")
LOGIN_USER = os.getenv("E2E_ADMIN_USER", os.getenv("INITIAL_ADMIN_USER", "admin"))
LOGIN_PASS = os.getenv("E2E_ADMIN_PASSWORD", os.getenv("INITIAL_ADMIN_PASSWORD", "AnantaAdminPassword123!"))
DEFAULT_REPORT_DIR = Path("test-reports/live-click")
DEFAULT_PHASES = ["setup", "goal", "execution", "review"]


def wd(method: str, path: str, payload=None):
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = request.Request(BASE + path, data=data, headers=headers, method=method)
    with request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def js(session_id: str, script: str, args=None):
    return wd("POST", f"/session/{session_id}/execute/sync", {"script": script, "args": args or []})


def wait_for(session_id: str, script: str, timeout: int = 25) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            out = js(session_id, script)
            if out.get("value"):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def current_route_and_title(session_id: str) -> Dict[str, str]:
    out = js(
        session_id,
        """
        const h=document.querySelector('h1,h2,h3');
        return {
          path: location.pathname + location.search + location.hash,
          title: h ? h.textContent.trim() : (document.title || '')
        };
        """,
    ).get("value") or {}
    return {"path": str(out.get("path") or ""), "title": str(out.get("title") or "")}


def list_visible_errors(session_id: str) -> List[Dict[str, str]]:
    out = js(
        session_id,
        """
        const nodes=[...document.querySelectorAll('.notification.error,.toast.toast-error,[role="alert"]')];
        const viewport={w:window.innerWidth,h:window.innerHeight};
        return nodes
          .filter(n => {
            const style = window.getComputedStyle(n);
            const box = n.getBoundingClientRect();
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            if (box.height <= 0 || box.width <= 0) return false;
            return box.bottom >= 0 && box.right >= 0 && box.top <= viewport.h && box.left <= viewport.w;
          })
          .map(n => ({ text: (n.textContent || '').trim().slice(0, 500), route: location.pathname }))
          .filter(x => !!x.text)
          .slice(0, 20);
        """,
    ).get("value")
    if not isinstance(out, list):
        return []
    result: List[Dict[str, str]] = []
    for item in out:
        if not isinstance(item, dict):
            continue
        result.append({"text": str(item.get("text") or ""), "route": str(item.get("route") or "")})
    return result


def step_nav(session_id: str, route: str, settle_s: float = 1.5):
    js(
        session_id,
        """
        const href = arguments[0];
        const a = [...document.querySelectorAll('a[href]')]
          .find(x => x.getAttribute('href') === href);
        if (a) { a.click(); return true; }
        location.href = href;
        return false;
        """,
        [route],
    )
    time.sleep(settle_s)


def settle(extra_seconds: float):
    if extra_seconds > 0:
        time.sleep(extra_seconds)


def parse_phases(raw: str) -> List[str]:
    allowed = {"setup", "goal", "execution", "review", "all"}
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    if not parts:
        return DEFAULT_PHASES[:]
    unknown = [p for p in parts if p not in allowed]
    if unknown:
        raise ValueError(f"Unknown phases: {', '.join(unknown)}")
    if "all" in parts:
        return DEFAULT_PHASES[:]
    return parts


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


def phase_setup(session_id: str, report: dict, hard_fail: bool, step_delay_seconds: float, bootstrap_setup: bool):
    t0 = time.time()
    wd("POST", f"/session/{session_id}/url", {"url": f"{APP_BASE}/login"})
    ok_form = wait_for(session_id, "return !!document.querySelector('input[name=\"username\"]')", 25)
    if not ok_form:
        raise RuntimeError("Login form not visible")
    js(
        session_id,
        """
        const u=document.querySelector('input[name="username"]');
        const p=document.querySelector('input[name="password"]');
        const username=arguments[0];
        const password=arguments[1];
        if(u){u.value=username;u.dispatchEvent(new Event('input',{bubbles:true}));}
        if(p){p.value=password;p.dispatchEvent(new Event('input',{bubbles:true}));}
        const b=[...document.querySelectorAll('button')].find(x=>/Anmelden|Verifizieren|Login/i.test((x.textContent||'').trim()));
        if(b){b.click(); return true;}
        return false;
        """,
        [LOGIN_USER, LOGIN_PASS],
    )
    ok_login = wait_for(session_id, "return location.pathname.includes('/dashboard') || location.pathname==='/'", 18)
    print("login_ok", ok_login, flush=True)
    record_step(report, "setup", "login", t0, ok_login, current_route_and_title(session_id))
    if not ok_login:
        raise RuntimeError("Login did not reach dashboard")
    settle(step_delay_seconds)
    gate_visible_errors(session_id, report, "setup", hard_fail)
    if not bootstrap_setup:
        return

    # Template setup
    t1 = time.time()
    step_nav(session_id, "/templates")
    wait_for(session_id, "return !![...document.querySelectorAll('h1,h2,h3')].find(x=>/Templates/i.test((x.textContent||'')))", 20)
    template_name = f"Live UI Template {int(time.time())}"
    template_created = bool(
        js(
            session_id,
            """
            const name=arguments[0];
            const desc='Template aus modularisiertem Live-Klicktest';
            const prompt='Du bist {{agent_name}} und bearbeitest {{task_title}}.';
            const nameInput=document.querySelector('input[placeholder="Name"]');
            const descInput=document.querySelector('input[placeholder="Beschreibung"]');
            const promptArea=document.querySelector('textarea[placeholder*="Platzhalter"]');
            const save=[...document.querySelectorAll('button')]
              .find(x=>/Anlegen\\s*\\/\\s*Speichern|Anlegen|Speichern/i.test((x.textContent||'').trim()));
            if(nameInput){nameInput.focus();nameInput.value=name;nameInput.dispatchEvent(new Event('input',{bubbles:true}));}
            if(descInput){descInput.focus();descInput.value=desc;descInput.dispatchEvent(new Event('input',{bubbles:true}));}
            if(promptArea){promptArea.focus();promptArea.value=prompt;promptArea.dispatchEvent(new Event('input',{bubbles:true}));}
            if(save){ save.click(); return true; }
            return false;
            """,
            [template_name],
        ).get("value")
    )
    time.sleep(2.0)
    print("template_create_clicked", template_created, "template", template_name, flush=True)
    record_step(report, "setup", "template_create", t1, template_created, {"template_name": template_name, **current_route_and_title(session_id)})
    settle(step_delay_seconds)
    gate_visible_errors(session_id, report, "setup", hard_fail)

    # Blueprint + Team setup
    t2 = time.time()
    step_nav(session_id, "/teams")
    wait_for(session_id, "return !![...document.querySelectorAll('h1,h2,h3')].find(x=>/Blueprint|Teams/i.test((x.textContent||'')))", 25)
    blueprint_name = f"Live UI Blueprint {int(time.time())}"
    blueprint_created = bool(
        js(
            session_id,
            """
            const blueprint=arguments[0];
            const panel=document.querySelector('.teams-editor-panel') || document;
            const labels=[...panel.querySelectorAll('label')];
            function inputByLabel(rx){
              const l=labels.find(x=>rx.test((x.textContent||'').trim()));
              if(!l) return null;
              const id=l.getAttribute('for');
              if(id) return panel.querySelector('#'+CSS.escape(id));
              return l.querySelector('input,textarea,select');
            }
            const nameInput=inputByLabel(/^Name$/i) || panel.querySelector('input[placeholder="Name"]');
            const descInput=inputByLabel(/Beschreibung/i) || panel.querySelector('input[placeholder="Beschreibung"]');
            const addRole=[...panel.querySelectorAll('button')].find(x=>/Rolle hinzuf/i.test((x.textContent||'')));
            if(nameInput){nameInput.focus();nameInput.value=blueprint;nameInput.dispatchEvent(new Event('input',{bubbles:true}));}
            if(descInput){descInput.focus();descInput.value='Blueprint aus modularisiertem Live-Klicktest';descInput.dispatchEvent(new Event('input',{bubbles:true}));}
            if(addRole){ addRole.click(); }
            const roleInput=[...panel.querySelectorAll('label')].find(x=>/Rollenname/i.test((x.textContent||'')));
            if(roleInput){
              const id=roleInput.getAttribute('for');
              const el=id ? panel.querySelector('#'+CSS.escape(id)) : roleInput.querySelector('input');
              if(el){el.focus();el.value='Implementer';el.dispatchEvent(new Event('input',{bubbles:true}));}
            }
            const selects=[...panel.querySelectorAll('select')];
            for(const s of selects){
              const valid=[...s.options].find(o=>o.value && !o.disabled);
              if(valid){s.value=valid.value;s.dispatchEvent(new Event('change',{bubbles:true}));}
            }
            const create=[...panel.querySelectorAll('button')].find(x=>/^Erstellen$/i.test((x.textContent||'').trim()));
            if(create){ create.click(); return true; }
            return false;
            """,
            [blueprint_name],
        ).get("value")
    )
    time.sleep(2.2)
    team_name = f"Live UI Team {int(time.time())}"
    team_created = bool(
        js(
            session_id,
            """
            const teamName=arguments[0];
            const fromBlueprint=[...document.querySelectorAll('button')].find(x=>/^Teams aus Blueprint$/i.test((x.textContent||'').trim()));
            if(fromBlueprint) fromBlueprint.click();
            const card=document.querySelector('.card.card-success') || document;
            const selects=[...card.querySelectorAll('select')];
            for(const s of selects){
              const valid=[...s.options].find(o=>o.value && !o.disabled);
              if(valid){s.value=valid.value;s.dispatchEvent(new Event('change',{bubbles:true}));}
            }
            const teamInput=[...card.querySelectorAll('input')].find(x=>/teamname|team name/i.test((x.getAttribute('aria-label')||'') + ' ' + (x.placeholder||'')));
            const byLabel=[...card.querySelectorAll('label')].find(x=>/Teamname/i.test((x.textContent||'')));
            let finalInput=teamInput;
            if(!finalInput && byLabel){
              const id=byLabel.getAttribute('for');
              if(id) finalInput=card.querySelector('#'+CSS.escape(id));
            }
            if(finalInput){finalInput.focus();finalInput.value=teamName;finalInput.dispatchEvent(new Event('input',{bubbles:true}));}
            const create=[...card.querySelectorAll('button')].find(x=>/^Team erstellen$/i.test((x.textContent||'').trim()));
            if(create){ create.click(); return true; }
            return false;
            """,
            [team_name],
        ).get("value")
    )
    time.sleep(2.0)
    ok = blueprint_created and team_created
    print("blueprint_create_clicked", blueprint_created, "team_create_clicked", team_created, flush=True)
    record_step(
        report,
        "setup",
        "blueprint_team_create",
        t2,
        ok,
        {"blueprint_name": blueprint_name, "team_name": team_name, **current_route_and_title(session_id)},
    )
    settle(step_delay_seconds)
    gate_visible_errors(session_id, report, "setup", hard_fail)


def phase_goal(session_id: str, report: dict, hard_fail: bool, step_delay_seconds: float, goal_wait_seconds: float):
    t0 = time.time()
    step_nav(session_id, "/auto-planner")
    goal_name = f"Live Goal {int(time.time())}"
    goal_clicked = bool(
        js(
            session_id,
            """
            const goal=arguments[0];
            const input=document.querySelector('[data-testid="auto-planner-goal-input"]');
            const btn=document.querySelector('[data-testid="auto-planner-goal-plan"]');
            if(input){input.focus();input.value=goal;input.dispatchEvent(new Event('input',{bubbles:true}));}
            if(btn){btn.click(); return true;}
            return false;
            """,
            [goal_name],
        ).get("value")
    )
    goal_result = wait_for(
        session_id,
        "return !!document.querySelector('[data-testid=\"goal-submit-result\"]')",
        int(max(20, goal_wait_seconds)),
    )
    goal_result_details = js(
        session_id,
        """
        const panel = document.querySelector('[data-testid="goal-submit-result"]');
        if(!panel) return { found:false, text:'', tasks_created:0 };
        const text = (panel.textContent || '').trim();
        const m = text.match(/(\\d+)\\s+Tasks/);
        return { found:true, text:text.slice(0, 400), tasks_created: m ? Number(m[1]) : 0 };
        """,
    ).get("value") or {}
    tasks_created = int(goal_result_details.get("tasks_created") or 0)
    ok = goal_clicked and goal_result and tasks_created > 0
    print("goal_submit_clicked", goal_clicked, "goal_result_visible", goal_result, "tasks_created", tasks_created, "goal", goal_name, flush=True)
    record_step(
        report,
        "goal",
        "goal_plan_submit",
        t0,
        ok,
        {
            "goal_name": goal_name,
            "goal_clicked": goal_clicked,
            "goal_result_visible": goal_result,
            "tasks_created": tasks_created,
            "goal_result_excerpt": str(goal_result_details.get("text") or ""),
            **current_route_and_title(session_id),
        },
    )
    settle(step_delay_seconds)
    gate_visible_errors(session_id, report, "goal", hard_fail)
    if not ok:
        raise RuntimeError("Goal submit flow failed")


def phase_execution(session_id: str, report: dict, hard_fail: bool, step_delay_seconds: float, wait_tasks_seconds: float):
    routes = ["/dashboard", "/board", "/templates", "/teams", "/settings"]
    ok_all = True
    for route in routes:
        t0 = time.time()
        step_nav(session_id, route)
        state = current_route_and_title(session_id)
        ok = bool(state["title"])
        details = dict(state)
        if route == "/board":
            # Wait for task cards to appear so this run verifies UI task visibility, not just navigation.
            has_tasks = wait_for(
                session_id,
                """
                const items = document.querySelectorAll('.board-item').length;
                return items > 0;
                """,
                int(max(5, wait_tasks_seconds)),
            )
            task_count = int(
                js(
                    session_id,
                    "return document.querySelectorAll('.board-item').length;",
                ).get("value")
                or 0
            )
            details["board_task_count"] = task_count
            details["board_has_tasks"] = has_tasks
            ok = ok and has_tasks and task_count > 0
        ok_all = ok_all and ok
        print("route", route, "title", state["title"], flush=True)
        record_step(report, "execution", f"navigate:{route}", t0, ok, details)
        settle(step_delay_seconds)
        gate_visible_errors(session_id, report, "execution", hard_fail)
    if not ok_all:
        raise RuntimeError("One or more execution navigation steps failed")


def phase_review(session_id: str, report: dict, hard_fail: bool, step_delay_seconds: float):
    t0 = time.time()
    step_nav(session_id, "/artifacts", settle_s=1.2)
    state = current_route_and_title(session_id)
    errors = list_visible_errors(session_id)
    has_401 = any("401" in item["text"] for item in errors)
    report["ui_signals"]["visible_errors"].extend(errors)
    report["ui_signals"]["visible_errors_contains_401"] = report["ui_signals"]["visible_errors_contains_401"] or has_401
    report["ui_signals"]["final_route"] = state["path"]
    report["ui_signals"]["final_title"] = state["title"]
    ok = len(errors) == 0
    record_step(report, "review", "final_ui_review", t0, ok, {"visible_error_count": len(errors), "contains_401": has_401, **state})
    settle(step_delay_seconds)
    if errors:
        print("visible_errors", len(errors), "contains_401", has_401, flush=True)
        print("visible_error_texts", json.dumps(errors, ensure_ascii=True), flush=True)
        if hard_fail:
            raise RuntimeError("Final review detected visible UI errors")


def run_phase(
    session_id: str,
    phase: str,
    report: dict,
    hard_fail: bool,
    step_delay_seconds: float,
    wait_tasks_seconds: float,
    goal_wait_seconds: float,
    bootstrap_setup: bool,
):
    if phase == "setup":
        phase_setup(session_id, report, hard_fail, step_delay_seconds, bootstrap_setup)
    elif phase == "goal":
        phase_goal(session_id, report, hard_fail, step_delay_seconds, goal_wait_seconds)
    elif phase == "execution":
        phase_execution(session_id, report, hard_fail, step_delay_seconds, wait_tasks_seconds)
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
        help="Comma-separated list: setup,goal,execution,review or all",
    )
    p.add_argument(
        "--report-file",
        default="",
        help="Optional explicit JSON report path (default: test-reports/live-click/<timestamp>.json)",
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
        "bootstrap_setup": bootstrap_setup,
        "replay_from_report": replay_source,
        "status": "running",
        "steps": [],
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
        write_report(report, report_path)


if __name__ == "__main__":
    main()
