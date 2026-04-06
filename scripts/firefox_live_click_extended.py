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
from urllib.parse import quote

from test_env_cleanup import cleanup_test_environment

BASE = os.getenv("ANANTA_SELENIUM_URL", "http://127.0.0.1:4444/wd/hub")
APP_BASE = os.getenv("ANANTA_FRONTEND_URL", "http://angular-frontend:4200")
HUB_BASE = os.getenv("HUB_BASE_URL", "http://127.0.0.1:5000")
HUB_CONTAINER = os.getenv("ANANTA_HUB_CONTAINER", "ananta-ai-agent-hub-1")
DEFAULT_REPORT_DIR = Path("test-reports/live-click")
DEFAULT_PHASES = ["setup", "goal", "execution", "benchmark", "review"]
FILE_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9_./-])(?:[A-Za-z0-9_.-]+/){1,}[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,10}")


def _read_repo_dotenv() -> Dict[str, str]:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return {}
    values: Dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


_DOTENV = _read_repo_dotenv()
LOGIN_USER = os.getenv("E2E_ADMIN_USER") or os.getenv("INITIAL_ADMIN_USER") or _DOTENV.get("INITIAL_ADMIN_USER") or "admin"
LOGIN_PASS = (
    os.getenv("E2E_ADMIN_PASSWORD")
    or os.getenv("INITIAL_ADMIN_PASSWORD")
    or _DOTENV.get("INITIAL_ADMIN_PASSWORD")
    or "AnantaAdminPassword123!"
)


def wd(method: str, path: str, payload=None, timeout: int = 45):
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = request.Request(BASE + path, data=data, headers=headers, method=method)
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def js(session_id: str, script: str, args=None):
    return wd("POST", f"/session/{session_id}/execute/sync", {"script": script, "args": args or []})


def js_async(session_id: str, script: str, args=None, timeout: int = 45):
    return wd("POST", f"/session/{session_id}/execute/async", {"script": script, "args": args or []}, timeout=timeout)


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


def set_input_value_via_js(session_id: str, selector: str, text: str) -> bool:
    result = js(
        session_id,
        """
        const selector = arguments[0];
        const text = arguments[1];
        const input = document.querySelector(selector);
        if (!input) return false;
        input.focus();
        input.value = text;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
        """,
        [selector, text],
    )
    return bool(result.get("value"))


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


