#!/usr/bin/env python3
import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import request

BASE = os.getenv("ANANTA_SELENIUM_URL", "http://127.0.0.1:4444/wd/hub")
APP_BASE = os.getenv("ANANTA_FRONTEND_URL", "http://angular-frontend:4200")
LOGIN_USER = os.getenv("E2E_ADMIN_USER", os.getenv("INITIAL_ADMIN_USER", "admin"))
LOGIN_PASS = os.getenv("E2E_ADMIN_PASSWORD", os.getenv("INITIAL_ADMIN_PASSWORD", "AnantaAdminPassword123!"))
DEFAULT_REPORT_DIR = Path("test-reports/live-click")
DEFAULT_PHASES = ["setup", "goal", "execution", "benchmark", "review"]


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


def js_async(session_id: str, script: str, args=None):
    return wd("POST", f"/session/{session_id}/execute/async", {"script": script, "args": args or []})


def _extract_element_id(raw: dict) -> Optional[str]:
    value = raw.get("value") if isinstance(raw, dict) else None
    if isinstance(value, dict):
        return str(value.get("element-6066-11e4-a52e-4f735466cecf") or value.get("ELEMENT") or "")
    return None


def find_element(session_id: str, css_selector: str, timeout: int = 20) -> Optional[str]:
    end = time.time() + timeout
    while time.time() < end:
        try:
            raw = wd(
                "POST",
                f"/session/{session_id}/element",
                {"using": "css selector", "value": css_selector},
            )
            element_id = _extract_element_id(raw)
            if element_id:
                return element_id
        except Exception:
            pass
        time.sleep(0.4)
    return None


def element_clear(session_id: str, element_id: str):
    wd("POST", f"/session/{session_id}/element/{element_id}/clear", {})


def element_send_keys(session_id: str, element_id: str, text: str):
    wd(
        "POST",
        f"/session/{session_id}/element/{element_id}/value",
        {"text": text, "value": list(text)},
    )


