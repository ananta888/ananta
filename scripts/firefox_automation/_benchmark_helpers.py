#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from firefox_automation._config import FILE_PATH_PATTERN, HUB_BASE, LOGIN_PASS
from firefox_automation._browser_utils import current_route_and_title, list_visible_errors, settle, step_nav
from firefox_automation._reporting import gate_visible_errors, record_step
from firefox_automation._webdriver import (
    element_clear,
    element_click,
    element_send_keys,
    find_element,
    js,
    js_async,
    set_input_value_via_js,
    wait_for,
    wd,
)


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


def ensure_opencode_execution_mode(
    session_id: str,
    mode: str = "interactive_terminal",
    *,
    interactive_launch_mode: str = "run",
    timeout_seconds: int = 90,
) -> dict:
    try:
        out = (
            js_async(
                session_id,
                """
            const desiredMode = arguments[0];
            const desiredLaunchMode = arguments[1];
            const timeoutSeconds = arguments[2];
            const password = arguments[3] || '';
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
                body: JSON.stringify({ opencode_runtime: { execution_mode: desiredMode, interactive_launch_mode: desiredLaunchMode } }),
              });
              const get = await fetchJson(`${baseUrl}/config`, {
                method: 'GET',
                headers,
              });
              const effectiveMode = ((((get.body || {}).data || {}).opencode_runtime || {}).execution_mode) || '';
              const effectiveLaunchMode = ((((get.body || {}).data || {}).opencode_runtime || {}).interactive_launch_mode) || '';
              return {
                base_url: baseUrl,
                ok: post.status >= 200 && post.status < 400 && effectiveMode === desiredMode && effectiveLaunchMode === desiredLaunchMode,
                post_status: post.status,
                get_status: get.status,
                execution_mode: effectiveMode,
                interactive_launch_mode: effectiveLaunchMode,
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
                  desired_launch_mode: desiredLaunchMode,
                  base_urls: baseUrls,
                  results,
                });
              })
              .catch((err) => {
                clearTimeout(timer);
                done({ ok: false, error: String(err), desired_mode: desiredMode, desired_launch_mode: desiredLaunchMode });
              });
            """,
                [mode, interactive_launch_mode, timeout_seconds, LOGIN_PASS],
                timeout=max(45, int(timeout_seconds) + 30),
            ).get("value")
            or {}
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"webdriver_async_error: {exc}",
            "desired_mode": mode,
            "desired_launch_mode": interactive_launch_mode,
        }
    if not isinstance(out, dict):
        return {
            "ok": False,
            "error": "invalid_mode_response",
            "desired_mode": mode,
            "desired_launch_mode": interactive_launch_mode,
        }
    return out


def _unwrap_envelope(payload: Any) -> Any:
    cur = payload
    for _ in range(4):
        if isinstance(cur, dict) and "data" in cur and "status" in cur:
            cur = cur.get("data")
            continue
        break
    return cur