def default_goal_text_for_benchmark(task_kind: str) -> str:
    normalized = str(task_kind or "").strip().lower()
    if normalized == "analysis":
        return "Analyze a small Python Fibonacci helper, identify two concrete improvements, and provide a short implementation plan."
    if normalized == "planning":
        return "Create a concrete implementation plan for a Python Fibonacci helper with tests, validation steps, and a short delivery summary."
    if normalized == "review":
        return "Review a small Python Fibonacci helper change, identify issues, propose fixes, and summarize verification steps."
    return (
        "Implement a small Python Fibonacci helper, add unit tests, and provide a short summary of the changed files and validation."
    )


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
    t_mode = time.time()
    mode_res = ensure_opencode_execution_mode(session_id, mode="interactive_terminal", timeout_seconds=60)
    mode_ok = bool(mode_res.get("ok"))
    print("opencode_execution_mode", json.dumps(mode_res, ensure_ascii=True), flush=True)
    record_step(report, "setup", "set_opencode_execution_mode", t_mode, mode_ok, mode_res)
    if not mode_ok:
        raise RuntimeError("Failed to switch opencode execution mode to interactive_terminal")
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
    report.setdefault("cleanup_targets", {}).setdefault("template_names", []).append(template_name)
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
    report.setdefault("cleanup_targets", {}).setdefault("blueprint_names", []).append(blueprint_name)
    report.setdefault("cleanup_targets", {}).setdefault("team_names", []).append(team_name)
    teams_res = browser_api_json(session_id, "GET", "/teams", timeout_seconds=45)
    teams_payload = _unwrap_envelope(teams_res.get("body")) if teams_res.get("ok") else []
    teams = teams_payload if isinstance(teams_payload, list) else []
    matched_team = next(
        (
            item
            for item in teams
            if isinstance(item, dict) and str(item.get("name") or "").strip() == team_name
        ),
        None,
    )
    resolved_team_id = str((matched_team or {}).get("id") or "").strip()
    if not resolved_team_id:
        fallback_team_res = browser_api_json(
            session_id,
            "POST",
            "/teams/setup-scrum",
            body={"name": team_name},
            timeout_seconds=45,
        )
        fallback_payload = _unwrap_envelope(fallback_team_res.get("body")) if fallback_team_res.get("ok") else {}
        fallback_payload = fallback_payload if isinstance(fallback_payload, dict) else {}
        matched_team = fallback_payload.get("team") if isinstance(fallback_payload.get("team"), dict) else {}
        resolved_team_id = str((matched_team or {}).get("id") or "").strip()
    if resolved_team_id:
        report.setdefault("cleanup_targets", {}).setdefault("team_ids", []).append(resolved_team_id)
        if not bool((matched_team or {}).get("is_active")):
            browser_api_json(session_id, "POST", f"/teams/{resolved_team_id}/activate", body={}, timeout_seconds=30)
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
    goals_before_res = browser_api_json(session_id, "GET", "/goals", timeout_seconds=45)
    goals_before_payload = _unwrap_envelope(goals_before_res.get("body")) if goals_before_res.get("ok") else []
    goals_before = goals_before_payload if isinstance(goals_before_payload, list) else []
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
    try:
        element_click(session_id, input_id)
        element_clear(session_id, input_id)
        element_send_keys(session_id, input_id, goal_name)
    except Exception:
        if not set_input_value_via_js(session_id, '[data-testid="auto-planner-goal-input"]', goal_name):
            raise
    time.sleep(0.6)
    expected_team_id = ""
    expected_team_name = ""
    cleanup_targets = report.get("cleanup_targets") if isinstance(report.get("cleanup_targets"), dict) else {}
    if isinstance(cleanup_targets, dict):
        team_ids = cleanup_targets.get("team_ids")
        if isinstance(team_ids, list) and team_ids:
            expected_team_id = str(team_ids[-1] or "").strip()
        team_names = cleanup_targets.get("team_names")
        if isinstance(team_names, list) and team_names:
            expected_team_name = str(team_names[-1] or "").strip()
    wait_for(
        session_id,
        """
        const selects = [...document.querySelectorAll('select')];
        const teamSelect = selects.find((s) => {
          const host = s.closest('label,div') || document;
          const txt = (host.textContent || '').toLowerCase();
          return txt.includes('team');
        });
        if (!teamSelect) return false;
        const validOptions = [...teamSelect.options].filter((o) => o.value && !o.disabled);
        return validOptions.length > 0;
        """,
        20,
    )
    team_pick = (
        js(
            session_id,
            """
            const expectedId = String(arguments[0] || '').trim();
            const expectedName = String(arguments[1] || '').trim().toLowerCase();
            const selects = [...document.querySelectorAll('select')];
            const teamSelect = selects.find((s) => {
              const host = s.closest('label,div') || document;
              const txt = (host.textContent || '').toLowerCase();
              return txt.includes('team');
            });
            if (!teamSelect) return { found:false, selected:'' };
            const current = String(teamSelect.value || '');
            const validOptions = [...teamSelect.options].filter((o) => o.value && !o.disabled);
            const currentOption = current ? validOptions.find((o) => String(o.value || '') === current) : null;
            const matchedById = expectedId
              ? validOptions.find((o) => String(o.value || '').trim() === expectedId)
              : null;
            const matched = expectedName
              ? validOptions.find((o) => String(o.textContent || '').trim().toLowerCase() === expectedName)
              : null;
            const preferred = matchedById || matched || null;
            if (current && (!preferred || preferred.value === current)) {
              return {
                found:true,
                selected: current,
                selectedLabel: currentOption ? String(currentOption.textContent || '').trim() : '',
              };
            }
            const firstValid = preferred || validOptions[0];
            if (!firstValid) return { found:true, selected:'' };
            teamSelect.value = firstValid.value;
            teamSelect.dispatchEvent(new Event('input', { bubbles:true }));
            teamSelect.dispatchEvent(new Event('change', { bubbles:true }));
            return { found:true, selected: firstValid.value, selectedLabel: String(firstValid.textContent || '').trim() };
            """,
            [expected_team_id, expected_team_name],
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
        return t.includes('Plane...') || !!document.querySelector('[data-testid="goal-submit-result"]');
        """,
        8,
    )
    click_debug["goals_before"] = len(goals_before)
    click_debug["goals_before_ids"] = [
        str(item.get("id") or "")
        for item in goals_before[-5:]
        if isinstance(item, dict) and str(item.get("id") or "")
    ]
    click_debug["fallback_click"] = False
    if not planning_started:
        click_debug["fallback_click"] = bool(
            js(
                session_id,
                """
                const btn = document.querySelector('[data-testid="auto-planner-goal-plan"]');
                if (!btn) return false;
                btn.click();
                return true;
                """,
            ).get("value")
        )
        planning_started = wait_for(
            session_id,
            """
            const btn = document.querySelector('[data-testid="auto-planner-goal-plan"]');
            if (!btn) return false;
            const t = (btn.textContent || '').trim();
            return t.includes('Plane...') || !!document.querySelector('[data-testid="goal-submit-result"]');
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
    goals_after_res = browser_api_json(session_id, "GET", "/goals", timeout_seconds=45)
    goals_after_payload = _unwrap_envelope(goals_after_res.get("body")) if goals_after_res.get("ok") else []
    goals_after = goals_after_payload if isinstance(goals_after_payload, list) else []
    if not created_goal_id:
        for goal in reversed(goals_after):
            if not isinstance(goal, dict):
                continue
            text_blob = f"{goal.get('goal', '')} {goal.get('summary', '')}".strip().lower()
            if goal_name.lower() and goal_name.lower() in text_blob:
                created_goal_id = str(goal.get("id") or "")
                break
    detail_after = {}
    if created_goal_id and tasks_created <= 0:
        detail_res = browser_api_json(session_id, "GET", f"/goals/{created_goal_id}/detail", timeout_seconds=60)
        detail_payload = _unwrap_envelope(detail_res.get("body")) if detail_res.get("ok") else {}
        detail_after = detail_payload if isinstance(detail_payload, dict) else {}
        detail_tasks = detail_after.get("tasks") if isinstance(detail_after, dict) else []
        detail_tasks = detail_tasks if isinstance(detail_tasks, list) else []
        tasks_created = max(tasks_created, len(detail_tasks))
    if created_goal_id:
        report["last_goal_id"] = created_goal_id
        report.setdefault("cleanup_targets", {}).setdefault("goal_ids", []).append(created_goal_id)
    click_debug["goals_after"] = len(goals_after)
    click_debug["goals_after_ids"] = [
        str(item.get("id") or "")
        for item in goals_after[-5:]
        if isinstance(item, dict) and str(item.get("id") or "")
    ]
    ok = goal_clicked and (goal_result or bool(created_goal_id)) and tasks_created > 0
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
            "detail_tasks_after_fallback": len((detail_after.get("tasks") or [])) if isinstance(detail_after, dict) else 0,
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
        start_res = (
            js(
                session_id,
                """
            const method = arguments[0];
            const path = arguments[1];
            const payload = arguments[2];
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
            const requestId = `req-${Date.now()}-${Math.random().toString(36).slice(2)}`;
            const headers = { 'Content-Type': 'application/json' };
            if (token) headers['Authorization'] = `Bearer ${token}`;
            const controller = new AbortController();
            const timeoutMs = Math.max(5000, Number(arguments[3] || 90) * 1000);
            window.__anantaApiRequests = window.__anantaApiRequests || {};
            window.__anantaApiRequests[requestId] = { state: 'pending', ok: false, status: 0, url, startedAt: Date.now() };
            const timer = setTimeout(() => controller.abort('timeout'), timeoutMs);
            fetch(url, {
              method,
              headers,
              body: payload !== null && payload !== undefined && method !== 'GET' ? JSON.stringify(payload) : undefined,
              signal: controller.signal,
            })
              .then(async (res) => {
                const text = await res.text();
                const parsed = safeParse(text);
                clearTimeout(timer);
                window.__anantaApiRequests[requestId] = {
                  state: 'done',
                  ok: res.status >= 200 && res.status < 400,
                  status: res.status || 0,
                  body: parsed || text,
                  url,
                };
              })
              .catch((err) => {
                clearTimeout(timer);
                window.__anantaApiRequests[requestId] = {
                  state: 'error',
                  ok: false,
                  status: 0,
                  error: String(err),
                  url,
                };
              });
            return { requestId, state: 'pending', url };
            """,
                [method.upper(), path, body, timeout_seconds],
            ).get("value")
            or {}
        )
    except Exception as exc:
        return {"ok": False, "error": f"webdriver_sync_error: {exc}", "status": 0, "path": path}
    if not isinstance(start_res, dict):
        return {"ok": False, "error": "invalid_request_start", "status": 0, "path": path}
    request_id = str(start_res.get("requestId") or "").strip()
    if not request_id:
        return {"ok": False, "error": "missing_request_id", "status": 0, "path": path}
    deadline = time.time() + max(5, int(timeout_seconds))
    last_state: dict = {"ok": False, "error": "request_pending", "status": 0, "path": path}
    while time.time() < deadline:
        try:
            poll_res = (
                js(
                    session_id,
                    """
                const requestId = arguments[0];
                const store = window.__anantaApiRequests || {};
                return store[requestId] || null;
                """,
                    [request_id],
                ).get("value")
                or {}
            )
        except Exception as exc:
            return {"ok": False, "error": f"webdriver_poll_error: {exc}", "status": 0, "path": path}
        if isinstance(poll_res, dict):
            last_state = poll_res
            if str(poll_res.get("state") or "") in {"done", "error"}:
                return poll_res
        time.sleep(0.5)
    return {
        "ok": False,
        "error": f"browser_api_timeout: {last_state.get('error') or last_state.get('state') or 'pending'}",
        "status": int(last_state.get("status") or 0),
        "path": path,
        "url": last_state.get("url"),
    }


def ensure_opencode_execution_mode(session_id: str, mode: str = "interactive_terminal", timeout_seconds: int = 90) -> dict:
    try:
        out = (
            js_async(
                session_id,
                """
            const desiredMode = arguments[0];
            const timeoutSeconds = arguments[1];
            const password = arguments[2] || '';
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
            async function fetchJson(url, init) {
              const res = await fetch(url, init);
              const text = await res.text();
              return { status: res.status, body: safeParse(text) || text };
            }
            async function configureAgent(baseUrl, username, passwordValue) {
              const login = await fetchJson(`${baseUrl}/login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password: passwordValue }),
              });
              const token = (((login.body || {}).data || {}).access_token) || '';
              if (!token) {
                return { base_url: baseUrl, ok: false, status: login.status, error: 'missing_access_token' };
              }
              const headers = { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` };
              const post = await fetchJson(`${baseUrl}/config`, {
                method: 'POST',
                headers,
                body: JSON.stringify({ opencode_runtime: { execution_mode: desiredMode } }),
              });
              const get = await fetchJson(`${baseUrl}/config`, {
                method: 'GET',
                headers,
              });
              const effectiveMode = ((((get.body || {}).data || {}).opencode_runtime || {}).execution_mode) || '';
              return {
                base_url: baseUrl,
                ok: post.status >= 200 && post.status < 400 && effectiveMode === desiredMode,
                post_status: post.status,
                get_status: get.status,
                execution_mode: effectiveMode,
              };
            }
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort('timeout'), Math.max(5, Number(timeoutSeconds || 90)) * 1000);
            const hubBase = resolveHubUrl();
            const hubToken = localStorage.getItem('ananta.user.token') || '';
            const username = localStorage.getItem('ananta.user.username') || 'admin';
            const hubHeaders = { 'Content-Type': 'application/json' };
            if (hubToken) hubHeaders['Authorization'] = `Bearer ${hubToken}`;
            fetch(`${hubBase}/api/system/agents`, { method: 'GET', headers: hubHeaders, signal: controller.signal })
              .then(async (res) => {
                const text = await res.text();
                const parsed = safeParse(text);
                const payload = parsed && parsed.data ? parsed.data : parsed;
                const agents = Array.isArray(payload) ? payload : [];
                const baseUrls = [hubBase];
                for (const agent of agents) {
                  const agentUrl = String((agent && agent.url) || '').replace(/\\/+$/, '');
                  if (agentUrl && !baseUrls.includes(agentUrl)) baseUrls.push(agentUrl);
                }
                const results = [];
                for (const baseUrl of baseUrls) {
                  try {
                    results.push(await configureAgent(baseUrl, username, password));
                  } catch (err) {
                    results.push({ base_url: baseUrl, ok: false, error: String(err) });
                  }
                }
                clearTimeout(timer);
                done({
                  ok: results.every((item) => !!item.ok),
                  desired_mode: desiredMode,
                  base_urls: baseUrls,
                  results,
                });
              })
              .catch((err) => {
                clearTimeout(timer);
                done({ ok: false, error: String(err), desired_mode: desiredMode });
              });
            """,
                [mode, timeout_seconds, LOGIN_PASS],
                timeout=max(45, int(timeout_seconds) + 30),
            ).get("value")
            or {}
        )
    except Exception as exc:
        return {"ok": False, "error": f"webdriver_async_error: {exc}", "desired_mode": mode}
    if not isinstance(out, dict):
        return {"ok": False, "error": "invalid_mode_response", "desired_mode": mode}
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


def _collect_goal_tasks_snapshot(
    session_id: str,
    *,
    goal_id: str,
    goal_trace_id: str,
    timeout_seconds: int = 60,
) -> List[Dict[str, Any]]:
    res = browser_api_json(session_id, "GET", "/tasks?limit=2000", timeout_seconds=timeout_seconds)
    if not res.get("ok") or int(res.get("status") or 0) >= 400:
        return []
    payload = _unwrap_envelope(res.get("body"))
    tasks = payload if isinstance(payload, list) else []
    filtered: List[Dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        t_goal_id = str(task.get("goal_id") or "").strip()
        t_trace = str(task.get("goal_trace_id") or "").strip()
        if goal_id and t_goal_id == goal_id:
            filtered.append(task)
            continue
        if goal_trace_id and t_trace and t_trace == goal_trace_id:
            filtered.append(task)
    return filtered


def _extract_file_path_evidence(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    paths: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            continue
        text = str(task.get("last_output") or "")
        if not text:
            continue
        for match in FILE_PATH_PATTERN.findall(text):
            candidate = match.strip().lstrip("./")
            if not candidate:
                continue
            if candidate.startswith("http/") or candidate.startswith("https/"):
                continue
            paths.add(candidate)
    sorted_paths = sorted(paths)
    dirs: set[str] = set()
    for path in sorted_paths:
        parts = path.split("/")
        if len(parts) > 1 and parts[0]:
            dirs.add(parts[0])
    return {
        "file_paths": sorted_paths,
        "distinct_file_count": len(sorted_paths),
        "distinct_dirs": sorted(dirs),
        "distinct_dir_count": len(dirs),
    }


def _extract_task_terminal_forward_param(task: Any) -> str:
    if not isinstance(task, dict):
        return ""
    routing = task.get("last_proposal") if isinstance(task.get("last_proposal"), dict) else {}
    routing = routing.get("routing") if isinstance(routing.get("routing"), dict) else {}
    live_terminal = routing.get("live_terminal") if isinstance(routing.get("live_terminal"), dict) else {}
    verification = task.get("verification_status") if isinstance(task.get("verification_status"), dict) else {}
    opencode_live_terminal = (
        verification.get("opencode_live_terminal") if isinstance(verification.get("opencode_live_terminal"), dict) else {}
    )
    cli_session = verification.get("cli_session") if isinstance(verification.get("cli_session"), dict) else {}
    for candidate in (live_terminal, opencode_live_terminal, cli_session):
        forward_param = str(candidate.get("forward_param") or "").strip()
        if forward_param:
            return forward_param
    return ""


def _extract_task_terminal_agent_url(task: Any) -> str:
    if not isinstance(task, dict):
        return ""
    routing = task.get("last_proposal") if isinstance(task.get("last_proposal"), dict) else {}
    routing = routing.get("routing") if isinstance(routing.get("routing"), dict) else {}
    live_terminal = routing.get("live_terminal") if isinstance(routing.get("live_terminal"), dict) else {}
    verification = task.get("verification_status") if isinstance(task.get("verification_status"), dict) else {}
    opencode_live_terminal = (
        verification.get("opencode_live_terminal") if isinstance(verification.get("opencode_live_terminal"), dict) else {}
    )
    cli_session = verification.get("cli_session") if isinstance(verification.get("cli_session"), dict) else {}
    for candidate in (live_terminal, opencode_live_terminal, cli_session):
        agent_url = str(candidate.get("agent_url") or "").strip()
        if agent_url:
            return agent_url
    return str(task.get("assigned_agent_url") or task.get("agent_url") or "").strip()


def _pick_task_terminal(tasks: List[dict], preferred_agent_url: str = "") -> tuple[str, str]:
    preferred_agent_url = str(preferred_agent_url or "").strip()
    fallback: tuple[str, str] = ("", "")
    for task in tasks:
        if not isinstance(task, dict):
            continue
        forward_param = _extract_task_terminal_forward_param(task)
        if not forward_param:
            continue
        assigned_agent_url = _extract_task_terminal_agent_url(task)
        if preferred_agent_url and assigned_agent_url == preferred_agent_url:
            return forward_param, assigned_agent_url
        if not fallback[0]:
            fallback = (forward_param, assigned_agent_url)
    return fallback


def _open_worker_terminal_panel(
    session_id: str,
    worker_name: str,
    mode: str = "read",
    forward_param: Optional[str] = None,
) -> Dict[str, Any]:
    worker_name = str(worker_name or "").strip()
    if not worker_name:
        return {"attempted": False, "opened": False, "connected": False, "error": "missing_worker_name"}
    route = f"/panel/{worker_name}?tab=terminal&mode={mode}"
    if forward_param:
        route = f"{route}&forward_param={quote(str(forward_param), safe='')}"
    step_nav(session_id, route, settle_s=1.2)
    opened = wait_for(
        session_id,
        """
        const heading = [...document.querySelectorAll('h1,h2,h3')]
          .find((node) => /agent panel/i.test((node.textContent || '').trim()));
        const liveTitle = [...document.querySelectorAll('h1,h2,h3')]
          .find((node) => /live terminal/i.test((node.textContent || '').trim()));
        return !!heading && !!liveTitle;
        """,
        20,
    )
    buffer_ready = wait_for(
        session_id,
        "return !!document.querySelector('[data-testid=\"terminal-output-buffer\"]');",
        20,
    )
    connected = wait_for(
        session_id,
        """
        const pill = document.querySelector('.status-pill');
        const buffer = document.querySelector('[data-testid="terminal-output-buffer"]');
        const pillText = (pill?.textContent || '').trim();
        const bufferText = (buffer?.textContent || '').trim();
        return /status:\\s*connected/i.test(pillText) || /\\[connected:/i.test(bufferText);
        """,
        30,
    )
    terminal_snapshot = (
        js(
            session_id,
            """
            const pill = document.querySelector('.status-pill');
            const buffer = document.querySelector('[data-testid="terminal-output-buffer"]');
            const bufferText = (buffer?.textContent || '');
            return {
              status_text: (pill?.textContent || '').trim(),
              buffer_excerpt: bufferText.slice(-4000),
              cli_command_visible: /opencode\\s+run/i.test(bufferText),
            };
            """,
        ).get("value")
        or {}
    )
    print("worker_terminal_opened", worker_name, "opened", opened and buffer_ready, "connected", connected, flush=True)
    return {
        "attempted": True,
        "worker_name": worker_name,
        "mode": mode,
        "forward_param": str(forward_param or ""),
        "route": route,
        "opened": bool(opened and buffer_ready),
        "connected": bool(connected),
        "status_text": str(terminal_snapshot.get("status_text") or ""),
        "buffer_excerpt": str(terminal_snapshot.get("buffer_excerpt") or ""),
        "cli_command_visible": bool(terminal_snapshot.get("cli_command_visible")),
        **current_route_and_title(session_id),
    }


def _inspect_task_detail_live_terminal(session_id: str, task_id: str) -> Dict[str, Any]:
    task_id = str(task_id or "").strip()
    if not task_id:
        return {"attempted": False, "error": "missing_task_id"}
    route = f"/task/{quote(task_id, safe='')}"
    step_nav(session_id, route, settle_s=1.0)
    task_ready = wait_for(
        session_id,
        """
        return !![...document.querySelectorAll('h1,h2,h3')].find((node) => /task/i.test((node.textContent || '').trim()));
        """,
        20,
    )
    logs_clicked = bool(
        js(
            session_id,
            """
            const button = [...document.querySelectorAll('button')].find((node) => /^logs$/i.test((node.textContent || '').trim()));
            if (!button) return false;
            button.click();
            return true;
            """,
        ).get("value")
    )
    terminal_ready = wait_for(
        session_id,
        """
        const root = document.querySelector('[data-testid="task-live-terminal"]');
        const buffer = root?.querySelector('[data-testid="terminal-output-buffer"]');
        return !!root && !!buffer;
        """,
        30,
    )
    connected = wait_for(
        session_id,
        """
        const root = document.querySelector('[data-testid="task-live-terminal"]');
        const pill = root?.querySelector('.status-pill');
        const buffer = root?.querySelector('[data-testid="terminal-output-buffer"]');
        const pillText = (pill?.textContent || '').trim();
        const bufferText = (buffer?.textContent || '').trim();
        return /status:\\s*connected/i.test(pillText) || /\\[connected:/i.test(bufferText);
        """,
        30,
    )
    snapshot = (
        js(
            session_id,
            """
            const root = document.querySelector('[data-testid="task-live-terminal"]');
            const pill = root?.querySelector('.status-pill');
            const buffer = root?.querySelector('[data-testid="terminal-output-buffer"]');
            const quickInput = root?.querySelector('input[aria-label="Terminal-Befehl"]');
            const sendButton = [...(root?.querySelectorAll('button') || [])].find((node) => /senden/i.test((node.textContent || '').trim()));
            const panelLink = [...document.querySelectorAll('a,button')].find((node) => /worker-panel|worker-panel oeffnen|im worker-panel oeffnen/i.test((node.textContent || '').trim().toLowerCase()));
            return {
              status_text: (pill?.textContent || '').trim(),
              buffer_excerpt: (buffer?.textContent || '').slice(-4000),
              input_visible: !!quickInput && quickInput.offsetParent !== null,
              send_visible: !!sendButton && sendButton.offsetParent !== null,
              panel_link_visible: !!panelLink && panelLink.offsetParent !== null,
            };
            """,
        ).get("value")
        or {}
    )
    return {
        "attempted": True,
        "task_id": task_id,
        "route": route,
        "opened": bool(task_ready),
        "logs_clicked": logs_clicked,
        "embedded_visible": bool(terminal_ready),
        "connected": bool(connected),
        "interactive_controls_visible": bool(snapshot.get("input_visible")) and bool(snapshot.get("send_visible")),
        "panel_link_visible": bool(snapshot.get("panel_link_visible")),
        "status_text": str(snapshot.get("status_text") or ""),
        "buffer_excerpt": str(snapshot.get("buffer_excerpt") or ""),
        **current_route_and_title(session_id),
    }


def _open_operations_console_artifact_flow(session_id: str) -> Dict[str, Any]:
    step_nav(session_id, "/operations", settle_s=1.0)
    heading_ready = wait_for(
        session_id,
        "return !![...document.querySelectorAll('h1,h2,h3')].find((node) => /operations konsole/i.test((node.textContent || '').trim()));",
        25,
    )
    artifact_ready = wait_for(
        session_id,
        "return !![...document.querySelectorAll('h1,h2,h3,button,div')].find((node) => /artifact flow/i.test((node.textContent || '').trim()));",
        25,
    )
    details_toggled = bool(
        js(
            session_id,
            """
            const button = [...document.querySelectorAll('button')].find((node) =>
              /Details anzeigen|Details ausblenden/i.test((node.textContent || '').trim())
            );
            if (!button) return false;
            button.click();
            return true;
            """,
        ).get("value")
    )
    time.sleep(1.0)
    snapshot = (
        js(
            session_id,
            """
            const pageText = (document.body?.innerText || '');
            const rows = document.querySelectorAll('table tbody tr').length;
            const rag = [...document.querySelectorAll('.muted,strong,div')]
              .find((node) => /^RAG$/i.test((node.textContent || '').trim()));
            return {
              artifact_flow_visible: /Artifact Flow/i.test(pageText),
              table_rows: rows,
              rag_label_present: !!rag,
              page_excerpt: pageText.slice(0, 1200),
            };
            """,
        ).get("value")
        or {}
    )
    state = current_route_and_title(session_id)
    return {
        "opened": bool(heading_ready and artifact_ready),
        "details_toggled": details_toggled,
        "artifact_flow_visible": bool(snapshot.get("artifact_flow_visible")),
        "table_rows": int(snapshot.get("table_rows") or 0),
        "rag_label_present": bool(snapshot.get("rag_label_present")),
        "page_excerpt": str(snapshot.get("page_excerpt") or "")[:600],
        **state,
    }


def phase_benchmark(
    session_id: str,
    report: dict,
    hard_fail: bool,
    step_delay_seconds: float,
    benchmark_ticks: int,
    benchmark_task_kind: str,
    require_followup: bool,
    require_artifact_summary: bool,
    require_multi_file_output: bool,
    min_distinct_files: int,
    min_distinct_dirs: int,
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
    goal_trace_id = str((detail_before.get("trace") or {}).get("trace_id") or "")
    tasks_before_full = _collect_goal_tasks_snapshot(
        session_id,
        goal_id=goal_id,
        goal_trace_id=goal_trace_id,
        timeout_seconds=75,
    )
    tasks_before_full_ids = {str(task.get("id") or "") for task in tasks_before_full if isinstance(task, dict)}
    team_id = str((detail_before.get("goal") or {}).get("team_id") or "")
    task_team_patch_info: Dict[str, Any] = {"resolved_team_id": team_id, "patched_task_ids": [], "patch_statuses": []}
    cleanup_targets = report.get("cleanup_targets") if isinstance(report.get("cleanup_targets"), dict) else {}
    expected_team_id = ""
    expected_team_name = ""
    if isinstance(cleanup_targets, dict):
        team_ids = cleanup_targets.get("team_ids")
        if isinstance(team_ids, list) and team_ids:
            expected_team_id = str(team_ids[-1] or "").strip()
        team_names = cleanup_targets.get("team_names")
        if isinstance(team_names, list) and team_names:
            expected_team_name = str(team_names[-1] or "").strip()
    if expected_team_id or expected_team_name:
        teams_res = browser_api_json(session_id, "GET", "/teams", timeout_seconds=45)
        teams_payload = _unwrap_envelope(teams_res.get("body")) if teams_res.get("ok") else []
        teams = teams_payload if isinstance(teams_payload, list) else []
        matched_team = next(
            (
                item
                for item in teams
                if isinstance(item, dict)
                and (
                    (expected_team_id and str(item.get("id") or "").strip() == expected_team_id)
                    or (expected_team_name and str(item.get("name") or "").strip() == expected_team_name)
                )
            ),
            None,
        )
        resolved_team_id = str((matched_team or {}).get("id") or "").strip()
        if resolved_team_id:
            task_team_patch_info["resolved_team_id"] = resolved_team_id
            needs_team_retarget = not team_id or team_id != resolved_team_id
            for task in tasks_before_full:
                if not isinstance(task, dict):
                    continue
                task_id = str(task.get("id") or "").strip()
                task_team_id = str(task.get("team_id") or "").strip()
                if not task_id or (task_team_id == resolved_team_id and not needs_team_retarget):
                    continue
                patch_res = browser_api_json(
                    session_id,
                    "PATCH",
                    f"/tasks/{task_id}",
                    body={"team_id": resolved_team_id},
                    timeout_seconds=45,
                )
                task_team_patch_info["patch_statuses"].append(
                    {
                        "task_id": task_id,
                        "from_team_id": task_team_id,
                        "status": int(patch_res.get("status") or 0),
                    }
                )
                if patch_res.get("ok") and int(patch_res.get("status") or 0) < 400:
                    task_team_patch_info["patched_task_ids"].append(task_id)
            if resolved_team_id:
                team_id = resolved_team_id
    if team_id:
        report.setdefault("cleanup_targets", {}).setdefault("team_ids", []).append(team_id)

    # Ensure the goal team has online workers assigned, otherwise autopilot stays idle.
    worker_bind_info: Dict[str, Any] = {"team_id": team_id, "applied": False}
    agent_entries: List[Dict[str, Any]] = []
    team_obj: Optional[Dict[str, Any]] = None
    if team_id:
        agents_res = browser_api_json(session_id, "GET", "/api/system/agents", timeout_seconds=45)
        teams_res = browser_api_json(session_id, "GET", "/teams", timeout_seconds=45)
        workers_payload = _unwrap_envelope(agents_res.get("body")) if agents_res.get("ok") else []
        teams_payload = _unwrap_envelope(teams_res.get("body")) if teams_res.get("ok") else []
        workers = workers_payload if isinstance(workers_payload, list) else []
        teams = teams_payload if isinstance(teams_payload, list) else []
        agent_entries = [item for item in workers if isinstance(item, dict)]
        team_obj = next((t for t in teams if isinstance(t, dict) and str(t.get("id") or "") == team_id), None)
        online_worker_urls = [
            str(a.get("url") or "")
            for a in agent_entries
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

    if not agent_entries:
        agents_res = browser_api_json(session_id, "GET", "/api/system/agents", timeout_seconds=45)
        agents_payload = _unwrap_envelope(agents_res.get("body")) if agents_res.get("ok") else []
        agent_entries = [item for item in (agents_payload if isinstance(agents_payload, list) else []) if isinstance(item, dict)]

    preferred_worker_urls: List[str] = []
    if team_obj:
        for member in ((team_obj or {}).get("members") or []):
            if not isinstance(member, dict):
                continue
            agent_url = str(member.get("agent_url") or "").strip()
            if agent_url and agent_url not in preferred_worker_urls:
                preferred_worker_urls.append(agent_url)
    for member in (worker_bind_info.get("members_payload") or []):
        if not isinstance(member, dict):
            continue
        agent_url = str(member.get("agent_url") or "").strip()
        if agent_url and agent_url not in preferred_worker_urls:
            preferred_worker_urls.append(agent_url)

    online_worker_agents = [
        agent
        for agent in agent_entries
        if str(agent.get("role") or "").lower() == "worker" and str(agent.get("status") or "").lower() == "online"
    ]
    terminal_agent: Optional[Dict[str, Any]] = None
    for worker_url in preferred_worker_urls:
        terminal_agent = next((agent for agent in online_worker_agents if str(agent.get("url") or "").strip() == worker_url), None)
        if terminal_agent:
            break
    if not terminal_agent and online_worker_agents:
        terminal_agent = online_worker_agents[0]

    terminal_view = {"attempted": False, "opened": False, "connected": False}
    terminal_forward_param = ""
    if terminal_agent:
        terminal_forward_param, terminal_agent_url = _pick_task_terminal(
            [task for task in tasks_before_full if isinstance(task, dict)],
            preferred_agent_url=str(terminal_agent.get("url") or ""),
        )
        if terminal_agent_url:
            matched_terminal_agent = next(
                (agent for agent in online_worker_agents if str(agent.get("url") or "").strip() == terminal_agent_url),
                None,
            )
            if matched_terminal_agent:
                terminal_agent = matched_terminal_agent

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
        tick_res = browser_api_json(session_id, "POST", "/tasks/autopilot/tick", body=tick_body, timeout_seconds=180)
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
    tasks_after_full = _collect_goal_tasks_snapshot(
        session_id,
        goal_id=goal_id,
        goal_trace_id=goal_trace_id,
        timeout_seconds=75,
    )
    if terminal_agent and not terminal_forward_param:
        terminal_forward_param, terminal_agent_url = _pick_task_terminal(
            [task for task in tasks_after_full if isinstance(task, dict)],
            preferred_agent_url=str(terminal_agent.get("url") or ""),
        )
        if terminal_forward_param:
            if terminal_agent_url:
                matched_terminal_agent = next(
                    (agent for agent in online_worker_agents if str(agent.get("url") or "").strip() == terminal_agent_url),
                    None,
                )
                if matched_terminal_agent:
                    terminal_agent = matched_terminal_agent
    task_detail_terminal = {"attempted": False, "embedded_visible": False, "connected": False}
    preferred_terminal_agent_url = str(terminal_agent.get("url") or "").strip() if isinstance(terminal_agent, dict) else ""
    preferred_terminal_task: Optional[dict] = None
    fallback_terminal_task: Optional[dict] = None
    for task in tasks_after_full:
        if not isinstance(task, dict):
            continue
        forward_param = _extract_task_terminal_forward_param(task)
        if not forward_param:
            continue
        if fallback_terminal_task is None:
            fallback_terminal_task = task
        agent_url = _extract_task_terminal_agent_url(task)
        if preferred_terminal_agent_url and agent_url == preferred_terminal_agent_url:
            preferred_terminal_task = task
            break
    selected_terminal_task = preferred_terminal_task or fallback_terminal_task
    tasks_after_full_ids = {str(task.get("id") or "") for task in tasks_after_full if isinstance(task, dict)}

    after_status = _summarize_tasks(tasks_after)
    fib_mentions = 0
    for task in tasks_after:
        if not isinstance(task, dict):
            continue
        task_blob = f"{task.get('title', '')} {task.get('description', '')}".lower()
        if "fibonacci" in task_blob:
            fib_mentions += 1
    new_task_ids = sorted(task_id for task_id in tasks_after_full_ids if task_id and task_id not in tasks_before_full_ids)
    followup_task_ids: List[str] = []
    for task in tasks_after_full:
        if not isinstance(task, dict):
            continue
        tid = str(task.get("id") or "")
        if not tid:
            continue
        parent_id = str(task.get("parent_task_id") or "")
        source_id = str(task.get("source_task_id") or "")
        if (parent_id and parent_id in tasks_before_full_ids) or (source_id and source_id in tasks_before_full_ids):
            followup_task_ids.append(tid)
    followup_observed = len(followup_task_ids) > 0 or len(new_task_ids) > 0
    followup_created = followup_observed
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
            tick_res = browser_api_json(session_id, "POST", "/tasks/autopilot/tick", body=tick_body, timeout_seconds=180)
            tick_results.append(tick_res)
            time.sleep(1.0)

        detail_after_res = browser_api_json(session_id, "GET", f"/goals/{goal_id}/detail", timeout_seconds=60)
        detail_after = _unwrap_envelope(detail_after_res.get("body")) if detail_after_res.get("ok") else {}
        detail_after = detail_after if isinstance(detail_after, dict) else {}
        tasks_after = detail_after.get("tasks") if isinstance(detail_after, dict) else []
        tasks_after = tasks_after if isinstance(tasks_after, list) else []
        tasks_after_full = _collect_goal_tasks_snapshot(
            session_id,
            goal_id=goal_id,
            goal_trace_id=goal_trace_id,
            timeout_seconds=75,
        )
        tasks_after_full_ids = {str(task.get("id") or "") for task in tasks_after_full if isinstance(task, dict)}
        after_status = _summarize_tasks(tasks_after)
        fib_mentions = 0
        for task in tasks_after:
            if not isinstance(task, dict):
                continue
            task_blob = f"{task.get('title', '')} {task.get('description', '')}".lower()
            if "fibonacci" in task_blob:
                fib_mentions += 1
        new_task_ids = sorted(task_id for task_id in tasks_after_full_ids if task_id and task_id not in tasks_before_full_ids)
        followup_task_ids = []
        for task in tasks_after_full:
            if not isinstance(task, dict):
                continue
            tid = str(task.get("id") or "")
            if not tid:
                continue
            parent_id = str(task.get("parent_task_id") or "")
            source_id = str(task.get("source_task_id") or "")
            if (parent_id and parent_id in tasks_before_full_ids) or (source_id and source_id in tasks_before_full_ids):
                followup_task_ids.append(tid)
        followup_observed = len(followup_task_ids) > 0 or len(new_task_ids) > 0
        followup_created = followup_observed
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

    artifacts_summary = (detail_after.get("artifacts") or {}) if isinstance(detail_after, dict) else {}
    result_summary = (artifacts_summary.get("result_summary") or {}) if isinstance(artifacts_summary, dict) else {}
    headline_artifact = (artifacts_summary.get("headline_artifact") or {}) if isinstance(artifacts_summary, dict) else {}
    artifact_entries = (artifacts_summary.get("artifacts") or []) if isinstance(artifacts_summary, dict) else []
    artifact_summary_ok = bool(result_summary) and (
        bool((headline_artifact or {}).get("preview"))
        or (isinstance(artifact_entries, list) and len(artifact_entries) > 0)
    )
    orchestration_rm_res = browser_api_json(
        session_id,
        "GET",
        "/tasks/orchestration/read-model?artifact_flow_enabled=1&artifact_flow_rag_enabled=1&artifact_flow_rag_include_content=1&artifact_flow_rag_top_k=5",
        timeout_seconds=90,
    )
    orchestration_rm_payload = _unwrap_envelope(orchestration_rm_res.get("body")) if orchestration_rm_res.get("ok") else {}
    orchestration_rm_payload = orchestration_rm_payload if isinstance(orchestration_rm_payload, dict) else {}
    artifact_flow_rm = orchestration_rm_payload.get("artifact_flow")
    artifact_flow_rm = artifact_flow_rm if isinstance(artifact_flow_rm, dict) else {}
    reconciliation_rm = orchestration_rm_payload.get("worker_execution_reconciliation")
    reconciliation_rm = reconciliation_rm if isinstance(reconciliation_rm, dict) else {}
    artifact_flow_items = artifact_flow_rm.get("items") if isinstance(artifact_flow_rm.get("items"), list) else []
    tracked_goal_task_ids = {str(task.get("id") or "") for task in tasks_after_full if isinstance(task, dict)}
    matching_artifact_flow_items = [
        item
        for item in artifact_flow_items
        if isinstance(item, dict) and str(item.get("task_id") or "") in tracked_goal_task_ids
    ]
    artifact_flow_summary = {
        "available": bool(artifact_flow_rm),
        "enabled": bool(artifact_flow_rm.get("enabled")),
        "config": dict(artifact_flow_rm.get("config") or {}) if isinstance(artifact_flow_rm.get("config"), dict) else {},
        "counts": dict(artifact_flow_rm.get("counts") or {}) if isinstance(artifact_flow_rm.get("counts"), dict) else {},
        "matching_item_count": len(matching_artifact_flow_items),
        "matching_task_ids": [str(item.get("task_id") or "") for item in matching_artifact_flow_items[:10]],
        "matching_sent_artifact_count": sum(len(item.get("sent_artifact_ids") or []) for item in matching_artifact_flow_items),
        "matching_returned_artifact_count": sum(len(item.get("returned_artifact_ids") or []) for item in matching_artifact_flow_items),
        "matching_worker_job_count": sum(len(item.get("worker_jobs") or []) for item in matching_artifact_flow_items),
        "matching_rag_context_count": sum(len(item.get("rag_context") or []) for item in matching_artifact_flow_items),
        "sample_items": matching_artifact_flow_items[:3],
    }
    reconciliation_summary = {
        "available": bool(reconciliation_rm),
        "issue_count": int(reconciliation_rm.get("issue_count") or 0),
        "counts": dict(reconciliation_rm.get("counts") or {}) if isinstance(reconciliation_rm.get("counts"), dict) else {},
        "issues": list(reconciliation_rm.get("issues") or [])[:5] if isinstance(reconciliation_rm.get("issues"), list) else [],
    }
    operations_console = _open_operations_console_artifact_flow(session_id)
    file_evidence = _extract_file_path_evidence(tasks_after_full)
    multi_file_output_ok = (
        int(file_evidence.get("distinct_file_count") or 0) >= int(max(1, min_distinct_files))
        and int(file_evidence.get("distinct_dir_count") or 0) >= int(max(1, min_distinct_dirs))
    )
    terminal_buffer = str(terminal_view.get("buffer_excerpt") or "")
    terminal_cli_visible = bool(terminal_view.get("cli_command_visible")) or "opencode run" in terminal_buffer.lower()
    terminal_workdir_error = "failed to change directory" in terminal_buffer.lower()
    task_detail_terminal_ok = (
        not task_detail_terminal.get("attempted")
        or (
            bool(task_detail_terminal.get("embedded_visible"))
            and bool(task_detail_terminal.get("connected"))
            and bool(task_detail_terminal.get("interactive_controls_visible"))
        )
    )
    provider_breakdown = (result_summary.get("cost_summary") or {}).get("provider_breakdown") if isinstance(result_summary, dict) else []
    provider_breakdown = provider_breakdown if isinstance(provider_breakdown, list) else []
    model_usage_ok = any(
        isinstance(item, dict)
        and str(item.get("provider") or "").strip().lower() == "opencode"
        and str(item.get("model") or "").strip() == model
        for item in provider_breakdown
    )

    benchmark_success = after_status["completed"] > 0
    if require_followup:
        benchmark_success = benchmark_success and followup_observed
    if require_artifact_summary:
        benchmark_success = benchmark_success and artifact_summary_ok
    if require_multi_file_output:
        benchmark_success = benchmark_success and multi_file_output_ok
    benchmark_success = benchmark_success and terminal_cli_visible and not terminal_workdir_error and model_usage_ok and task_detail_terminal_ok
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
    if terminal_agent:
        terminal_view = _open_worker_terminal_panel(
            session_id,
            str(terminal_agent.get("name") or ""),
            mode="interactive" if terminal_forward_param else "read",
            forward_param=terminal_forward_param or None,
        )
    if isinstance(selected_terminal_task, dict):
        task_detail_terminal = _inspect_task_detail_live_terminal(session_id, str(selected_terminal_task.get("id") or ""))

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
    if require_followup or require_artifact_summary or require_multi_file_output:
        ok = ok and benchmark_success
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
            "task_team_patch_info": task_team_patch_info,
            "worker_terminal": terminal_view,
            "task_detail_terminal": task_detail_terminal,
            "terminal_cli_visible": terminal_cli_visible,
            "terminal_workdir_error": terminal_workdir_error,
            "model_usage_ok": model_usage_ok,
            "tasks_before": len(tasks_before),
            "tasks_after": len(tasks_after),
            "tasks_before_full": len(tasks_before_full),
            "tasks_after_full": len(tasks_after_full),
            "new_task_ids": new_task_ids,
            "followup_task_ids": followup_task_ids,
            "followup_created": followup_created,
            "followup_observed": followup_observed,
            "terminalized": terminalized,
            "fibonacci_mentions_in_tasks": fib_mentions,
            "artifacts_summary_present": artifact_summary_ok,
            "result_summary": result_summary if isinstance(result_summary, dict) else {},
            "headline_artifact_preview": str((headline_artifact or {}).get("preview") or "")[:280],
            "artifact_flow": artifact_flow_summary,
            "execution_reconciliation": reconciliation_summary,
            "operations_console": operations_console,
            "file_output_evidence": file_evidence,
            "multi_file_output_ok": multi_file_output_ok,
            "require_followup": require_followup,
            "require_artifact_summary": require_artifact_summary,
            "require_multi_file_output": require_multi_file_output,
            "min_distinct_files": int(max(1, min_distinct_files)),
            "min_distinct_dirs": int(max(1, min_distinct_dirs)),
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
                    explicit_targets=explicit_targets,
                )
                report["cleanup"] = cleanup
            except Exception as e:
                report["errors"].append({"message": f"cleanup_error: {e}"})
                report["cleanup"] = {"error": str(e)}
                print("cleanup_error", str(e), flush=True)
        write_report(report, report_path)


if __name__ == "__main__":
    main()
