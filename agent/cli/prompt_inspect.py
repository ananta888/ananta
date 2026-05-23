"""CLI commands for prompt trace inspection. PTI-019, PTI-020, PTI-021, PTI-022, PTI-023."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def _get_trace_svc():
    from agent.services.prompt_trace_service import get_prompt_trace_service
    return get_prompt_trace_service()


def _api_request(method: str, path: str, *, params: dict | None = None, timeout: int = 30):
    from agent.cli_goals import _request
    try:
        return _request(method, path, params=params, timeout=timeout)
    except SystemExit:
        return None


def _api_data(response) -> dict:
    from agent.cli_goals import _api_data
    if response is None:
        return {}
    data = _api_data(response)
    return data if isinstance(data, dict) else {}


def _print_table(rows: list[dict], columns: list[str]) -> None:
    widths = {col: len(col) for col in columns}
    for row in rows:
        for col in columns:
            widths[col] = max(widths[col], len(str(row.get(col) or "")))
    header = "  ".join(col.ljust(widths[col]) for col in columns)
    sep = "  ".join("-" * widths[col] for col in columns)
    print(header)
    print(sep)
    for row in rows:
        print("  ".join(str(row.get(col) or "").ljust(widths[col]) for col in columns))


def _format_ts(ts: float | None) -> str:
    if not ts:
        return ""
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _load_llm_log_entries(limit: int = 2000) -> list[dict[str, Any]]:
    try:
        from agent.utils import get_data_dir
        log_path = os.path.join(get_data_dir(), "llm_log.jsonl")
        if not os.path.exists(log_path):
            return []
        rows: list[dict[str, Any]] = []
        with open(log_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        if limit > 0:
            rows = rows[-limit:]
        return rows
    except Exception:
        return []


def _latest_llm_response_by_request_id(limit: int = 2000) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for entry in _load_llm_log_entries(limit=limit):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("event") or "") != "llm_call_end":
            continue
        request_id = str(entry.get("request_id") or "").strip()
        if not request_id:
            continue
        current = latest.get(request_id)
        current_ts = float((current or {}).get("timestamp") or 0.0)
        entry_ts = float(entry.get("timestamp") or 0.0)
        if current is None or entry_ts >= current_ts:
            latest[request_id] = dict(entry)
    return latest


def _is_propose_like_request_kind(kind: str) -> bool:
    normalized = str(kind or "").strip().lower()
    return normalized in {
        "propose",
        "task_propose",
        "generate",
        "repair",
        "proposal",
    }


def _infer_task_executor(task: dict[str, Any], task_detail: dict[str, Any], last_trace: dict[str, Any]) -> str:
    provider = str(last_trace.get("provider") or "").strip().lower()
    model = str(last_trace.get("model") or "").strip().lower()
    if provider:
        if "opencode" in provider:
            return "opencode"
        if provider in {"lmstudio", "ollama", "openai", "anthropic"}:
            return provider
    if "opencode" in model:
        return "opencode"
    history = list(task_detail.get("history") or task.get("history") or [])
    for item in reversed(history):
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or "")
        actor = str(item.get("actor") or "")
        reason_codes = [str(code).lower() for code in list(item.get("reason_codes") or [])]
        if "opencode" in command.lower() or "opencode" in actor.lower():
            return "opencode"
        if "artifact_manifest_verified" in reason_codes:
            return "deterministic(policy)"
    if str(task.get("status") or "").lower() == "completed":
        return "deterministic/unknown"
    return "unknown"


def _extract_last_event(task: dict[str, Any], task_detail: dict[str, Any]) -> tuple[str, str]:
    history = list(task_detail.get("history") or task.get("history") or [])
    if not history:
        return "-", "-"
    last = history[-1] if isinstance(history[-1], dict) else {}
    event = str(last.get("event") or last.get("action") or last.get("step") or "-")
    reason_codes = [str(code) for code in list(last.get("reason_codes") or [])]
    reason = ",".join(reason_codes[:3]) if reason_codes else str(last.get("reason") or last.get("message") or "-")
    return event, (reason or "-")


def _collect_goal_runtime_view(
    goal_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    from agent.cli_goals import _request, _api_data

    goal_res = _request("GET", f"/goals/{goal_id}/detail", timeout=30)
    if goal_res.status_code != 200:
        raise RuntimeError(f"goal detail request failed ({goal_res.status_code})")
    goal_detail = _api_data(goal_res) or {}
    if not isinstance(goal_detail, dict):
        goal_detail = {}

    trace_res = _request("GET", f"/goals/{goal_id}/prompt-traces", params={"limit": 400}, timeout=30)
    trace_payload = _api_data(trace_res) if trace_res.status_code == 200 else {}
    if not isinstance(trace_payload, dict):
        trace_payload = {}
    traces_grouped = dict(trace_payload.get("traces") or {})

    tasks = [t for t in list(goal_detail.get("tasks") or []) if isinstance(t, dict)]
    task_details: dict[str, Any] = {}
    for task in tasks:
        tid = str(task.get("id") or "").strip()
        if not tid:
            continue
        t_res = _request("GET", f"/tasks/{tid}", timeout=20)
        if t_res.status_code == 200:
            task_details[tid] = _api_data(t_res) or {}

    return goal_detail, tasks, traces_grouped, task_details


def _collect_runtime_artifacts(tasks: list[dict[str, Any]], task_details: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in tasks:
        tid = str((task or {}).get("id") or "").strip()
        if not tid:
            continue
        detail = dict(task_details.get(tid) or {})
        verification = detail.get("verification_status") or {}
        if not isinstance(verification, dict):
            verification = {}
        execution_artifacts = verification.get("execution_artifacts")
        if not isinstance(execution_artifacts, list):
            continue
        for idx, item in enumerate(execution_artifacts, start=1):
            if not isinstance(item, dict):
                continue
            artifact_id = str(item.get("artifact_id") or item.get("id") or "").strip() or f"{tid}-artifact-{idx:03d}"
            kind = str(item.get("kind") or "").strip() or "task_output"
            path = str(
                item.get("path")
                or item.get("name")
                or item.get("filename")
                or item.get("title")
                or item.get("workspace_relative_path")
                or ""
            ).strip()
            row = dict(item)
            row["artifact_id"] = artifact_id
            row.setdefault("id", artifact_id)
            row["kind"] = kind
            if path:
                row["path"] = path
            row.setdefault("task_id", tid)
            rows.append(row)
    return rows


def _worker_debug_request(
    worker_url: str,
    path: str,
    *,
    token: str | None = None,
    params: dict | None = None,
    timeout: int = 20,
) -> tuple[int, dict[str, Any]]:
    try:
        import requests
    except Exception:
        return 0, {"error": "requests_unavailable"}
    base = str(worker_url or "").strip().rstrip("/")
    if not base:
        return 0, {"error": "missing_worker_url"}
    endpoint = path if str(path).startswith("/") else f"/{path}"
    url = f"{base}{endpoint}"
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        res = requests.get(url, params=params or {}, headers=headers, timeout=timeout)
        payload = res.json() if res.content else {}
        if not isinstance(payload, dict):
            payload = {"raw": payload}
        return int(res.status_code), payload
    except Exception as exc:
        return 0, {"error": str(exc)}


# ── llm-log tail ─────────────────────────────────────────────────────────────

def cmd_llm_log_tail(args: argparse.Namespace) -> int:
    svc = _get_trace_svc()

    filters: dict[str, Any] = {}
    if getattr(args, "provider", None):
        filters["provider"] = args.provider
    if getattr(args, "model", None):
        filters["model"] = args.model
    if getattr(args, "goal_id", None):
        filters["goal_id"] = args.goal_id
    if getattr(args, "task_id", None):
        filters["task_id"] = args.task_id

    limit = getattr(args, "limit", 20) or 20
    traces = svc.list_traces(limit=limit, **filters)

    if not traces:
        # fallback: try llm_log.jsonl
        try:
            from agent.utils import get_data_dir
            log_path = os.path.join(get_data_dir(), "llm_log.jsonl")
            if not os.path.exists(log_path):
                print("No prompt traces or LLM log found.")
                return 0
            rows = []
            with open(log_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
            rows = rows[-limit:]
            if getattr(args, "json", False):
                print(json.dumps(rows, indent=2))
            else:
                for row in rows:
                    ts = _format_ts(row.get("timestamp"))
                    print(f"[{ts}] {row.get('event','')} provider={row.get('provider','')} model={row.get('model','')} success={row.get('success','')}")
            return 0
        except Exception as exc:
            print(f"No traces found. ({exc})")
            return 0

    if getattr(args, "json", False):
        print(json.dumps([t.to_dict() for t in traces], indent=2))
        return 0

    rows = []
    for t in traces:
        preview = ""
        if t.final_prompt_redacted:
            preview = t.final_prompt_redacted[:60].replace("\n", " ")
        rows.append({
            "trace_id": t.trace_id[:16],
            "ts": _format_ts(t.created_at),
            "provider": t.provider or "",
            "model": (t.model or "")[:20],
            "kind": t.request_kind or "",
            "ok": str(t.success),
            "ms": str(t.latency_ms or ""),
            "preview": preview,
        })
    _print_table(rows, ["ts", "provider", "model", "kind", "ok", "ms", "preview", "trace_id"])
    return 0


# ── prompt inspect ────────────────────────────────────────────────────────────

def cmd_prompt_inspect(args: argparse.Namespace) -> int:
    trace_id = getattr(args, "trace_id", None) or getattr(args, "request_id", None)
    if not trace_id:
        print("Error: --trace-id is required", file=sys.stderr)
        return 2

    svc = _get_trace_svc()
    trace = svc.get_trace(trace_id)
    if trace is None:
        # Fallback to hub endpoint for traces that only exist remotely.
        remote_res = _api_request("GET", f"/debug/llm-requests/{trace_id}", timeout=30)
        if remote_res is not None and remote_res.status_code == 200:
            data = _api_data(remote_res)
            if getattr(args, "json", False):
                print(json.dumps(data, indent=2))
                return 0
            print(f"=== Prompt Trace: {data.get('trace_id')} ===")
            print(f"Provider:       {data.get('provider')}")
            print(f"Model:          {data.get('model')}")
            print(f"Request Kind:   {data.get('request_kind')}")
            print(f"Source:         {data.get('source_component')}")
            print(f"Goal ID:        {data.get('goal_id') or '-'}")
            print(f"Task ID:        {data.get('task_id') or '-'}")
            print(f"Created:        {_format_ts(data.get('created_at'))}")
            print(f"Latency:        {data.get('latency_ms')}ms")
            print(f"Success:        {data.get('success')}")
            if data.get("error_type"):
                print(f"Error:          {data.get('error_type')}: {data.get('error_message')}")
            print()
            print("--- Prompt (redacted) ---")
            text = str(data.get("final_prompt_redacted") or "")
            if text and not getattr(args, "full", False):
                text = text[:1000] + ("..." if len(text) > 1000 else "")
            print(text)
            return 0
    if trace is None:
        print(f"Error: trace '{trace_id}' not found", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(trace.to_dict(), indent=2))
        return 0

    print(f"=== Prompt Trace: {trace.trace_id} ===")
    print(f"Provider:       {trace.provider}")
    print(f"Model:          {trace.model}")
    print(f"Request Kind:   {trace.request_kind}")
    print(f"Source:         {trace.source_component}")
    print(f"Goal ID:        {trace.goal_id or '-'}")
    print(f"Task ID:        {trace.task_id or '-'}")
    print(f"Created:        {_format_ts(trace.created_at)}")
    print(f"Latency:        {trace.latency_ms}ms")
    print(f"Success:        {trace.success}")
    if trace.error_type:
        print(f"Error:          {trace.error_type}: {trace.error_message}")
    print()
    print("--- Prompt (redacted) ---")
    if trace.final_prompt_redacted:
        text = trace.final_prompt_redacted
        if not getattr(args, "full", False):
            text = text[:1000] + ("..." if len(text) > 1000 else "")
        print(text)
    elif trace.messages_redacted:
        for msg in trace.messages_redacted:
            role = msg.get("role", "?")
            content = str(msg.get("content") or "")
            if not getattr(args, "full", False):
                content = content[:500] + ("..." if len(content) > 500 else "")
            print(f"[{role}] {content}")
    print()
    if trace.template_chain:
        print("--- Template Chain ---")
        for entry in trace.template_chain:
            applied = "✓" if entry.get("applied") else "✗"
            print(f"  {entry.get('order',0):02d} [{applied}] {entry.get('type','')} - {entry.get('name') or entry.get('id') or ''} v={entry.get('version','')}")
    if trace.usage:
        print()
        print("--- Usage ---")
        for k, v in trace.usage.items():
            print(f"  {k}: {v}")
    return 0


# ── prompt render ─────────────────────────────────────────────────────────────

def cmd_prompt_render(args: argparse.Namespace) -> int:
    mode = getattr(args, "mode", None) or "generic"
    goal = getattr(args, "goal", None) or "Test goal"
    language = getattr(args, "language", None) or "de"
    model_family = getattr(args, "model_family", None)
    context_file = getattr(args, "context_file", None)
    preferred_output_format = getattr(args, "preferred_output_format", None) or "json"
    save_trace = bool(getattr(args, "save_trace", False))

    context = None
    if context_file:
        try:
            with open(context_file) as f:
                context = f.read()
        except Exception as exc:
            print(f"Error reading context file: {exc}", file=sys.stderr)
            return 1

    try:
        from agent.services.planning_prompt_registry import get_planning_prompt_registry
        from agent.services.prompt_trace_service import get_prompt_trace_service, prompt_hash
        from agent.services.prompt_provenance import PromptProvenanceChain
        from agent.services.prompt_redaction_service import get_redaction_service

        registry = get_planning_prompt_registry()
        resolved = registry.resolve(
            goal=goal,
            context=context,
            mode=mode,
            language=language,
            model_family=model_family,
            preferred_output_format=preferred_output_format,
        )
    except Exception as exc:
        print(f"Render failed: {exc}", file=sys.stderr)
        return 1

    chain = PromptProvenanceChain()
    chain.add_planning_prompt(
        prompt_version_id=resolved.prompt_version_id,
        version=resolved.version,
        language=resolved.language,
        mode=resolved.mode,
        checksum=resolved.checksum,
        is_inline_fallback=resolved.is_inline_fallback,
    )
    p_hash = prompt_hash(resolved.prompt)
    chain.add_final_render(output_hash=p_hash)

    redaction_result = get_redaction_service().redact(resolved.prompt)

    if getattr(args, "json", False):
        print(json.dumps({
            "prompt_version_id": resolved.prompt_version_id,
            "version": resolved.version,
            "checksum": resolved.checksum,
            "prompt_hash_sha256": p_hash,
            "final_prompt_redacted": redaction_result.redacted_text,
            "template_chain": chain.to_list(),
            "secrets_detected": redaction_result.secrets_detected,
        }, indent=2))
    else:
        print(f"Prompt Version:  {resolved.prompt_version_id}")
        print(f"Version:         {resolved.version}")
        print(f"Checksum:        {resolved.checksum[:16]}")
        print(f"Hash:            {p_hash[:16] if p_hash else 'n/a'}")
        print()
        print("--- Rendered Prompt (redacted) ---")
        print(redaction_result.redacted_text)

    if save_trace:
        try:
            svc = get_prompt_trace_service()
            trace = svc.create_trace(
                source_component="cli_render",
                request_kind="dry_run",
                prompt=resolved.prompt,
                template_chain=chain.to_list(),
            )
            finalized = svc.finalize_trace(trace, success=None)
            svc.store(finalized)
            print(f"\nTrace saved: {finalized.trace_id}")
        except Exception as exc:
            print(f"Warning: could not save trace: {exc}", file=sys.stderr)

    return 0


# ── prompt goal-traces ────────────────────────────────────────────────────────

def cmd_prompt_goal_traces(args: argparse.Namespace) -> int:
    goal_id = getattr(args, "goal_id", None)
    if not goal_id:
        print("Error: --goal-id is required", file=sys.stderr)
        return 2

    svc = _get_trace_svc()
    traces = svc.find_by_goal_id(goal_id)
    remote_grouped: dict[str, list[dict[str, Any]]] = {}
    if not traces:
        remote_res = _api_request("GET", f"/goals/{goal_id}/prompt-traces", params={"limit": 200}, timeout=30)
        if remote_res is not None and remote_res.status_code == 200:
            remote_payload = _api_data(remote_res)
            remote_grouped = dict(remote_payload.get("traces") or {})

    if getattr(args, "json", False):
        grouped: dict[str, list] = {}
        if traces:
            for t in traces:
                kind = t.request_kind or "unknown"
                grouped.setdefault(kind, []).append(t.to_dict())
        elif remote_grouped:
            grouped = remote_grouped
        print(json.dumps({"goal_id": goal_id, "traces": grouped}, indent=2))
        return 0

    if not traces and not remote_grouped:
        print(f"No traces found for goal {goal_id}")
        return 0

    if traces:
        grouped_display: dict[str, list] = {}
        for t in traces:
            kind = t.request_kind or "unknown"
            grouped_display.setdefault(kind, []).append(t)
        grouped_items = sorted(grouped_display.items())
    else:
        grouped_items = sorted(remote_grouped.items())

    for kind, group in grouped_items:
        print(f"\n=== {kind} ({len(group)}) ===")
        rows = []
        if traces:
            for t in group:
                preview = (t.final_prompt_redacted or "")[:60].replace("\n", " ")
                rows.append({
                    "ts": _format_ts(t.created_at),
                    "provider": t.provider or "",
                    "model": (t.model or "")[:20],
                    "ok": str(t.success),
                    "trace_id": t.trace_id[:16],
                    "preview": preview,
                })
        else:
            for item in group:
                preview = str(item.get("prompt_preview_redacted") or "")[:60].replace("\n", " ")
                rows.append({
                    "ts": _format_ts(item.get("created_at")),
                    "provider": str(item.get("provider") or ""),
                    "model": str(item.get("model") or "")[:20],
                    "ok": str(item.get("success")),
                    "trace_id": str(item.get("trace_id") or "")[:16],
                    "preview": preview,
                })
        _print_table(rows, ["ts", "provider", "model", "ok", "trace_id", "preview"])

    return 0


def cmd_prompt_goal_report(args: argparse.Namespace) -> int:
    goal_id = getattr(args, "goal_id", None)
    if not goal_id:
        print("Error: --goal-id is required", file=sys.stderr)
        return 2

    try:
        from agent.cli_goals import _request, _api_data
    except Exception as exc:
        print(f"Error: goal report helper unavailable: {exc}", file=sys.stderr)
        return 1

    goal_res = _request("GET", f"/goals/{goal_id}/detail", timeout=30)
    if goal_res.status_code != 200:
        print(f"Error: goal detail request failed ({goal_res.status_code})", file=sys.stderr)
        return 1
    goal_detail = _api_data(goal_res) or {}

    trace_res = _request("GET", f"/goals/{goal_id}/prompt-traces", params={"limit": 200}, timeout=30)
    trace_payload = _api_data(trace_res) if trace_res.status_code == 200 else {}
    if not isinstance(trace_payload, dict):
        trace_payload = {}

    goal_payload = goal_detail.get("goal") or {}
    tasks = [t for t in list(goal_detail.get("tasks") or []) if isinstance(t, dict)]
    task_details: dict[str, Any] = {}
    for task in tasks:
        tid = str(task.get("id") or "").strip()
        if not tid:
            continue
        t_res = _request("GET", f"/tasks/{tid}", timeout=20)
        if t_res.status_code == 200:
            task_details[tid] = _api_data(t_res) or {}
    artifacts = _collect_runtime_artifacts(tasks, task_details)
    traces_grouped = dict(trace_payload.get("traces") or {})

    result = {
        "goal_id": goal_id,
        "goal_status": goal_payload.get("status"),
        "goal_reason": goal_payload.get("last_status_reason"),
        "task_count": len(tasks),
        "tasks": [
            {
                "id": t.get("id"),
                "status": t.get("status"),
                "title": t.get("title"),
                "assigned_agent_url": t.get("assigned_agent_url"),
            }
            for t in tasks
        ],
        "prompt_trace_total": int(trace_payload.get("total") or 0),
        "prompt_traces": traces_grouped,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
        return 0

    print(f"=== Goal Report: {goal_id} ===")
    print(f"Status:        {result['goal_status']}")
    print(f"Reason:        {result['goal_reason'] or '-'}")
    print(f"Tasks:         {result['task_count']}")
    print(f"Prompt Traces: {result['prompt_trace_total']}")
    print(f"Artifacts:     {result['artifact_count']}")

    print("\n--- Tasks ---")
    if not result["tasks"]:
        print("(none)")
    else:
        rows = []
        for t in result["tasks"]:
            rows.append(
                {
                    "id": str(t.get("id") or "")[:16],
                    "status": str(t.get("status") or ""),
                    "title": str(t.get("title") or "")[:70],
                    "agent": str(t.get("assigned_agent_url") or "")[:36],
                }
            )
        _print_table(rows, ["id", "status", "title", "agent"])

    print("\n--- Prompt Traces ---")
    if result["prompt_trace_total"] <= 0:
        print("(none)")
    else:
        rows = []
        for kind, items in sorted((result["prompt_traces"] or {}).items()):
            for item in items:
                rows.append(
                    {
                        "kind": kind,
                        "trace_id": str(item.get("trace_id") or "")[:16],
                        "provider": str(item.get("provider") or ""),
                        "model": str(item.get("model") or "")[:24],
                        "ok": str(item.get("success")),
                        "ms": str(item.get("latency_ms") or ""),
                    }
                )
        _print_table(rows, ["kind", "provider", "model", "ok", "ms", "trace_id"])

    print("\n--- Artifacts ---")
    if not result["artifacts"]:
        print("(none)")
    else:
        rows = []
        for item in result["artifacts"]:
            rows.append(
                {
                    "id": str(item.get("id") or item.get("artifact_id") or "")[:16],
                    "kind": str(item.get("kind") or ""),
                    "task_id": str(item.get("task_id") or "")[:16],
                    "path": str(item.get("path") or item.get("name") or "")[:64],
                }
            )
        _print_table(rows, ["id", "kind", "task_id", "path"])
    return 0


def cmd_prompt_task_report(args: argparse.Namespace) -> int:
    task_id = str(getattr(args, "task_id", "") or "").strip()
    if not task_id:
        print("Error: --task-id is required", file=sys.stderr)
        return 2

    try:
        from agent.cli_goals import _request, _api_data
    except Exception as exc:
        print(f"Error: task report helper unavailable: {exc}", file=sys.stderr)
        return 1

    task_res = _request("GET", f"/tasks/{task_id}", timeout=30)
    task_detail = {}
    if task_res.status_code == 200:
        task_detail = _api_data(task_res) or {}
        if not isinstance(task_detail, dict):
            task_detail = {}
    else:
        traces_probe = _get_trace_svc().find_by_task_id(task_id)
        if task_res.status_code == 404 and not traces_probe:
            print(f"Error: invalid task id or not found: {task_id}", file=sys.stderr)
            return 1
        if task_res.status_code not in (200, 404):
            print(f"Warning: task detail request failed ({task_res.status_code}); continuing with traces only", file=sys.stderr)

    svc = _get_trace_svc()
    traces = list(svc.find_by_task_id(task_id))
    if not traces:
        trace_res = _request("GET", "/debug/llm-requests", params={"task_id": task_id, "limit": 200}, timeout=30)
        trace_payload = _api_data(trace_res) if trace_res.status_code == 200 else {}
        if isinstance(trace_payload, dict):
            traces = []
            for item in trace_payload.get("traces") or []:
                if isinstance(item, dict) and str(item.get("task_id") or "").strip() == task_id:
                    traces.append(item)

    response_by_request_id = _latest_llm_response_by_request_id()
    trace_rows: list[dict[str, Any]] = []
    for trace in sorted(
        traces,
        key=lambda t: float(getattr(t, "created_at", t.get("created_at") if isinstance(t, dict) else 0.0) or 0.0),
    ):
        if hasattr(trace, "to_dict"):
            trace_dict = trace.to_dict()
        else:
            trace_dict = dict(trace or {})
        request_id = str(trace_dict.get("request_id") or "").strip()
        response_entry = response_by_request_id.get(request_id) if request_id else {}
        response_text = str((response_entry or {}).get("response") or "")
        trace_rows.append(
            {
                "trace_id": str(trace_dict.get("trace_id") or "")[:16],
                "request_id": request_id[:16],
                "request_kind": str(trace_dict.get("request_kind") or ""),
                "provider": str(trace_dict.get("provider") or ""),
                "model": str(trace_dict.get("model") or ""),
                "success": str(trace_dict.get("success")),
                "created_at": _format_ts(trace_dict.get("created_at")),
                "prompt_preview_redacted": str(trace_dict.get("final_prompt_redacted") or trace_dict.get("prompt_preview_redacted") or "")[:120].replace("\n", " "),
                "response_preview": response_text[:160].replace("\n", " "),
                "response_hash": str(trace_dict.get("response_hash_sha256") or ""),
            }
        )

    instruction_layers = dict(task_detail.get("instruction_layers") or {}) if isinstance(task_detail, dict) else {}
    task_row = {
        "task_id": task_id,
        "title": str(task_detail.get("title") or ""),
        "status": str(task_detail.get("status") or ""),
        "task_kind": str(task_detail.get("task_kind") or ""),
        "assigned_agent_url": str(task_detail.get("assigned_agent_url") or ""),
        "required_capabilities": list(task_detail.get("required_capabilities") or []),
        "instruction_layers": {
            "selected_profile": instruction_layers.get("selected_profile"),
            "selected_overlay": instruction_layers.get("selected_overlay"),
            "template_compatibility": instruction_layers.get("template_compatibility"),
        },
    }
    result = {
        "task": task_row,
        "trace_count": len(trace_rows),
        "traces": trace_rows,
    }

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
        return 0

    print(f"=== Task Report: {task_id} ===")
    print(f"Title:         {task_row['title'] or '-'}")
    print(f"Status:        {task_row['status'] or '-'}")
    print(f"Task Kind:     {task_row['task_kind'] or '-'}")
    print(f"Agent:         {task_row['assigned_agent_url'] or '-'}")
    print(f"Traces:        {result['trace_count']}")
    if task_row["required_capabilities"]:
        print(f"Capabilities:  {', '.join(str(x) for x in task_row['required_capabilities'])}")
    if task_row["instruction_layers"]:
        print(f"Profile:       {task_row['instruction_layers'].get('selected_profile') or '-'}")
        print(f"Overlay:       {task_row['instruction_layers'].get('selected_overlay') or '-'}")

    if not trace_rows:
        print("\n(no prompt traces found)")
        return 0

    print("\n--- Prompt Traces ---")
    _print_table(
        trace_rows,
        ["created_at", "request_kind", "provider", "model", "success", "trace_id", "request_id", "response_hash"],
    )
    print("\n--- Prompt / Response Preview ---")
    for row in trace_rows:
        print(f"- {row['trace_id']} kind={row['request_kind'] or '-'} provider={row['provider'] or '-'} model={row['model'] or '-'}")
        print(f"  prompt={row['prompt_preview_redacted'] or '-'}")
        print(f"  response={row['response_preview'] or '-'}")
    return 0


def cmd_prompt_task_traces(args: argparse.Namespace) -> int:
    task_id = str(getattr(args, "task_id", "") or "").strip()
    goal_id = str(getattr(args, "goal_id", "") or "").strip()
    if not task_id:
        print("Error: --task-id is required", file=sys.stderr)
        return 2

    propose_only = bool(getattr(args, "propose_only", False))
    svc = _get_trace_svc()
    traces: list[dict[str, Any]] = []

    # Primary source: local prompt trace storage filtered by task_id.
    for item in svc.find_by_task_id(task_id):
        traces.append(item.to_dict() if hasattr(item, "to_dict") else dict(item or {}))

    # Optional source: goal-scoped endpoint when goal_id is provided.
    if goal_id:
        goal_trace_res = _api_request("GET", f"/goals/{goal_id}/prompt-traces", params={"limit": 400}, timeout=30)
        if goal_trace_res is not None and goal_trace_res.status_code == 200:
            payload = _api_data(goal_trace_res)
            grouped = dict(payload.get("traces") or {}) if isinstance(payload, dict) else {}
            for _kind, items in grouped.items():
                for item in list(items or []):
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("task_id") or "").strip() != task_id:
                        continue
                    traces.append(dict(item))

    # Fallback source: debug endpoint directly filtered by task.
    if not traces:
        debug_res = _api_request("GET", "/debug/llm-requests", params={"task_id": task_id, "limit": 400}, timeout=30)
        if debug_res is not None and debug_res.status_code == 200:
            payload = _api_data(debug_res)
            for item in list((payload.get("traces") if isinstance(payload, dict) else []) or []):
                if not isinstance(item, dict):
                    continue
                if str(item.get("task_id") or "").strip() == task_id:
                    traces.append(dict(item))

    # Deduplicate by trace_id/request_id while preserving newest created_at.
    dedup: dict[str, dict[str, Any]] = {}
    for row in traces:
        trace_key = str(row.get("trace_id") or row.get("request_id") or "").strip()
        if not trace_key:
            continue
        existing = dedup.get(trace_key)
        row_ts = float(row.get("created_at") or 0.0)
        existing_ts = float((existing or {}).get("created_at") or 0.0)
        if existing is None or row_ts >= existing_ts:
            dedup[trace_key] = row
    trace_rows = list(dedup.values())

    if propose_only:
        trace_rows = [row for row in trace_rows if _is_propose_like_request_kind(row.get("request_kind"))]

    trace_rows.sort(key=lambda row: float(row.get("created_at") or 0.0))

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "task_id": task_id,
                    "goal_id": goal_id or None,
                    "propose_only": propose_only,
                    "trace_count": len(trace_rows),
                    "traces": trace_rows,
                },
                indent=2,
            )
        )
        return 0

    print(f"=== Task Traces: {task_id} ===")
    if goal_id:
        print(f"Goal:          {goal_id}")
    print(f"Propose only:  {propose_only}")
    print(f"Trace count:   {len(trace_rows)}")
    if not trace_rows:
        print("\n(no traces found)")
        return 0

    rows: list[dict[str, Any]] = []
    for row in trace_rows:
        rows.append(
            {
                "created_at": _format_ts(row.get("created_at")),
                "request_kind": str(row.get("request_kind") or ""),
                "provider": str(row.get("provider") or ""),
                "model": str(row.get("model") or "")[:24],
                "ok": str(row.get("success")),
                "trace_id": str(row.get("trace_id") or "")[:16],
                "request_id": str(row.get("request_id") or "")[:16],
            }
        )
    print()
    _print_table(rows, ["created_at", "request_kind", "provider", "model", "ok", "trace_id", "request_id"])
    return 0


def _extract_learning_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    llm_cfg = payload.get("llm_configuration") if isinstance(payload.get("llm_configuration"), dict) else {}
    if isinstance(llm_cfg, dict) and isinstance(llm_cfg.get("planning_learning"), dict):
        return dict(llm_cfg.get("planning_learning") or {})
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    summary = settings.get("summary") if isinstance(settings.get("summary"), dict) else {}
    if isinstance(summary, dict) and isinstance(summary.get("planning_learning"), dict):
        return dict(summary.get("planning_learning") or {})
    return {}


def cmd_prompt_learning_report(args: argparse.Namespace) -> int:
    try:
        from agent.cli_goals import _request, _api_data
    except Exception as exc:
        print(f"Error: learning report helper unavailable: {exc}", file=sys.stderr)
        return 1

    dashboard_res = _request("GET", "/dashboard/read-model", params={"benchmark_task_kind": "analysis", "include_task_snapshot": 0}, timeout=30)
    dashboard_payload = _api_data(dashboard_res) if dashboard_res.status_code == 200 else {}
    if not isinstance(dashboard_payload, dict):
        dashboard_payload = {}

    snapshot = _extract_learning_snapshot(dashboard_payload)
    source = "dashboard_read_model"
    if not snapshot:
        assistant_res = _request("GET", "/assistant/read-model", timeout=30)
        assistant_payload = _api_data(assistant_res) if assistant_res.status_code == 200 else {}
        if isinstance(assistant_payload, dict):
            snapshot = _extract_learning_snapshot(assistant_payload)
            source = "assistant_read_model"

    if not snapshot:
        print("Error: planning learning snapshot not available", file=sys.stderr)
        return 1

    result = {
        "source": source,
        "enabled": bool(snapshot.get("enabled", False)),
        "policy": dict(snapshot.get("policy") or {}),
        "candidate_count": int(snapshot.get("candidate_count") or 0),
        "review_item_count": int(snapshot.get("review_item_count") or 0),
        "overview": dict(snapshot.get("overview") or {}),
        "profiles": list(snapshot.get("profiles") or []),
    }

    normalized_profiles: list[dict[str, Any]] = []
    for profile in result["profiles"]:
        if not isinstance(profile, dict):
            continue
        profile_copy = dict(profile)
        learning_state = dict(profile_copy.get("learning_state") or {})
        profile_copy.setdefault(
            "observed_output_shape",
            str(
                profile_copy.get("observed_output_shape")
                or learning_state.get("observed_output_shape")
                or learning_state.get("observed_output_format")
                or ""
            ),
        )
        profile_copy.setdefault(
            "observed_output_format",
            str(profile_copy.get("observed_output_format") or learning_state.get("observed_output_format") or ""),
        )
        profile_copy.setdefault(
            "preferred_output_format",
            str(
                profile_copy.get("preferred_output_format")
                or (result.get("overview") or {}).get("preferred_output_format", {}).get("value")
                or "",
            ),
        )
        normalized_profiles.append(profile_copy)
    result["profiles"] = normalized_profiles

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
        return 0

    print("=== Planning Learning Report ===")
    print(f"Source:        {result['source']}")
    print(f"Enabled:       {result['enabled']}")
    print(f"Candidates:    {result['candidate_count']}")
    print(f"Review Items:  {result['review_item_count']}")
    overview = result.get("overview") or {}
    if overview:
        print(f"Stable:        {overview.get('stable_profile_count', 0)}")
        print(f"Candidate:     {overview.get('candidate_profile_count', 0)}")
        print(f"Degraded:      {overview.get('degraded_profile_count', 0)}")
        preferred_shape = dict(overview.get("preferred_output_shape") or {})
        preferred_format = dict(overview.get("preferred_output_format") or {})
        if preferred_shape.get("value") or preferred_format.get("value"):
            print(f"Preferred Shp: {preferred_shape.get('value') or '-'} ({preferred_shape.get('state') or 'unknown'})")
            print(f"Preferred Fmt: {preferred_format.get('value') or '-'} ({preferred_format.get('state') or 'unknown'})")
    policy = result["policy"]
    if policy:
        print(f"Lookback Runs: {policy.get('lookback_runs', '-')}")
        print(f"Freeze Mins:   {policy.get('freeze_minutes', '-')}")
        print(f"Auto Activate: {policy.get('auto_activate', '-')}")

    profiles = result["profiles"]
    print("\n--- Profiles ---")
    if not profiles:
        print("(none)")
        return 0

    rows = []
    for profile in profiles:
        candidate = dict(profile.get("current_candidate") or {})
        freeze = dict(profile.get("freeze") or {})
        metrics = dict(profile.get("metrics") or {})
        rows.append(
            {
                "profile": str(profile.get("profile_name") or "")[:18],
                "enabled": str(bool(profile.get("enabled"))),
                "provider": str(profile.get("provider") or "")[:10],
                "model": str(profile.get("model_family") or profile.get("model_name_pattern") or "")[:18],
                "state": str((profile.get("learning_state") or {}).get("state") or "")[:10],
                "obs_shape": str((profile.get("learning_state") or {}).get("observed_output_shape") or (profile.get("learning_state") or {}).get("observed_output_format") or "")[:16],
                "obs_fmt": str((profile.get("learning_state") or {}).get("observed_output_format") or "")[:12],
                "pref_fmt": str(profile.get("preferred_output_format") or (overview.get("preferred_output_format") or {}).get("value") or "")[:12],
                "prompt": str(profile.get("active_prompt_version_id") or "")[:16],
                "quality": str(profile.get("current_quality_score") or "")[:8],
                "trend": str(profile.get("trend_direction") or "")[:10],
                "candidate": str(candidate.get("status") or "")[:10],
                "freeze": str(bool(freeze.get("active"))),
                "samples": str(metrics.get("run_count") or 0),
            }
        )
    _print_table(rows, ["profile", "enabled", "provider", "model", "state", "obs_shape", "obs_fmt", "pref_fmt", "prompt", "quality", "trend", "candidate", "freeze", "samples"])
    return 0


def _load_learning_snapshot_payload() -> dict[str, Any]:
    try:
        from agent.cli_goals import _request, _api_data
    except Exception:
        return {}
    dashboard_res = _request("GET", "/dashboard/read-model", params={"benchmark_task_kind": "analysis", "include_task_snapshot": 0}, timeout=30)
    dashboard_payload = _api_data(dashboard_res) if dashboard_res.status_code == 200 else {}
    if not isinstance(dashboard_payload, dict):
        dashboard_payload = {}
    snapshot = _extract_learning_snapshot(dashboard_payload)
    source = "dashboard_read_model"
    if not snapshot:
        assistant_res = _request("GET", "/assistant/read-model", timeout=30)
        assistant_payload = _api_data(assistant_res) if assistant_res.status_code == 200 else {}
        if isinstance(assistant_payload, dict):
            snapshot = _extract_learning_snapshot(assistant_payload)
            source = "assistant_read_model"
    if not snapshot:
        return {}
    return {
        "source": source,
        "enabled": bool(snapshot.get("enabled", False)),
        "policy": dict(snapshot.get("policy") or {}),
        "candidate_count": int(snapshot.get("candidate_count") or 0),
        "review_item_count": int(snapshot.get("review_item_count") or 0),
        "overview": dict(snapshot.get("overview") or {}),
        "profiles": list(snapshot.get("profiles") or []),
    }


def cmd_prompt_learning_status(args: argparse.Namespace) -> int:
    payload = _load_learning_snapshot_payload()
    if not payload:
        print("Error: planning learning snapshot not available", file=sys.stderr)
        return 1
    result = {
        "source": payload.get("source"),
        "enabled": bool(payload.get("enabled", False)),
        "policy": dict(payload.get("policy") or {}),
        "candidate_count": int(payload.get("candidate_count") or 0),
        "review_item_count": int(payload.get("review_item_count") or 0),
        "overview": dict(payload.get("overview") or {}),
    }
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
        return 0
    print("=== Planning Learning Status ===")
    print(f"Source:        {result['source']}")
    print(f"Enabled:       {result['enabled']}")
    print(f"Candidates:    {result['candidate_count']}")
    print(f"Review Items:  {result['review_item_count']}")
    policy = result.get("policy") or {}
    print(f"Interval Sec:  {policy.get('interval_seconds', '-')}")
    print(f"Lookback Runs: {policy.get('lookback_runs', '-')}")
    print(f"Auto Activate: {policy.get('auto_activate', '-')}")
    print(f"Freeze Mins:   {policy.get('freeze_minutes', '-')}")
    return 0


def cmd_prompt_planner_profiles(args: argparse.Namespace) -> int:
    payload = _load_learning_snapshot_payload()
    if not payload:
        print("Error: planning learning snapshot not available", file=sys.stderr)
        return 1
    provider_filter = str(getattr(args, "provider", "") or "").strip().lower()
    model_filter = str(getattr(args, "model", "") or "").strip().lower()
    profiles = [p for p in list(payload.get("profiles") or []) if isinstance(p, dict)]

    def _matches(profile: dict[str, Any]) -> bool:
        if provider_filter and str(profile.get("provider") or "").strip().lower() != provider_filter:
            return False
        model_blob = " ".join(
            [
                str(profile.get("model_family") or ""),
                str(profile.get("model_name_pattern") or ""),
                str(profile.get("profile_name") or ""),
            ]
        ).lower()
        if model_filter and model_filter not in model_blob:
            return False
        return True

    filtered = [p for p in profiles if _matches(p)]
    if getattr(args, "json", False):
        print(json.dumps({"source": payload.get("source"), "count": len(filtered), "profiles": filtered}, indent=2))
        return 0

    print("=== Planner Profiles ===")
    print(f"Source: {payload.get('source')}")
    print(f"Count:  {len(filtered)}")
    if not filtered:
        print("(none)")
        return 0
    rows = []
    for profile in filtered:
        learning_state = dict(profile.get("learning_state") or {})
        rows.append(
            {
                "profile": str(profile.get("profile_name") or "")[:22],
                "provider": str(profile.get("provider") or "")[:10],
                "model": str(profile.get("model_family") or profile.get("model_name_pattern") or "")[:22],
                "lang": str(profile.get("prompt_language") or "")[:4],
                "max_out": str(profile.get("max_output_tokens") or ""),
                "temp": str(profile.get("temperature") or ""),
                "repairs": str(profile.get("repair_attempts") or ""),
                "strict": str(profile.get("output_contract_strictness") or "")[:16],
                "state": str(learning_state.get("state") or "")[:10],
                "pref_prompt": str(profile.get("active_prompt_version_id") or profile.get("preferred_prompt_version_id") or "")[:18],
            }
        )
    _print_table(rows, ["profile", "provider", "model", "lang", "max_out", "temp", "repairs", "strict", "state", "pref_prompt"])
    return 0


def _last_trace_by_task_id(traces_grouped: dict[str, Any]) -> dict[str, dict[str, Any]]:
    last: dict[str, dict[str, Any]] = {}
    for kind, items in (traces_grouped or {}).items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id") or "").strip()
            if not task_id:
                continue
            created_at = float(item.get("created_at") or 0.0)
            existing = last.get(task_id)
            existing_ts = float((existing or {}).get("created_at") or 0.0)
            if (existing is None) or (created_at >= existing_ts):
                normalized = dict(item)
                normalized["request_kind"] = str(item.get("request_kind") or kind or "")
                last[task_id] = normalized
    return last


def cmd_prompt_delegation_report(args: argparse.Namespace) -> int:
    goal_id = str(getattr(args, "goal_id", "") or "").strip()
    if not goal_id:
        print("Error: --goal-id is required", file=sys.stderr)
        return 2

    try:
        from agent.cli_goals import _request, _api_data
    except Exception as exc:
        print(f"Error: delegation report helper unavailable: {exc}", file=sys.stderr)
        return 1

    goal_res = _request("GET", f"/goals/{goal_id}/detail", timeout=30)
    if goal_res.status_code != 200:
        if goal_res.status_code == 404:
            print(f"Error: invalid goal id or not found: {goal_id}", file=sys.stderr)
        else:
            print(f"Error: goal detail request failed ({goal_res.status_code})", file=sys.stderr)
        return 1
    goal_detail = _api_data(goal_res) or {}
    if not isinstance(goal_detail, dict):
        print("Error: invalid goal detail payload", file=sys.stderr)
        return 1

    trace_res = _request("GET", f"/goals/{goal_id}/prompt-traces", params={"limit": 300}, timeout=30)
    trace_payload = _api_data(trace_res) if trace_res.status_code == 200 else {}
    if not isinstance(trace_payload, dict):
        trace_payload = {}

    tasks = list(goal_detail.get("tasks") or [])
    traces_grouped = dict(trace_payload.get("traces") or {})
    task_last_trace = _last_trace_by_task_id(traces_grouped)

    rows: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        instruction_layers = dict(task.get("instruction_layers") or {})
        template_compat = dict(instruction_layers.get("template_compatibility") or {})
        role_ctx = dict(template_compat.get("role_template_context") or {})
        verification_status = dict(task.get("verification_status") or {})
        execution_scope = dict(verification_status.get("execution_scope") or {})
        worker_ctx = dict(task.get("worker_execution_context") or {})
        routing_hints = dict(worker_ctx.get("routing_hints") or {})

        task_id = str(task.get("id") or "")
        trace = dict(task_last_trace.get(task_id) or {})
        trace_compact = {
            "request_kind": str(trace.get("request_kind") or ""),
            "provider": str(trace.get("provider") or ""),
            "model": str(trace.get("model") or ""),
            "prompt_hash": str(trace.get("prompt_hash_sha256") or ""),
            "prompt_preview_redacted": str(trace.get("prompt_preview_redacted") or ""),
            "created_at": trace.get("created_at"),
        }
        row = {
            "task_id": task_id,
            "title": str(task.get("title") or ""),
            "status": str(task.get("status") or ""),
            "task_kind": str(task.get("task_kind") or ""),
            "required_capabilities": list(task.get("required_capabilities") or []),
            "assigned_agent_url": str(task.get("assigned_agent_url") or ""),
            "execution_worker_url": str(execution_scope.get("worker_url") or ""),
            "execution_profile_hint": str(routing_hints.get("worker_profile") or ""),
            "execution_profile_source": str(routing_hints.get("profile_source") or ""),
            "instruction_layers": {
                "selected_profile": instruction_layers.get("selected_profile"),
                "selected_overlay": instruction_layers.get("selected_overlay"),
                "template_compatibility": {
                    "status": template_compat.get("status"),
                    "role_template_context": {
                        "template_id": role_ctx.get("template_id"),
                        "template_name": role_ctx.get("template_name"),
                    },
                },
            },
            "last_prompt_trace": trace_compact,
        }
        rows.append(row)

    result = {
        "goal_id": goal_id,
        "goal_status": str((goal_detail.get("goal") or {}).get("status") or ""),
        "task_count": len(rows),
        "tasks": rows,
    }

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
        return 0

    print(f"=== Delegation Report: {goal_id} ===")
    print(f"Goal Status: {result['goal_status'] or '-'}")
    print(f"Tasks:       {result['task_count']}")
    if not rows:
        print("(no tasks)")
        return 0

    table_rows: list[dict[str, Any]] = []
    for row in rows:
        t = row["last_prompt_trace"]
        il = row["instruction_layers"]
        tc = il["template_compatibility"]
        rtc = tc["role_template_context"]
        table_rows.append(
            {
                "task_id": str(row.get("task_id") or "")[:14],
                "status": str(row.get("status") or "")[:14],
                "kind": str(row.get("task_kind") or "")[:10],
                "caps": ",".join([str(x) for x in list(row.get("required_capabilities") or [])])[:24],
                "agent": str(row.get("assigned_agent_url") or row.get("execution_worker_url") or "")[:34],
                "profile": str(il.get("selected_profile") or row.get("execution_profile_hint") or "")[:16],
                "overlay": str(il.get("selected_overlay") or "")[:16],
                "tpl_status": str(tc.get("status") or "")[:12],
                "template": str(rtc.get("template_name") or rtc.get("template_id") or "")[:24],
                "trace": str(t.get("request_kind") or "")[:8],
                "provider": str(t.get("provider") or "")[:10],
                "model": str(t.get("model") or "")[:16],
                "trace_at": _format_ts(t.get("created_at")),
            }
        )
    _print_table(
        table_rows,
        ["task_id", "status", "kind", "caps", "agent", "profile", "overlay", "tpl_status", "template", "trace", "provider", "model", "trace_at"],
    )

    print("\n--- Last Trace Preview By Task ---")
    for row in rows:
        task_id = str(row.get("task_id") or "")
        title = str(row.get("title") or "")
        t = row["last_prompt_trace"]
        preview = str(t.get("prompt_preview_redacted") or "").replace("\n", " ")
        if len(preview) > 180:
            preview = preview[:177] + "..."
        print(f"- {task_id} | {title}")
        print(f"  trace={t.get('request_kind') or '-'} provider={t.get('provider') or '-'} model={t.get('model') or '-'} hash={str(t.get('prompt_hash') or '')[:16]}")
        print(f"  preview={preview or '-'}")
    return 0


def cmd_prompt_goal_flows(args: argparse.Namespace) -> int:
    goal_id = str(getattr(args, "goal_id", "") or "").strip()
    if not goal_id:
        print("Error: --goal-id is required", file=sys.stderr)
        return 2
    try:
        goal_detail, tasks, traces_grouped, task_details = _collect_goal_runtime_view(goal_id)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    artifacts = list(((goal_detail.get("artifacts") or {}).get("artifacts") or []))
    artifacts_by_task: dict[str, int] = {}
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("task_id") or "").strip()
        if tid:
            artifacts_by_task[tid] = artifacts_by_task.get(tid, 0) + 1
    last_trace_by_task = _last_trace_by_task_id(traces_grouped)

    rows: list[dict[str, str]] = []
    for task in tasks:
        tid = str(task.get("id") or "")
        detail = dict(task_details.get(tid) or {})
        trace = dict(last_trace_by_task.get(tid) or {})
        executor = _infer_task_executor(task, detail, trace)
        event, reason = _extract_last_event(task, detail)
        propose_traces = 0
        for _kind, items in traces_grouped.items():
            for item in list(items or []):
                if not isinstance(item, dict):
                    continue
                if str(item.get("task_id") or "").strip() != tid:
                    continue
                if _is_propose_like_request_kind(str(item.get("request_kind") or "")):
                    propose_traces += 1
        rows.append(
            {
                "task_id": tid[:14],
                "status": str(task.get("status") or "")[:12],
                "executor": executor[:24],
                "propose": str(propose_traces),
                "artifacts": str(artifacts_by_task.get(tid, 0)),
                "event": event[:24],
                "reason": reason[:38],
            }
        )

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "goal_id": goal_id,
                    "goal_status": str((goal_detail.get("goal") or {}).get("status") or ""),
                    "task_count": len(tasks),
                    "rows": rows,
                },
                indent=2,
            )
        )
        return 0

    print(f"=== Goal Flows: {goal_id} ===")
    print(f"Status: {str((goal_detail.get('goal') or {}).get('status') or '-')}")
    print(f"Tasks:  {len(tasks)}")
    if not rows:
        print("(none)")
        return 0
    print()
    _print_table(rows, ["task_id", "status", "executor", "propose", "artifacts", "event", "reason"])
    return 0


def cmd_prompt_task_why(args: argparse.Namespace) -> int:
    task_id = str(getattr(args, "task_id", "") or "").strip()
    if not task_id:
        print("Error: --task-id is required", file=sys.stderr)
        return 2
    try:
        from agent.cli_goals import _request, _api_data
    except Exception as exc:
        print(f"Error: task why helper unavailable: {exc}", file=sys.stderr)
        return 1
    res = _request("GET", f"/tasks/{task_id}", timeout=30)
    if res.status_code != 200:
        print(f"Error: task request failed ({res.status_code})", file=sys.stderr)
        return 1
    task = _api_data(res) or {}
    if not isinstance(task, dict):
        task = {}
    history = [item for item in list(task.get("history") or []) if isinstance(item, dict)]
    last = history[-1] if history else {}
    reason_codes = [str(code) for code in list(last.get("reason_codes") or [])]
    result = {
        "task_id": task_id,
        "status": str(task.get("status") or ""),
        "last_event": str(last.get("event") or last.get("action") or last.get("step") or ""),
        "reason_codes": reason_codes,
        "reason": str(last.get("reason") or last.get("message") or ""),
        "actor": str(last.get("actor") or ""),
        "command": str(last.get("command") or ""),
    }
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
        return 0
    print(f"=== Task Why: {task_id} ===")
    print(f"Status:      {result['status'] or '-'}")
    print(f"Last Event:  {result['last_event'] or '-'}")
    print(f"Actor:       {result['actor'] or '-'}")
    print(f"ReasonCodes: {', '.join(result['reason_codes']) if result['reason_codes'] else '-'}")
    print(f"Reason:      {result['reason'] or '-'}")
    if result["command"]:
        print(f"Command:     {result['command'][:220]}")
    return 0


def cmd_prompt_goal_stuck(args: argparse.Namespace) -> int:
    goal_id = str(getattr(args, "goal_id", "") or "").strip()
    if not goal_id:
        print("Error: --goal-id is required", file=sys.stderr)
        return 2
    min_minutes = max(1, int(getattr(args, "minutes", 10) or 10))
    now = time.time()
    try:
        goal_detail, tasks, traces_grouped, task_details = _collect_goal_runtime_view(goal_id)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    last_trace_by_task = _last_trace_by_task_id(traces_grouped)
    rows: list[dict[str, str]] = []
    for task in tasks:
        status = str(task.get("status") or "").lower()
        if status not in {"proposing", "assigned", "in_progress"}:
            continue
        tid = str(task.get("id") or "")
        detail = dict(task_details.get(tid) or {})
        updated_at = float(detail.get("updated_at") or task.get("updated_at") or task.get("created_at") or 0.0)
        age_min = int((now - updated_at) / 60) if updated_at > 0 else -1
        if age_min >= 0 and age_min < min_minutes:
            continue
        trace = dict(last_trace_by_task.get(tid) or {})
        event, reason = _extract_last_event(task, detail)
        rows.append(
            {
                "task_id": tid[:14],
                "status": status[:12],
                "age_min": str(age_min if age_min >= 0 else "?"),
                "executor": _infer_task_executor(task, detail, trace)[:22],
                "trace": str(trace.get("request_kind") or "-")[:12],
                "event": event[:20],
                "reason": reason[:36],
            }
        )
    if getattr(args, "json", False):
        print(json.dumps({"goal_id": goal_id, "min_minutes": min_minutes, "rows": rows}, indent=2))
        return 0
    print(f"=== Goal Stuck: {goal_id} (>{min_minutes}m) ===")
    if not rows:
        print("No stuck tasks found.")
        return 0
    _print_table(rows, ["task_id", "status", "age_min", "executor", "trace", "event", "reason"])
    return 0


def cmd_prompt_goal_execmap(args: argparse.Namespace) -> int:
    goal_id = str(getattr(args, "goal_id", "") or "").strip()
    if not goal_id:
        print("Error: --goal-id is required", file=sys.stderr)
        return 2
    try:
        goal_detail, tasks, traces_grouped, task_details = _collect_goal_runtime_view(goal_id)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    last_trace_by_task = _last_trace_by_task_id(traces_grouped)
    buckets: dict[str, int] = {}
    for task in tasks:
        tid = str(task.get("id") or "")
        detail = dict(task_details.get(tid) or {})
        trace = dict(last_trace_by_task.get(tid) or {})
        executor = _infer_task_executor(task, detail, trace)
        buckets[executor] = buckets.get(executor, 0) + 1
    rows = [{"executor": key, "tasks": str(val)} for key, val in sorted(buckets.items(), key=lambda item: (-item[1], item[0]))]
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "goal_id": goal_id,
                    "goal_status": str((goal_detail.get("goal") or {}).get("status") or ""),
                    "task_count": len(tasks),
                    "executors": rows,
                },
                indent=2,
            )
        )
        return 0
    print(f"=== Goal Execmap: {goal_id} ===")
    print(f"Status: {str((goal_detail.get('goal') or {}).get('status') or '-')}")
    print(f"Tasks:  {len(tasks)}")
    if not rows:
        print("(none)")
        return 0
    print()
    _print_table(rows, ["executor", "tasks"])
    return 0


def cmd_prompt_artifact_provenance(args: argparse.Namespace) -> int:
    goal_id = str(getattr(args, "goal_id", "") or "").strip()
    if not goal_id:
        print("Error: --goal-id is required", file=sys.stderr)
        return 2
    try:
        goal_detail, tasks, traces_grouped, task_details = _collect_goal_runtime_view(goal_id)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    artifacts = _collect_runtime_artifacts(tasks, task_details)

    # goal-level traces overview (compact)
    trace_rows: list[dict[str, Any]] = []
    for kind, items in sorted(traces_grouped.items()):
        for item in list(items or []):
            if not isinstance(item, dict):
                continue
            trace_rows.append(
                {
                    "trace_id": str(item.get("trace_id") or ""),
                    "kind": kind,
                    "provider": str(item.get("provider") or ""),
                    "model": str(item.get("model") or ""),
                    "ok": item.get("success"),
                    "created_at": item.get("created_at"),
                }
            )

    rows: list[dict[str, Any]] = []
    for idx, artifact in enumerate(artifacts, start=1):
        task_id = str(artifact.get("task_id") or "").strip()
        task = dict(task_details.get(task_id) or {})
        verification = task.get("verification_status") or {}
        if not isinstance(verification, dict):
            verification = {}
        scope = verification.get("execution_scope") or {}
        if not isinstance(scope, dict):
            scope = {}
        provenance = verification.get("execution_provenance") or {}
        if not isinstance(provenance, dict):
            provenance = {}
        llm_diag = verification.get("llm_diagnostics") or {}
        if not isinstance(llm_diag, dict):
            llm_diag = {}

        rows.append(
            {
                "row": idx,
                "artifact_id": str(artifact.get("id") or artifact.get("artifact_id") or ""),
                "artifact_kind": str(artifact.get("kind") or ""),
                "artifact_path": str(artifact.get("path") or artifact.get("name") or ""),
                "task_id": task_id,
                "task_status": str(task.get("status") or ""),
                "task_kind": str(task.get("task_kind") or ""),
                "task_title": str(task.get("title") or ""),
                "execution_mode": str(provenance.get("execution_mode") or ""),
                "worker_url": str(scope.get("worker_url") or ""),
                "workspace_id": str(scope.get("workspace_id") or ""),
                "lease_id": str(scope.get("lease_id") or ""),
                "executor_container": str(scope.get("executor_container") or ""),
                "llm_inference_provider": str(llm_diag.get("inference_provider") or ""),
                "llm_inference_model": str(llm_diag.get("inference_model") or ""),
                "llm_propose_backend": str(llm_diag.get("propose_backend") or ""),
                "llm_propose_model": str(llm_diag.get("propose_model") or ""),
                "current_worker_job_id": str(task.get("current_worker_job_id") or ""),
            }
        )

    payload = {
        "schema": "artifact_provenance_matrix.v1",
        "generated_at": time.time(),
        "goal_id": goal_id,
        "goal_status": str((goal_detail.get("goal") or {}).get("status") or ""),
        "task_count": len(tasks),
        "artifact_count": len(artifacts),
        "prompt_trace_count": len(trace_rows),
        "prompt_traces": trace_rows,
        "matrix": rows,
    }

    out_path_raw = str(getattr(args, "out", "") or "").strip()
    out_written: str | None = None
    if out_path_raw:
        out_path = Path(out_path_raw).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        out_written = str(out_path)

        if bool(getattr(args, "with_md", False)):
            md_path = out_path.with_suffix(".md")
            lines = [
                "# Artifact Provenance Matrix",
                "",
                f"- goal_id: `{goal_id}`",
                f"- goal_status: `{payload['goal_status'] or '-'}`",
                f"- generated_at: `{_format_ts(float(payload.get('generated_at') or 0.0))}`",
                f"- tasks: `{payload['task_count']}` | artifacts: `{payload['artifact_count']}` | prompt_traces: `{payload['prompt_trace_count']}`",
                "",
                "## Artifact Matrix",
                "",
                "| # | artifact_id | kind | task_id | task_status | task_kind | execution_mode | worker_url | workspace_id |",
                "|---:|---|---|---|---|---|---|---|---|",
            ]
            for row in rows:
                lines.append(
                    f"| {row.get('row') or ''} | {row.get('artifact_id') or ''} | {row.get('artifact_kind') or ''} | "
                    f"{row.get('task_id') or ''} | {row.get('task_status') or ''} | {row.get('task_kind') or ''} | "
                    f"{row.get('execution_mode') or ''} | {row.get('worker_url') or ''} | {row.get('workspace_id') or ''} |"
                )
            md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
        if out_written:
            print(json.dumps({"written": out_written}))
        return 0

    print(f"=== Artifact Provenance: {goal_id} ===")
    print(f"Status:         {payload['goal_status'] or '-'}")
    print(f"Tasks:          {payload['task_count']}")
    print(f"Artifacts:      {payload['artifact_count']}")
    print(f"Prompt Traces:  {payload['prompt_trace_count']}")
    if not rows:
        print("(no artifacts)")
        return 0
    print()
    table_rows = []
    for row in rows:
        table_rows.append(
            {
                "#": str(row.get("row") or ""),
                "artifact_id": str(row.get("artifact_id") or "")[:16],
                "kind": str(row.get("artifact_kind") or "")[:12],
                "task_id": str(row.get("task_id") or "")[:14],
                "status": str(row.get("task_status") or "")[:12],
                "mode": str(row.get("execution_mode") or "")[:20],
                "worker": str(row.get("worker_url") or "")[:34],
                "workspace": str(row.get("workspace_id") or "")[:18],
            }
        )
    _print_table(table_rows, ["#", "artifact_id", "kind", "task_id", "status", "mode", "worker", "workspace"])
    if out_written:
        print(f"\nSaved JSON: {out_written}")
        if bool(getattr(args, "with_md", False)):
            print(f"Saved Markdown: {str(Path(out_written).with_suffix('.md'))}")
    return 0


def cmd_prompt_goal_worker_traces(args: argparse.Namespace) -> int:
    goal_id = str(getattr(args, "goal_id", "") or "").strip()
    if not goal_id:
        print("Error: --goal-id is required", file=sys.stderr)
        return 2
    propose_only = bool(getattr(args, "propose_only", False))
    include_full = bool(getattr(args, "full", False))
    limit = int(getattr(args, "limit", 80) or 80)
    try:
        goal_detail, tasks, _traces_grouped, task_details = _collect_goal_runtime_view(goal_id)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    workers: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for task in tasks:
        tid = str(task.get("id") or "").strip()
        if not tid:
            continue
        detail = dict(task_details.get(tid) or {})
        worker_url = str(detail.get("assigned_agent_url") or task.get("assigned_agent_url") or "").strip()
        worker_token = str(detail.get("assigned_agent_token") or "").strip() or None
        if not worker_url:
            continue
        if worker_url not in workers:
            workers[worker_url] = {"ok": 0, "fail": 0}
        status, payload = _worker_debug_request(
            worker_url,
            "/debug/llm-requests",
            token=worker_token,
            params={"task_id": tid, "limit": limit},
            timeout=25,
        )
        if status != 200:
            workers[worker_url]["fail"] += 1
            rows.append(
                {
                    "task_id": tid,
                    "task_status": str(task.get("status") or ""),
                    "worker_url": worker_url,
                    "error": str((payload or {}).get("message") or (payload or {}).get("error") or f"http_{status}"),
                    "traces": [],
                }
            )
            continue
        workers[worker_url]["ok"] += 1
        traces = list((payload.get("traces") if isinstance(payload, dict) else []) or [])
        trace_rows: list[dict[str, Any]] = []
        for item in traces:
            if not isinstance(item, dict):
                continue
            if str(item.get("task_id") or "").strip() != tid:
                continue
            if propose_only and not _is_propose_like_request_kind(str(item.get("request_kind") or "")):
                continue
            row = {
                "trace_id": str(item.get("trace_id") or ""),
                "request_id": str(item.get("request_id") or ""),
                "request_kind": str(item.get("request_kind") or ""),
                "provider": str(item.get("provider") or ""),
                "model": str(item.get("model") or ""),
                "success": item.get("success"),
                "latency_ms": item.get("latency_ms"),
                "created_at": item.get("created_at"),
                "prompt_preview_redacted": str(item.get("prompt_preview_redacted") or ""),
                "prompt_hash_sha256": str(item.get("prompt_hash_sha256") or ""),
                "response_hash_sha256": str(item.get("response_hash_sha256") or ""),
            }
            if include_full and row["trace_id"]:
                d_status, d_payload = _worker_debug_request(
                    worker_url,
                    f"/debug/llm-requests/{row['trace_id']}",
                    token=worker_token,
                    timeout=25,
                )
                if d_status == 200 and isinstance(d_payload, dict):
                    detail_payload = dict((d_payload.get("data") if isinstance(d_payload.get("data"), dict) else d_payload))
                    row["detail"] = {
                        "final_prompt_redacted": detail_payload.get("final_prompt_redacted"),
                        "messages_redacted": detail_payload.get("messages_redacted"),
                        "error_type": detail_payload.get("error_type"),
                        "error_message": detail_payload.get("error_message"),
                        "usage": detail_payload.get("usage"),
                    }
            trace_rows.append(row)

        trace_rows.sort(key=lambda item: float(item.get("created_at") or 0.0))
        rows.append(
            {
                "task_id": tid,
                "task_status": str(task.get("status") or ""),
                "task_title": str(task.get("title") or ""),
                "worker_url": worker_url,
                "error": None,
                "traces": trace_rows,
            }
        )

    total_traces = sum(len(item.get("traces") or []) for item in rows)
    payload = {
        "goal_id": goal_id,
        "goal_status": str((goal_detail.get("goal") or {}).get("status") or ""),
        "propose_only": propose_only,
        "full": include_full,
        "worker_count": len(workers),
        "workers": [{"worker_url": url, **stats} for url, stats in sorted(workers.items())],
        "task_count": len(tasks),
        "task_rows": rows,
        "trace_count": total_traces,
    }

    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
        return 0

    print(f"=== Goal Worker Traces: {goal_id} ===")
    print(f"Status:         {payload['goal_status'] or '-'}")
    print(f"Tasks:          {payload['task_count']}")
    print(f"Workers:        {payload['worker_count']}")
    print(f"Trace Count:    {payload['trace_count']}")
    print(f"Propose Only:   {propose_only}")
    print(f"Full Detail:    {include_full}")
    print()
    table_rows: list[dict[str, Any]] = []
    for item in rows:
        table_rows.append(
            {
                "task_id": str(item.get("task_id") or "")[:14],
                "status": str(item.get("task_status") or "")[:12],
                "worker": str(item.get("worker_url") or "")[:34],
                "traces": str(len(item.get("traces") or [])),
                "error": str(item.get("error") or "")[:36],
            }
        )
    _print_table(table_rows, ["task_id", "status", "worker", "traces", "error"])
    print("\n--- Trace Preview ---")
    for item in rows:
        tid = str(item.get("task_id") or "")
        for tr in list(item.get("traces") or [])[:5]:
            print(
                f"- {tid} {str(tr.get('trace_id') or '')[:16]} kind={tr.get('request_kind') or '-'} "
                f"provider={tr.get('provider') or '-'} model={str(tr.get('model') or '')[:20]} ok={tr.get('success')}"
            )
            print(f"  prompt={str(tr.get('prompt_preview_redacted') or '-')[:180]}")
    return 0


# ── Subparser builder ─────────────────────────────────────────────────────────

def build_prompt_subparser(subparsers) -> None:
    prompt_p = subparsers.add_parser("prompt", help="Prompt trace inspection commands")
    prompt_sub = prompt_p.add_subparsers(dest="prompt_cmd")

    # inspect
    inspect_p = prompt_sub.add_parser("inspect", help="Show a specific prompt trace")
    inspect_p.add_argument("--trace-id", dest="trace_id", required=True, help="Trace ID to inspect")
    inspect_p.add_argument("--json", action="store_true", help="JSON output")
    inspect_p.add_argument("--raw", action="store_true", help="Request raw prompt (requires policy)")
    inspect_p.add_argument("--full", action="store_true", help="Show full prompt without truncation")

    # render
    render_p = prompt_sub.add_parser("render", help="Render a planning prompt without calling a provider")
    render_p.add_argument("--mode", default="generic", help="Planning mode (default: generic)")
    render_p.add_argument("--goal", default="Test goal", help="Goal text to render")
    render_p.add_argument("--language", default="de", help="Language (default: de)")
    render_p.add_argument("--model-family", dest="model_family", help="Model family hint")
    render_p.add_argument("--context-file", dest="context_file", help="Path to context file")
    render_p.add_argument("--preferred-output-format", dest="preferred_output_format", default="json")
    render_p.add_argument("--save-trace", dest="save_trace", action="store_true", help="Save dry_run trace")
    render_p.add_argument("--json", action="store_true", help="JSON output")

    # goal-traces
    gt_p = prompt_sub.add_parser("goal-traces", help="Show all traces for a goal")
    gt_p.add_argument("--goal-id", dest="goal_id", required=True, help="Goal ID")
    gt_p.add_argument("--json", action="store_true", help="JSON output")

    # goal-report
    gr_p = prompt_sub.add_parser("goal-report", help="Show tasks + prompt traces + artifacts for a goal")
    gr_p.add_argument("--goal-id", dest="goal_id", required=True, help="Goal ID")
    gr_p.add_argument("--json", action="store_true", help="JSON output")
    dr_p = prompt_sub.add_parser("delegation-report", help="Show compact task delegation/template view for a goal")
    dr_p.add_argument("--goal-id", dest="goal_id", required=True, help="Goal ID")
    dr_p.add_argument("--json", action="store_true", help="JSON output")
    tr_p = prompt_sub.add_parser("task-report", help="Show compact prompt/response view for a task")
    tr_p.add_argument("--task-id", dest="task_id", required=True, help="Task ID")
    tr_p.add_argument("--json", action="store_true", help="JSON output")
    tt_p = prompt_sub.add_parser("task-traces", help="Show all prompt traces for a task (optionally propose-only)")
    tt_p.add_argument("--task-id", dest="task_id", required=True, help="Task ID")
    tt_p.add_argument("--goal-id", dest="goal_id", default="", help="Optional goal ID to resolve traces via goal endpoint")
    tt_p.add_argument("--propose-only", dest="propose_only", action="store_true", help="Show only propose-like traces")
    tt_p.add_argument("--json", action="store_true", help="JSON output")
    ti_p = prompt_sub.add_parser("task-inspect", help="Alias for task-report")
    ti_p.add_argument("--task-id", dest="task_id", required=True, help="Task ID")
    ti_p.add_argument("--json", action="store_true", help="JSON output")
    lr_p = prompt_sub.add_parser("learning-report", help="Show planning learning loop snapshot")
    lr_p.add_argument("--json", action="store_true", help="JSON output")
    ls_p = prompt_sub.add_parser("learning-status", help="Show compact planning learning status")
    ls_p.add_argument("--json", action="store_true", help="JSON output")
    pp_p = prompt_sub.add_parser("planner-profiles", help="Show planning model profiles")
    pp_p.add_argument("--provider", default="", help="Filter by provider (e.g. lmstudio)")
    pp_p.add_argument("--model", default="", help="Filter by model/family/name pattern substring")
    pp_p.add_argument("--json", action="store_true", help="JSON output")
    gf_p = prompt_sub.add_parser("goal-flows", help="Compact per-task flow view with executor/propose/artifacts")
    gf_p.add_argument("--goal-id", dest="goal_id", required=True, help="Goal ID")
    gf_p.add_argument("--json", action="store_true", help="JSON output")
    tw_p = prompt_sub.add_parser("task-why", help="Show latest completion/transition reason for a task")
    tw_p.add_argument("--task-id", dest="task_id", required=True, help="Task ID")
    tw_p.add_argument("--json", action="store_true", help="JSON output")
    gs_p = prompt_sub.add_parser("goal-stuck", help="Show tasks that appear stuck in proposing/assigned/in_progress")
    gs_p.add_argument("--goal-id", dest="goal_id", required=True, help="Goal ID")
    gs_p.add_argument("--minutes", type=int, default=10, help="Minimum age in minutes (default: 10)")
    gs_p.add_argument("--json", action="store_true", help="JSON output")
    ge_p = prompt_sub.add_parser("goal-execmap", help="Group tasks by inferred executor")
    ge_p.add_argument("--goal-id", dest="goal_id", required=True, help="Goal ID")
    ge_p.add_argument("--json", action="store_true", help="JSON output")
    ap_p = prompt_sub.add_parser("artifact-provenance", help="Show artifact provenance matrix for a goal")
    ap_p.add_argument("--goal-id", dest="goal_id", required=True, help="Goal ID")
    ap_p.add_argument("--json", action="store_true", help="JSON output")
    ap_p.add_argument("--out", default="", help="Write JSON output to this path")
    ap_p.add_argument("--with-md", dest="with_md", action="store_true", help="Also write a Markdown table next to --out")
    ap_alias = prompt_sub.add_parser("goal-artifact-matrix", help="Alias for artifact-provenance")
    ap_alias.add_argument("--goal-id", dest="goal_id", required=True, help="Goal ID")
    ap_alias.add_argument("--json", action="store_true", help="JSON output")
    ap_alias.add_argument("--out", default="", help="Write JSON output to this path")
    ap_alias.add_argument("--with-md", dest="with_md", action="store_true", help="Also write a Markdown table next to --out")
    gwt_p = prompt_sub.add_parser("goal-worker-traces", help="Fetch worker-side prompt traces for all tasks in a goal")
    gwt_p.add_argument("--goal-id", dest="goal_id", required=True, help="Goal ID")
    gwt_p.add_argument("--propose-only", dest="propose_only", action="store_true", help="Show only propose-like traces")
    gwt_p.add_argument("--full", action="store_true", help="Also fetch per-trace detail from workers")
    gwt_p.add_argument("--limit", type=int, default=80, help="Per-task trace fetch limit (default: 80)")
    gwt_p.add_argument("--json", action="store_true", help="JSON output")


def build_llm_log_subparser(subparsers) -> None:
    llm_p = subparsers.add_parser("llm-log", help="LLM request log commands")
    llm_sub = llm_p.add_subparsers(dest="llm_log_cmd")

    tail_p = llm_sub.add_parser("tail", help="Show recent LLM requests")
    tail_p.add_argument("--limit", type=int, default=20, help="Number of entries (default: 20)")
    tail_p.add_argument("--provider", help="Filter by provider")
    tail_p.add_argument("--model", help="Filter by model")
    tail_p.add_argument("--goal-id", dest="goal_id", help="Filter by goal ID")
    tail_p.add_argument("--task-id", dest="task_id", help="Filter by task ID")
    tail_p.add_argument("--json", action="store_true", help="JSON output")


def run_prompt_command(args: argparse.Namespace) -> int:
    cmd = getattr(args, "prompt_cmd", None)
    if cmd == "inspect":
        return cmd_prompt_inspect(args)
    elif cmd == "render":
        return cmd_prompt_render(args)
    elif cmd == "goal-traces":
        return cmd_prompt_goal_traces(args)
    elif cmd == "goal-report":
        return cmd_prompt_goal_report(args)
    elif cmd == "delegation-report":
        return cmd_prompt_delegation_report(args)
    elif cmd == "task-report":
        return cmd_prompt_task_report(args)
    elif cmd == "task-traces":
        return cmd_prompt_task_traces(args)
    elif cmd == "task-inspect":
        return cmd_prompt_task_report(args)
    elif cmd == "learning-report":
        return cmd_prompt_learning_report(args)
    elif cmd == "learning-status":
        return cmd_prompt_learning_status(args)
    elif cmd == "planner-profiles":
        return cmd_prompt_planner_profiles(args)
    elif cmd == "goal-flows":
        return cmd_prompt_goal_flows(args)
    elif cmd == "task-why":
        return cmd_prompt_task_why(args)
    elif cmd == "goal-stuck":
        return cmd_prompt_goal_stuck(args)
    elif cmd == "goal-execmap":
        return cmd_prompt_goal_execmap(args)
    elif cmd == "artifact-provenance":
        return cmd_prompt_artifact_provenance(args)
    elif cmd == "goal-artifact-matrix":
        return cmd_prompt_artifact_provenance(args)
    elif cmd == "goal-worker-traces":
        return cmd_prompt_goal_worker_traces(args)
    else:
        print(
            "Usage: ananta prompt "
            "{inspect,render,goal-traces,goal-report,delegation-report,task-report,task-traces,task-inspect,"
            "learning-report,learning-status,planner-profiles,goal-flows,task-why,goal-stuck,goal-execmap,"
            "artifact-provenance,goal-artifact-matrix,goal-worker-traces} --help"
        )
        return 2


def run_llm_log_command(args: argparse.Namespace) -> int:
    cmd = getattr(args, "llm_log_cmd", None)
    if cmd == "tail":
        return cmd_llm_log_tail(args)
    else:
        print("Usage: ananta llm-log {tail} --help")
        return 2
