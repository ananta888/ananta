#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import time
from typing import Dict, List, Optional

from firefox_automation._benchmark import (
    _select_preferred_team,
    _unwrap_envelope,
    browser_api_json,
    ensure_opencode_execution_mode,
)
from firefox_automation._browser_utils import current_route_and_title, settle, step_nav
from firefox_automation._config import APP_BASE, LOGIN_PASS, LOGIN_USER
from firefox_automation._reporting import gate_visible_errors, record_step
from firefox_automation._webdriver import (
    element_clear,
    element_click,
    element_send_keys,
    find_element,
    js,
    set_input_value_via_js,
    wait_for,
    wd,
)


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
    preferred_team = _select_preferred_team(
        session_id,
        preferred_team_id=expected_team_id,
        preferred_team_name=expected_team_name,
    )
    if not expected_team_id:
        expected_team_id = str(preferred_team.get("team_id") or "").strip()
    if not expected_team_name:
        expected_team_name = str(preferred_team.get("team_name") or "").strip()
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
    click_debug["preferred_team"] = preferred_team
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


