"""Tool-calling loop for the ananta-settings (Ananta-Konfig) chat session.

The LLM gets:
  - system prompt:  UI-Guide role instructions (from the session config)
  - user message:   live config snapshot + generated UI guide + user question

And can call these tools when it needs more detail:
  - search_ui_docs(query)       CodeCompass + keyword search in docs/ and Angular files
  - read_ananta_config(section) current user.json settings snapshot
  - get_hub_workers()           worker pool status from the repository layer
  - get_hub_sessions()          active agent sessions
  - get_hub_policies()          pending policy approvals
  - refresh_ui_guide()          force regenerate docs/ananta-ui-guide.md from source
"""
from __future__ import annotations

import json
import logging
import pathlib as _pl
import re
import time as _time
from typing import Any

from agent.services.snake_chat_cancellation import is_chat_cancelled

_log = logging.getLogger(__name__)
_REPO_ROOT = _pl.Path(__file__).parent.parent.parent
_UI_GUIDE_PATH = _REPO_ROOT / "docs" / "ananta-ui-guide.md"
_UI_GUIDE_SCRIPT = _REPO_ROOT / "scripts" / "generate_ui_guide.py"
_MAX_TOOL_RESULT_CHARS = 6000
_GUIDE_MAX_AGE_SECONDS = 86_400  # regenerate guide if older than 24 h

# ── Guide generation / freshness ──────────────────────────────────────────────

def ensure_ui_guide(force: bool = False) -> str:
    """Return the UI guide markdown, generating it when missing or stale."""
    needs_regen = force
    if not needs_regen:
        if not _UI_GUIDE_PATH.exists():
            needs_regen = True
        else:
            age = _time.time() - _UI_GUIDE_PATH.stat().st_mtime
            if age > _GUIDE_MAX_AGE_SECONDS:
                needs_regen = True

    if needs_regen:
        try:
            import subprocess
            import sys
            subprocess.run(
                [sys.executable, str(_UI_GUIDE_SCRIPT)],
                check=False, timeout=30,
                cwd=str(_REPO_ROOT),
            )
            _log.info("UI guide regenerated: %s", _UI_GUIDE_PATH)
        except Exception as exc:
            _log.warning("UI guide generation failed: %s", exc)

    try:
        return _UI_GUIDE_PATH.read_text(encoding="utf-8")
    except Exception:
        return ""


# ── Tool schemas ──────────────────────────────────────────────────────────────