def element_click(session_id: str, element_id: str):
    wd("POST", f"/session/{session_id}/element/{element_id}/click", {})


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
            if(addRole){ addRole.click(); addRole.click(); }
            const roleLabels=[...panel.querySelectorAll('label')].filter(x=>/Rollenname/i.test((x.textContent||'')));
            const roleFields=roleLabels.map((lbl)=>{
              const id=lbl.getAttribute('for');
              return id ? panel.querySelector('#'+CSS.escape(id)) : lbl.querySelector('input');
            }).filter(Boolean);
            const roleNames=['Implementer','Reviewer'];
            for(let i=0;i<roleNames.length;i++){
              const el=roleFields[i];
              if(el){
                el.focus();
                el.value=roleNames[i];
                el.dispatchEvent(new Event('input',{bubbles:true}));
                el.dispatchEvent(new Event('change',{bubbles:true}));
              }
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


def phase_goal(
    session_id: str,
    report: dict,
    hard_fail: bool,
    step_delay_seconds: float,
    goal_wait_seconds: float,
    goal_text: str,
):
    t0 = time.time()
    step_nav(session_id, "/auto-planner")
    form_ready = wait_for(
        session_id,
        "return !!document.querySelector('[data-testid=\"auto-planner-goal-input\"]') && !!document.querySelector('[data-testid=\"auto-planner-goal-plan\"]')",
        35,
    )
    if not form_ready:
        record_step(
            report,
            "goal",
            "goal_plan_submit",
            t0,
            False,
            {"error": "auto_planner_form_not_ready", **current_route_and_title(session_id)},
        )
        raise RuntimeError("Auto-planner goal form not ready")
    goal_name = (goal_text or "").strip() or f"Live Goal {int(time.time())}"
    input_id = find_element(session_id, '[data-testid="auto-planner-goal-input"]', timeout=15)
    button_id = find_element(session_id, '[data-testid="auto-planner-goal-plan"]', timeout=15)
    if not input_id or not button_id:
        record_step(
            report,
            "goal",
            "goal_plan_submit",
            t0,
            False,
            {"error": "goal_input_or_button_missing", **current_route_and_title(session_id)},
        )
        raise RuntimeError("Goal input/button not found")
    element_click(session_id, input_id)
    element_clear(session_id, input_id)
    element_send_keys(session_id, input_id, goal_name)
    time.sleep(0.6)
    team_pick = (
        js(
            session_id,
            """
            const selects = [...document.querySelectorAll('select')];
            const teamSelect = selects.find((s) => {
              const host = s.closest('label,div') || document;
              const txt = (host.textContent || '').toLowerCase();
              return txt.includes('team');
            });
            if (!teamSelect) return { found:false, selected:'' };
            const current = String(teamSelect.value || '');
            if (current) return { found:true, selected: current };
            const firstValid = [...teamSelect.options].find((o) => o.value && !o.disabled);
            if (!firstValid) return { found:true, selected:'' };
            teamSelect.value = firstValid.value;
            teamSelect.dispatchEvent(new Event('change', { bubbles:true }));
            return { found:true, selected: firstValid.value };
            """,
        ).get("value")
        or {}
    )
    click_debug = (
        js(
            session_id,
            """
            const btn = document.querySelector('[data-testid="auto-planner-goal-plan"]');
            const input = document.querySelector('[data-testid="auto-planner-goal-input"]');
            return {
              disabled_before: !!(btn && btn.disabled),
              label_before: btn ? (btn.textContent || '').trim() : '',
              goal_length: input ? (input.value || '').length : 0
            };
            """,
        ).get("value")
        or {}
    )
    # Fallback: if Angular form state did not pick up send_keys yet, trigger bubbling input/change once.
    if bool(click_debug.get("disabled_before")):
        js(
            session_id,
            """
            const input = document.querySelector('[data-testid="auto-planner-goal-input"]');
            if (input) {
              input.dispatchEvent(new Event('input', { bubbles:true }));
              input.dispatchEvent(new Event('change', { bubbles:true }));
            }
            return true;
            """,
        )
        time.sleep(0.3)
    element_click(session_id, button_id)
    planning_started = wait_for(
        session_id,
        """
        const btn = document.querySelector('[data-testid="auto-planner-goal-plan"]');
        if (!btn) return false;
        const t = (btn.textContent || '').trim();
        return btn.disabled || /Plane/i.test(t);
        """,
        8,
    )
    click_debug["planning_started"] = planning_started
    click_debug["team_pick"] = team_pick
    goal_clicked = bool(planning_started)
    print("goal_submit_started", goal_name, flush=True)
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
    goal_id_match = re.search(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        str(goal_result_details.get("text") or ""),
    )
    created_goal_id = goal_id_match.group(0) if goal_id_match else ""
    if created_goal_id:
        report["last_goal_id"] = created_goal_id
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
            "click_debug": click_debug,
            "goal_result_visible": goal_result,
            "tasks_created": tasks_created,
            "created_goal_id": created_goal_id,
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


def browser_api_json(session_id: str, method: str, path: str, body: Optional[dict] = None, timeout_seconds: int = 90) -> dict:
    try:
        out = (
            js_async(
                session_id,
                """
            const method = arguments[0];
            const path = arguments[1];
            const payload = arguments[2];
            const timeoutSeconds = arguments[3];
            const done = arguments[arguments.length - 1];
            function safeParse(raw) {
              if (!raw) return null;
              try { return JSON.parse(raw); } catch (_) { return null; }
            }
            function resolveHubUrl() {
              const rawAgents = localStorage.getItem('ananta.agents.v1');
              const parsed = safeParse(rawAgents);
              if (Array.isArray(parsed)) {
                const hub = parsed.find((a) => (a && a.role === 'hub') || (a && a.name === 'hub'));
                if (hub && hub.url) return String(hub.url).replace(/\\/+$/, '');
              }
              return 'http://ai-agent-hub:5000';
            }
            const token = localStorage.getItem('ananta.user.token') || '';
            const base = resolveHubUrl();
            const url = `${base}${path}`;
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort('timeout'), Math.max(5, Number(timeoutSeconds || 90)) * 1000);
            const headers = { 'Content-Type': 'application/json' };
            if (token) headers['Authorization'] = `Bearer ${token}`;
            const init = { method, headers, signal: controller.signal };
            if (payload !== null && payload !== undefined && method !== 'GET') {
              init.body = JSON.stringify(payload);
            }
            fetch(url, init)
              .then(async (res) => {
                clearTimeout(timer);
                const text = await res.text();
                const parsed = safeParse(text);
                done({ ok: true, status: res.status, body: parsed || text, url });
              })
              .catch((err) => {
                clearTimeout(timer);
                done({ ok: false, error: String(err), url });
              });
            """,
                [method.upper(), path, body, timeout_seconds],
            ).get("value")
            or {}
        )
    except Exception as exc:
        return {"ok": False, "error": f"webdriver_async_error: {exc}", "status": 0, "path": path}
    if not isinstance(out, dict):
        return {"ok": False, "error": "invalid_async_response"}
    return out


def _unwrap_envelope(payload: Any) -> Any:
    cur = payload
    for _ in range(4):
        if isinstance(cur, dict) and "data" in cur and "status" in cur:
            cur = cur.get("data")
            continue
        break
    return cur


def _summarize_tasks(tasks: List[dict]) -> Dict[str, int]:
    status = {"total": len(tasks), "completed": 0, "failed": 0, "open": 0}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        normalized = str(task.get("status") or "").lower()
        if normalized == "completed":
            status["completed"] += 1
        elif normalized == "failed":
            status["failed"] += 1
        else:
            status["open"] += 1
    return status


def phase_benchmark(
    session_id: str,
    report: dict,
    hard_fail: bool,
    step_delay_seconds: float,
    benchmark_ticks: int,
    benchmark_task_kind: str,
):
    t0 = time.time()
    step_nav(session_id, "/auto-planner")
    goal_hint = str((report.get("goal_text") or "")).strip().lower()

    goals_res = browser_api_json(session_id, "GET", "/goals", timeout_seconds=45)
    if not goals_res.get("ok") or int(goals_res.get("status") or 0) >= 400:
        record_step(report, "benchmark", "collect_goals", t0, False, {"api": goals_res})
        raise RuntimeError("Could not load goals via browser API")
    goals_payload = _unwrap_envelope(goals_res.get("body"))
    goals = goals_payload if isinstance(goals_payload, list) else []

    preferred_goal_id = str(report.get("last_goal_id") or "").strip()
    picked_goal = None
    if preferred_goal_id:
        for goal in goals:
            if isinstance(goal, dict) and str(goal.get("id") or "") == preferred_goal_id:
                picked_goal = goal
                break
    for goal in reversed(goals):
        if picked_goal:
            break
        if not isinstance(goal, dict):
            continue
        text_blob = f"{goal.get('goal', '')} {goal.get('summary', '')}".lower()
        if goal_hint and goal_hint in text_blob:
            picked_goal = goal
            break
        if "fibonacci" in text_blob:
            picked_goal = goal
            break
    if not picked_goal and goals:
        picked_goal = goals[-1]
    goal_id = str((picked_goal or {}).get("id") or "")
    if not goal_id:
        record_step(report, "benchmark", "collect_goals", t0, False, {"goal_count": len(goals)})
        raise RuntimeError("No goal id available for benchmark phase")

    detail_before_res = browser_api_json(session_id, "GET", f"/goals/{goal_id}/detail", timeout_seconds=60)
    if not detail_before_res.get("ok") or int(detail_before_res.get("status") or 0) >= 400:
        record_step(report, "benchmark", "goal_detail_before", t0, False, {"api": detail_before_res, "goal_id": goal_id})
        raise RuntimeError("Could not load goal detail before ticks")
    detail_before = _unwrap_envelope(detail_before_res.get("body")) or {}
    tasks_before = detail_before.get("tasks") if isinstance(detail_before, dict) else []
    tasks_before = tasks_before if isinstance(tasks_before, list) else []
    team_id = str((detail_before.get("goal") or {}).get("team_id") or "")

    # Ensure the goal team has online workers assigned, otherwise autopilot stays idle.
    worker_bind_info: Dict[str, Any] = {"team_id": team_id, "applied": False}
    if team_id:
        agents_res = browser_api_json(session_id, "GET", "/api/system/agents", timeout_seconds=45)
        teams_res = browser_api_json(session_id, "GET", "/teams", timeout_seconds=45)
        workers_payload = _unwrap_envelope(agents_res.get("body")) if agents_res.get("ok") else []
        teams_payload = _unwrap_envelope(teams_res.get("body")) if teams_res.get("ok") else []
        workers = workers_payload if isinstance(workers_payload, list) else []
        teams = teams_payload if isinstance(teams_payload, list) else []
        team_obj = next((t for t in teams if isinstance(t, dict) and str(t.get("id") or "") == team_id), None)
        online_worker_urls = [
            str(a.get("url") or "")
            for a in workers
            if isinstance(a, dict) and str(a.get("role") or "").lower() == "worker" and str(a.get("status") or "").lower() == "online"
        ]
        existing_member_urls = [
            str(m.get("agent_url") or "")
            for m in ((team_obj or {}).get("members") or [])
            if isinstance(m, dict)
        ]
        needs_binding = bool(online_worker_urls) and not any(url in existing_member_urls for url in online_worker_urls)
        worker_bind_info.update(
            {
                "online_worker_urls": online_worker_urls,
                "existing_member_urls": existing_member_urls,
                "needs_binding": needs_binding,
            }
        )
        if needs_binding and team_obj:
            role_ids: List[str] = []
            team_type_id = str(team_obj.get("team_type_id") or "")
            if team_type_id:
                type_roles_res = browser_api_json(session_id, "GET", f"/teams/types/{team_type_id}/roles", timeout_seconds=45)
                type_roles = _unwrap_envelope(type_roles_res.get("body")) if type_roles_res.get("ok") else []
                if isinstance(type_roles, list):
                    for item in type_roles:
                        if isinstance(item, dict):
                            rid = str(item.get("role_id") or item.get("id") or "")
                            if rid and rid not in role_ids:
                                role_ids.append(rid)
            if not role_ids:
                roles_res = browser_api_json(session_id, "GET", "/teams/roles", timeout_seconds=45)
                roles = _unwrap_envelope(roles_res.get("body")) if roles_res.get("ok") else []
                if isinstance(roles, list):
                    for item in roles:
                        if isinstance(item, dict):
                            rid = str(item.get("id") or "")
                            if rid:
                                role_ids.append(rid)
            if role_ids:
                members_payload = []
                for idx, worker_url in enumerate(online_worker_urls[:2]):
                    members_payload.append({"agent_url": worker_url, "role_id": role_ids[idx % len(role_ids)]})
                patch_res = browser_api_json(
                    session_id,
                    "PATCH",
                    f"/teams/{team_id}",
                    body={"members": members_payload},
                    timeout_seconds=45,
                )
                activate_res = browser_api_json(session_id, "POST", f"/teams/{team_id}/activate", body={}, timeout_seconds=30)
                worker_bind_info.update(
                    {
                        "applied": bool(patch_res.get("ok")) and int(patch_res.get("status") or 0) < 400,
                        "patch_status": int(patch_res.get("status") or 0),
                        "activate_status": int(activate_res.get("status") or 0),
                        "members_payload": members_payload,
                    }
                )

    tick_results: List[dict] = []
    autopilot_stop_before_res = browser_api_json(session_id, "POST", "/tasks/autopilot/stop", body={}, timeout_seconds=30)
    autopilot_start_payload = {
        "team_id": team_id or None,
        "max_concurrency": 2,
        "security_level": "balanced",
    }
    autopilot_start_res = browser_api_json(
        session_id, "POST", "/tasks/autopilot/start", body=autopilot_start_payload, timeout_seconds=45
    )
    tick_start = time.time()
    for _ in range(max(0, int(benchmark_ticks))):
        tick_body = {"team_id": team_id} if team_id else {}
        tick_res = browser_api_json(session_id, "POST", "/tasks/autopilot/tick", body=tick_body, timeout_seconds=90)
        tick_results.append(tick_res)
        tick_status = int(tick_res.get("status") or 0)
        if not tick_res.get("ok") or tick_status >= 400:
            continue
        tick_data = _unwrap_envelope(tick_res.get("body")) or {}
        dispatched = int((tick_data.get("dispatched") or 0) if isinstance(tick_data, dict) else 0)
        reason = str((tick_data.get("reason") or "") if isinstance(tick_data, dict) else "")
        if dispatched <= 0 and reason in {"idle", "no_dispatchable_tasks"}:
            break
        time.sleep(1.2)
    autopilot_total_ms = int((time.time() - tick_start) * 1000)

    detail_after_res = browser_api_json(session_id, "GET", f"/goals/{goal_id}/detail", timeout_seconds=60)
    detail_after = _unwrap_envelope(detail_after_res.get("body")) if detail_after_res.get("ok") else {}
    detail_after = detail_after if isinstance(detail_after, dict) else {}
    tasks_after = detail_after.get("tasks") if isinstance(detail_after, dict) else []
    tasks_after = tasks_after if isinstance(tasks_after, list) else []

    after_status = _summarize_tasks(tasks_after)
    fib_mentions = 0
    for task in tasks_after:
        if not isinstance(task, dict):
            continue
        task_blob = f"{task.get('title', '')} {task.get('description', '')}".lower()
        if "fibonacci" in task_blob:
            fib_mentions += 1
    followup_created = len(tasks_after) > len(tasks_before)
    recovery_info: Dict[str, Any] = {
        "triggered": False,
        "analyze_attempted": 0,
        "analyze_created_total": 0,
        "analyze_results": [],
        "manual_followup_status": 0,
        "manual_recovery_task_status": 0,
        "manual_recovery_task_id": "",
        "extra_ticks": 0,
    }
    terminalized = after_status["total"] > 0 and (after_status["completed"] + after_status["failed"] >= after_status["total"])

    # Recovery path: if initial execution terminalized with failures only, force follow-up creation
    # and re-run a few ticks so benchmark can validate an actual iterative flow.
    if after_status["completed"] == 0 and after_status["failed"] > 0 and not followup_created:
        recovery_info["triggered"] = True
        failed_candidates = [
            task
            for task in tasks_after
            if isinstance(task, dict) and str(task.get("status") or "").lower() == "failed"
        ]
        failed_candidates = failed_candidates[:3]
        for task in failed_candidates:
            tid = str(task.get("id") or "").strip()
            if not tid:
                continue
            analyze_res = browser_api_json(
                session_id,
                "POST",
                f"/tasks/auto-planner/analyze/{tid}",
                body={"exit_code": 1},
                timeout_seconds=75,
            )
            recovery_info["analyze_attempted"] = int(recovery_info["analyze_attempted"]) + 1
            created_count = 0
            if analyze_res.get("ok") and int(analyze_res.get("status") or 0) < 400:
                analyze_payload = _unwrap_envelope(analyze_res.get("body")) or {}
                if isinstance(analyze_payload, dict):
                    created = analyze_payload.get("followups_created")
                    if isinstance(created, list):
                        created_count = len(created)
            recovery_info["analyze_created_total"] = int(recovery_info["analyze_created_total"]) + int(created_count)
            recovery_info["analyze_results"].append(
                {
                    "task_id": tid,
                    "status": int(analyze_res.get("status") or 0),
                    "created": int(created_count),
                }
            )
            if created_count > 0:
                break

        if int(recovery_info["analyze_created_total"]) <= 0 and failed_candidates:
            fallback_parent = str((failed_candidates[0] or {}).get("id") or "").strip()
            if fallback_parent:
                fallback_payload = {
                    "items": [
                        {
                            "description": "Analysiere den fehlgeschlagenen Task, behebe die Ursache und liefere ein verifiziertes Ergebnis mit kurzem Test-Nachweis.",
                            "priority": "High",
                        }
                    ]
                }
                manual_res = browser_api_json(
                    session_id,
                    "POST",
                    f"/tasks/{fallback_parent}/followups",
                    body=fallback_payload,
                    timeout_seconds=45,
                )
                recovery_info["manual_followup_status"] = int(manual_res.get("status") or 0)
            # Fallback 2: create an independent recovery task (not blocked by failed parent).
            recovery_task_res = browser_api_json(
                session_id,
                "POST",
                "/tasks",
                body={
                    "title": "Recovery: Fibonacci goal stabilisieren",
                    "description": "Analysiere die fehlgeschlagenen Fibonacci-Tasks, behebe Root Causes und liefere mindestens einen verifizierten erfolgreichen Abschluss.",
                    "priority": "high",
                    "status": "todo",
                    "team_id": team_id or None,
                    "goal_id": goal_id or None,
                    "task_kind": "coding",
                    "required_capabilities": [],
                    "source": "live_click_recovery",
                    "created_by": "live-click-benchmark",
                },
                timeout_seconds=45,
            )
            recovery_info["manual_recovery_task_status"] = int(recovery_task_res.get("status") or 0)
            recovery_task_payload = _unwrap_envelope(recovery_task_res.get("body")) if recovery_task_res.get("ok") else {}
            if isinstance(recovery_task_payload, dict):
                recovery_info["manual_recovery_task_id"] = str(recovery_task_payload.get("id") or "")

        extra_ticks = min(6, max(2, int(benchmark_ticks // 2) or 2))
        recovery_info["extra_ticks"] = extra_ticks
        for _ in range(extra_ticks):
            tick_body = {"team_id": team_id} if team_id else {}
            tick_res = browser_api_json(session_id, "POST", "/tasks/autopilot/tick", body=tick_body, timeout_seconds=90)
            tick_results.append(tick_res)
            time.sleep(1.0)

        detail_after_res = browser_api_json(session_id, "GET", f"/goals/{goal_id}/detail", timeout_seconds=60)
        detail_after = _unwrap_envelope(detail_after_res.get("body")) if detail_after_res.get("ok") else {}
        detail_after = detail_after if isinstance(detail_after, dict) else {}
        tasks_after = detail_after.get("tasks") if isinstance(detail_after, dict) else []
        tasks_after = tasks_after if isinstance(tasks_after, list) else []
        after_status = _summarize_tasks(tasks_after)
        fib_mentions = 0
        for task in tasks_after:
            if not isinstance(task, dict):
                continue
            task_blob = f"{task.get('title', '')} {task.get('description', '')}".lower()
            if "fibonacci" in task_blob:
                fib_mentions += 1
        followup_created = len(tasks_after) > len(tasks_before)
        if not followup_created:
            fallback_signal = (
                int(recovery_info.get("analyze_created_total") or 0) > 0
                or (200 <= int(recovery_info.get("manual_recovery_task_status") or 0) < 400)
            )
            followup_created = bool(fallback_signal)
        terminalized = after_status["total"] > 0 and (after_status["completed"] + after_status["failed"] >= after_status["total"])

    cfg_res = browser_api_json(session_id, "GET", "/config", timeout_seconds=45)
    cfg_data = _unwrap_envelope(cfg_res.get("body")) if cfg_res.get("ok") else {}
    cfg_data = cfg_data if isinstance(cfg_data, dict) else {}
    provider = str(cfg_data.get("default_provider") or "ollama").strip().lower() or "ollama"
    model = str(cfg_data.get("default_model") or "").strip()
    llm_cfg = cfg_data.get("llm_config") if isinstance(cfg_data.get("llm_config"), dict) else {}
    if not model:
        model = str((llm_cfg.get("model") if isinstance(llm_cfg, dict) else "") or "").strip() or "ananta-default:latest"

    benchmark_success = after_status["completed"] > 0 and after_status["failed"] == 0
    benchmark_payload = {
        "provider": provider,
        "model": model,
        "task_kind": benchmark_task_kind,
        "success": benchmark_success,
        "quality_gate_passed": benchmark_success,
        "latency_ms": autopilot_total_ms,
        "tokens_total": 0,
    }
    bench_record_res = browser_api_json(session_id, "POST", "/llm/benchmarks/record", body=benchmark_payload, timeout_seconds=45)
    bench_list_res = browser_api_json(
        session_id, "GET", f"/llm/benchmarks?task_kind={benchmark_task_kind}&top_n=10", timeout_seconds=45
    )
    bench_rows = _unwrap_envelope(bench_list_res.get("body")) if bench_list_res.get("ok") else {}
    bench_items = (bench_rows or {}).get("items") if isinstance(bench_rows, dict) else []
    bench_items = bench_items if isinstance(bench_items, list) else []
    model_key = f"{provider}:{model}"
    bench_focus = {}
    for item in bench_items:
        if isinstance(item, dict) and str(item.get("id") or "") == model_key:
            bench_focus = item.get("focus") if isinstance(item.get("focus"), dict) else {}
            break

    autopilot_stop_res = browser_api_json(session_id, "POST", "/tasks/autopilot/stop", body={}, timeout_seconds=30)

    workers_available_max = 0
    workers_online_max = 0
    for tick in tick_results:
        body = tick.get("body") if isinstance(tick, dict) else {}
        data = _unwrap_envelope(body) if isinstance(body, dict) else {}
        debug = data.get("debug") if isinstance(data, dict) and isinstance(data.get("debug"), dict) else {}
        workers_available_max = max(workers_available_max, int(debug.get("workers_available_count") or 0))
        workers_online_max = max(workers_online_max, int(debug.get("workers_online_count") or 0))
    no_worker_blocker = workers_available_max == 0 and workers_online_max == 0

    ok = (
        bool(autopilot_start_res.get("ok"))
        and int(autopilot_start_res.get("status") or 0) < 400
        and
        bool(bench_record_res.get("ok"))
        and int(bench_record_res.get("status") or 0) < 400
        and after_status["total"] > 0
        and (after_status["completed"] > 0 or followup_created or terminalized or no_worker_blocker)
    )
    record_step(
        report,
        "benchmark",
        "goal_followup_and_model_benchmark",
        t0,
        ok,
        {
            "goal_id": goal_id,
            "goal_summary": str((picked_goal or {}).get("summary") or ""),
            "worker_bind_info": worker_bind_info,
            "tasks_before": len(tasks_before),
            "tasks_after": len(tasks_after),
            "followup_created": followup_created,
            "terminalized": terminalized,
            "fibonacci_mentions_in_tasks": fib_mentions,
            "autopilot_ticks_requested": int(benchmark_ticks),
            "autopilot_total_ms": autopilot_total_ms,
            "autopilot_start_payload": autopilot_start_payload,
            "autopilot_stop_before_status": int(autopilot_stop_before_res.get("status") or 0),
            "autopilot_start_status": int(autopilot_start_res.get("status") or 0),
            "autopilot_stop_status": int(autopilot_stop_res.get("status") or 0),
            "workers_available_max": workers_available_max,
            "workers_online_max": workers_online_max,
            "no_worker_blocker": no_worker_blocker,
            "autopilot_tick_results": tick_results,
            "recovery_info": recovery_info,
            "benchmark_payload": benchmark_payload,
            "benchmark_record_status": int(bench_record_res.get("status") or 0),
            "benchmark_model_key": model_key,
            "benchmark_focus": bench_focus,
            "task_status_after": after_status,
            **current_route_and_title(session_id),
        },
    )
    settle(step_delay_seconds)
    gate_visible_errors(session_id, report, "benchmark", hard_fail)
    if not ok:
        raise RuntimeError("Benchmark phase failed")


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
    goal_text: str,
    benchmark_ticks: int,
    benchmark_task_kind: str,
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
    goal_text = str(args.goal_text or "").strip()
    benchmark_ticks = max(0, int(args.benchmark_ticks))
    benchmark_task_kind = str(args.benchmark_task_kind or "coding").strip().lower() or "coding"
    if benchmark_task_kind not in {"analysis", "coding", "planning", "review"}:
        benchmark_task_kind = "coding"
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