def _select_preferred_team(
    session_id: str,
    *,
    preferred_team_id: str = "",
    preferred_team_name: str = "",
) -> Dict[str, str]:
    preferred_team_id = str(preferred_team_id or "").strip()
    preferred_team_name = str(preferred_team_name or "").strip().lower()
    teams_res = browser_api_json(session_id, "GET", "/teams", timeout_seconds=45)
    agents_res = browser_api_json(session_id, "GET", "/api/system/agents", timeout_seconds=45)
    teams_payload = _unwrap_envelope(teams_res.get("body")) if teams_res.get("ok") else []
    agents_payload = _unwrap_envelope(agents_res.get("body")) if agents_res.get("ok") else []
    teams = [item for item in (teams_payload if isinstance(teams_payload, list) else []) if isinstance(item, dict)]
    agents = [item for item in (agents_payload if isinstance(agents_payload, list) else []) if isinstance(item, dict)]
    online_worker_urls = {
        str(agent.get("url") or "").strip()
        for agent in agents
        if str(agent.get("role") or "").strip().lower() == "worker" and str(agent.get("status") or "").strip().lower() == "online"
    }

    def _matches_preferred(team: Dict[str, Any]) -> bool:
        team_id = str(team.get("id") or "").strip()
        team_name = str(team.get("name") or "").strip().lower()
        return (preferred_team_id and team_id == preferred_team_id) or (preferred_team_name and team_name == preferred_team_name)

    def _has_online_worker_member(team: Dict[str, Any]) -> bool:
        members = team.get("members") if isinstance(team.get("members"), list) else []
        return any(str(member.get("agent_url") or "").strip() in online_worker_urls for member in members if isinstance(member, dict))

    preferred = next((team for team in teams if _matches_preferred(team)), None)
    selected = (
        preferred
        or next((team for team in teams if bool(team.get("is_active")) and _has_online_worker_member(team)), None)
        or next((team for team in teams if _has_online_worker_member(team)), None)
        or next((team for team in teams if bool(team.get("is_active"))), None)
        or (teams[0] if teams else None)
    )
    return {
        "team_id": str((selected or {}).get("id") or ""),
        "team_name": str((selected or {}).get("name") or ""),
    }


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


def _model_identifier_tokens(value: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9.]+", " ", str(value or "").strip().lower())
    return {token for token in normalized.split() if token}


def _model_identifier_matches(left: str, right: str) -> bool:
    left_value = str(left or "").strip()
    right_value = str(right or "").strip()
    if not left_value or not right_value:
        return False
    if left_value.lower() == right_value.lower():
        return True
    left_tokens = _model_identifier_tokens(left_value)
    right_tokens = _model_identifier_tokens(right_value)
    overlap = left_tokens & right_tokens
    return len(overlap) >= 2 and (left_tokens.issubset(right_tokens) or right_tokens.issubset(left_tokens))


def _task_terminal_runtime(task: Any) -> Dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    verification = task.get("verification_status") if isinstance(task.get("verification_status"), dict) else {}
    routing = task.get("last_proposal") if isinstance(task.get("last_proposal"), dict) else {}
    routing = routing.get("routing") if isinstance(routing.get("routing"), dict) else {}
    candidate_sources = [
        routing.get("live_terminal") if isinstance(routing.get("live_terminal"), dict) else {},
        verification.get("opencode_live_terminal") if isinstance(verification.get("opencode_live_terminal"), dict) else {},
        verification.get("cli_session") if isinstance(verification.get("cli_session"), dict) else {},
    ]
    runtime: Dict[str, Any] = {
        "forward_param": "",
        "agent_url": str(task.get("assigned_agent_url") or task.get("agent_url") or "").strip(),
        "model": "",
        "session_status": "",
        "terminal_status": "",
        "task_status": str(task.get("status") or "").strip().lower(),
        "current_worker_job_id": str(task.get("current_worker_job_id") or "").strip(),
    }
    for candidate in candidate_sources:
        if not isinstance(candidate, dict):
            continue
        if not runtime["forward_param"]:
            runtime["forward_param"] = str(candidate.get("forward_param") or "").strip()
        if not runtime["agent_url"]:
            runtime["agent_url"] = str(candidate.get("agent_url") or "").strip()
        if not runtime["model"]:
            runtime["model"] = str(candidate.get("model") or "").strip()
        if not runtime["session_status"]:
            runtime["session_status"] = str(candidate.get("status") or "").strip().lower()
        if not runtime["terminal_status"]:
            runtime["terminal_status"] = str(candidate.get("terminal_status") or "").strip().lower()
    return runtime


def _task_has_active_terminal(task: Any) -> bool:
    runtime = _task_terminal_runtime(task)
    if not runtime.get("forward_param"):
        return False
    if runtime.get("session_status") in {"active", "connected"}:
        return True
    if runtime.get("terminal_status") in {"active", "connected"}:
        return True
    if runtime.get("current_worker_job_id"):
        return True
    return runtime.get("task_status") in {"assigned", "running", "in_progress"}


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