_CONFIG_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_ui_docs",
            "description": (
                "Search the Ananta UI guide and Angular source files using CodeCompass "
                "semantic search plus keyword grep. "
                "Use this when you need details about a specific UI area, component, route, "
                "or waypoint that wasn't covered in the initial context "
                "(e.g. 'worker pool settings', 'pair dev tab', 'chat backend select')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords to search for (e.g. 'worker pool', 'chat backend', 'pair dev tab')",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_ananta_config",
            "description": (
                "Return the current Ananta user configuration from user.json. "
                "Use 'section' to narrow the output: "
                "'chat' (chat sessions, backend, model), "
                "'routing' (model routing rules), "
                "'all' (complete settings dump). Default: 'chat'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "enum": ["chat", "routing", "all"],
                        "description": "Which config section to return",
                        "default": "chat",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_hub_workers",
            "description": (
                "Return the current list of registered workers from the Ananta Hub. "
                "Includes name, URL, role, health status, capabilities, and last-seen time."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_hub_sessions",
            "description": (
                "Return active agent sessions (goal execution sessions) from the Hub. "
                "These are NOT chat sessions — they represent running agent jobs."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_hub_policies",
            "description": (
                "Return pending policy approval decisions from the Hub. "
                "Shows what actions are waiting for human review."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refresh_ui_guide",
            "description": (
                "Force-regenerate the docs/ananta-ui-guide.md file from the current source code. "
                "Call this when you suspect the guide is outdated (e.g. new UI areas were added). "
                "Returns a summary of what was generated."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


# ── Tool implementations ──────────────────────────────────────────────────────

def _grep_ui_search(query: str) -> list[str]:
    """Keyword search over docs/, the UI guide, and Angular routing/component files."""
    results: list[str] = []
    q_lower = query.lower()
    keywords = [w for w in re.split(r"\W+", q_lower) if len(w) > 2]

    def _score(text: str) -> int:
        tl = text.lower()
        return sum(tl.count(kw) for kw in keywords)

    # All docs/*.md files (feature documentation) — searched first, section-by-section
    docs_dir = _REPO_ROOT / "docs"
    if docs_dir.exists():
        # collect top hits across all markdown files
        doc_hits: list[tuple[str, int, str]] = []  # (label, score, text)
        for doc_file in sorted(docs_dir.glob("*.md")):
            text = doc_file.read_text(encoding="utf-8", errors="replace")
            file_score = _score(text)
            if file_score == 0:
                continue
            # split into sections, pick best section per file
            sections = re.split(r"\n(?=##)", text)
            best = max(sections, key=_score)
            best_score = _score(best)
            if best_score > 0:
                doc_hits.append((f"docs/{doc_file.name}", best_score, best.strip()[:1200]))
        # return top 3 doc hits
        for label, sc, snippet in sorted(doc_hits, key=lambda x: -x[1])[:3]:
            results.append(f"[{label}, score={sc}]\n{snippet}")

    # Generated UI guide — section-by-section
    if _UI_GUIDE_PATH.exists():
        guide_text = _UI_GUIDE_PATH.read_text(encoding="utf-8", errors="replace")
        sections = re.split(r"\n(?=##)", guide_text)
        scored = sorted(((s, _score(s)) for s in sections if _score(s) > 0), key=lambda x: -x[1])
        for sec, sc in scored[:2]:
            results.append(f"[UI-Guide, score={sc}]\n{sec.strip()[:1000]}")

    # Angular routing files
    angular_src = _REPO_ROOT / "frontend-angular" / "src"
    for pattern in ("*.routes.ts", "route-metadata.ts"):
        for f in sorted(angular_src.rglob(pattern)):
            text = f.read_text(encoding="utf-8", errors="replace")
            if _score(text) > 0:
                lines = [ln for ln in text.splitlines() if any(kw in ln.lower() for kw in keywords)]
                if lines:
                    results.append(f"[{f.name}]\n" + "\n".join(lines[:20]))

    # Angular components with data-waypoints
    for f in sorted(angular_src.rglob("*.ts")):
        if "spec" in f.name or ".service." in f.name:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if "data-waypoint" in text and _score(text) > 1:
            lines = [ln for ln in text.splitlines()
                     if any(kw in ln.lower() for kw in keywords) or "data-waypoint" in ln]
            if lines:
                results.append(f"[{f.name}]\n" + "\n".join(lines[:15]))

    return results


def _codecompass_search(query: str) -> list[str]:
    """CodeCompass semantic search restricted to docs/ and Angular files."""
    results: list[str] = []
    try:
        from agent.services.rag_service import get_rag_service
        bundle = get_rag_service().retrieve_context_bundle(
            query,
            max_chunks=6,
            source_types=["repo"],
        )
        chunks = list(bundle.get("chunks") or [])
        for chunk in chunks:
            metadata = dict((chunk or {}).get("metadata") or {})
            path = str(
                metadata.get("file_path") or metadata.get("path")
                or metadata.get("source_id") or chunk.get("source") or ""
            ).strip()
            # restrict to docs/ and Angular
            if not (path.startswith("docs/") or "frontend-angular" in path or "angular" in path.lower()):
                continue
            text = str(chunk.get("text") or chunk.get("content") or "").strip()
            if text:
                results.append(f"[CodeCompass: {path}]\n{text[:800]}")
    except Exception as exc:
        _log.debug("CodeCompass search failed in config tool: %s", exc)
    return results


def _tool_search_ui_docs(query: str) -> str:
    grep_results = _grep_ui_search(query)
    cc_results = _codecompass_search(query)

    # deduplicate by source label prefix
    all_results = grep_results + [r for r in cc_results if not any(
        r.split("\n")[0] in g for g in grep_results
    )]

    if not all_results:
        return f"Keine Treffer für '{query}' in UI-Dokumentation und Angular-Dateien."
    return "\n\n".join(all_results)[:_MAX_TOOL_RESULT_CHARS]


def _tool_read_ananta_config(section: str = "chat") -> str:
    try:
        import sys
        sys.path.insert(0, str(_REPO_ROOT))
        from client_surfaces.operator_tui.config.user_config_manager import get_manager
        cfg = get_manager().load()
    except Exception as exc:
        return f"(Fehler beim Laden der Konfiguration: {exc})"

    if section == "routing":
        subset = {k: cfg[k] for k in cfg if "routing" in k or "model" in k.lower()}
        return json.dumps(subset, indent=2, ensure_ascii=False)[:_MAX_TOOL_RESULT_CHARS]

    if section == "all":
        return json.dumps(cfg, indent=2, ensure_ascii=False)[:_MAX_TOOL_RESULT_CHARS]

    # default: "chat"
    subset = {k: cfg[k] for k in cfg if k.startswith("chat_")}
    return json.dumps(subset, indent=2, ensure_ascii=False)[:_MAX_TOOL_RESULT_CHARS]


def _tool_get_hub_workers() -> str:
    try:
        from agent.services.repository_registry import get_repository_registry
        agents = get_repository_registry().agent_repo.get_all() or []
        items = [{
            "id": str(getattr(a, "name", "") or getattr(a, "url", "") or ""),
            "url": str(getattr(a, "url", "") or ""),
            "role": str(getattr(a, "role", "worker") or "worker"),
            "health": str(getattr(a, "status", "offline") or "offline"),
            "capabilities": list(getattr(a, "capabilities", None) or []),
            "last_seen": float(getattr(a, "last_seen", 0.0) or 0.0),
        } for a in agents]
        return json.dumps({"workers": items, "count": len(items)}, indent=2, ensure_ascii=False)
    except Exception as exc:
        return f"(Fehler beim Laden der Worker: {exc})"


def _tool_get_hub_sessions() -> str:
    try:
        from agent.services.repository_registry import get_repository_registry
        sessions = get_repository_registry().agent_session_repo.get_all() or []
        items = [{
            "id": str(getattr(s, "id", "") or ""),
            "status": str(getattr(s, "status", "") or ""),
            "goal_id": str(getattr(s, "goal_id", "") or ""),
            "worker": str(getattr(s, "worker_url", "") or ""),
            "created_at": str(getattr(s, "created_at", "") or ""),
        } for s in sessions[:20]]
        return json.dumps({"sessions": items, "count": len(sessions)}, indent=2, ensure_ascii=False)
    except Exception as exc:
        return f"(Fehler beim Laden der Sessions: {exc})"


def _tool_get_hub_policies() -> str:
    try:
        from agent.services.repository_registry import get_repository_registry
        policies = get_repository_registry().policy_decision_repo.get_all() or []
        pending = [p for p in policies if str(getattr(p, "status", "") or "") in ("pending", "requested")]
        items = [{
            "id": str(getattr(p, "id", "") or ""),
            "action": str(getattr(p, "action_type", getattr(p, "action", "")) or ""),
            "status": str(getattr(p, "status", "") or ""),
            "goal_id": str(getattr(p, "goal_id", "") or ""),
            "task_id": str(getattr(p, "task_id", "") or ""),
        } for p in pending[:20]]
        return json.dumps({"pending_policies": items, "count": len(pending)}, indent=2, ensure_ascii=False)
    except Exception as exc:
        return f"(Fehler beim Laden der Policies: {exc})"


def _tool_refresh_ui_guide() -> str:
    try:
        guide = ensure_ui_guide(force=True)
        size = len(guide)
        lines = guide.count("\n")
        return f"UI-Guide neu generiert: {size} Zeichen, {lines} Zeilen → {str(_UI_GUIDE_PATH)}"
    except Exception as exc:
        return f"(Fehler beim Regenerieren: {exc})"


def _dispatch_tool(name: str, arguments: dict) -> str:
    if name == "search_ui_docs":
        return _tool_search_ui_docs(str(arguments.get("query") or ""))
    if name == "read_ananta_config":
        return _tool_read_ananta_config(str(arguments.get("section") or "chat"))
    if name == "get_hub_workers":
        return _tool_get_hub_workers()
    if name == "get_hub_sessions":
        return _tool_get_hub_sessions()
    if name == "get_hub_policies":
        return _tool_get_hub_policies()
    if name == "refresh_ui_guide":
        return _tool_refresh_ui_guide()
    return f"(Unbekanntes Tool: {name})"


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_ananta_config_tool_loop(
    *,
    messages: list[dict],
    provider: str,
    model: str | None,
    api_base: str | None = None,
    max_tool_calls: int = 8,
    timeout: int = 120,
    cancel_event: Any | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Tool-calling loop for the Ananta-Konfig session.

    Args:
        messages: Full message list [system, ...history, user].
        provider: LLM provider name.
        model: Model ID (or None for provider default).
        api_base: Override base URL.
        max_tool_calls: Hard limit on tool calls.
        timeout: Per-call timeout in seconds.
        cancel_event: Optional cancellation event.

    Returns:
        (final_answer_text, trace_dict)
    """
    import requests
    from agent.llm_integration import _runtime_api_key, _runtime_provider_urls

    trace: dict[str, Any] = {
        "mode": "ananta_config_tool_loop",
        "tool_calls_made": 0,
        "tools_used": [],
    }

    urls = _runtime_provider_urls()
    base_url = api_base or str(urls.get(provider) or "").rstrip("/")
    api_key = _runtime_api_key(provider)

    if not base_url:
        trace["error"] = f"no_url_for_provider:{provider}"
        return "", trace

    endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    current_messages = list(messages)
    tool_call_count = 0
    last_content = ""

    for _iteration in range(max_tool_calls + 2):
        if is_chat_cancelled(cancel_event):
            trace["cancelled"] = True
            break

        use_tools = tool_call_count < max_tool_calls
        payload: dict[str, Any] = {
            "model": model or "default",
            "messages": current_messages,
            "temperature": 0.3,
        }
        if use_tools:
            payload["tools"] = _CONFIG_TOOLS
            payload["tool_choice"] = "auto"

        try:
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            _log.warning("ananta_config_tool_loop LLM call failed: %s", exc)
            trace["error"] = str(exc)
            break

        choices = data.get("choices") or []
        if not choices:
            break

        msg = choices[0].get("message") or {}
        finish_reason = choices[0].get("finish_reason", "")
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []

        if content:
            last_content = content

        if not tool_calls or finish_reason == "stop":
            break

        # append assistant turn with tool_calls
        current_messages.append({
            "role": "assistant",
            "content": content or None,
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            tc_id = tc.get("id") or f"call_{tool_call_count}"
            fn = tc.get("function") or {}
            fn_name = fn.get("name") or ""
            try:
                fn_args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                fn_args = {}

            tool_call_count += 1
            trace["tool_calls_made"] = tool_call_count
            if fn_name not in trace["tools_used"]:
                trace["tools_used"].append(fn_name)

            _log.debug("ananta_config_tool: %s(%s)", fn_name, fn_args)
            result = _dispatch_tool(fn_name, fn_args)

            current_messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result,
            })

        if tool_call_count >= max_tool_calls:
            continue  # next iteration without tools → forces final answer

    return last_content, trace
