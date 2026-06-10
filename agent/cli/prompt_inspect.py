"""CLI commands for prompt trace inspection. PTI-019, PTI-020, PTI-021, PTI-022, PTI-023."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from agent.cli.prompt_inspect_core import (
    _api_data,
    _api_request,
    _get_trace_svc,
    _is_propose_like_request_kind,
    _latest_llm_response_by_request_id,
    _load_llm_log_entries,
)
from agent.cli.prompt_inspect_formatters import _format_ts, _print_table


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
        if "opencode" in command.lower() or "opencode" in actor.lower():  # noqa: E501
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

    goal_res = _api_request("GET", f"/goals/{goal_id}/detail", timeout=30)
    if goal_res is None or goal_res.status_code != 200:
        raise RuntimeError(f"goal detail request failed ({goal_res.status_code if goal_res else 'N/A'})")
    goal_detail = _api_data(goal_res) or {}
    if not isinstance(goal_detail, dict):
        goal_detail = {}

    trace_res = _api_request("GET", f"/goals/{goal_id}/prompt-traces", params={"limit": 400}, timeout=30)
    trace_payload = _api_data(trace_res) if trace_res and trace_res.status_code == 200 else {}  # noqa: E501
    if not isinstance(trace_payload, dict):
        trace_payload = {}
    traces_grouped = dict(trace_payload.get("traces") or {})

    tasks = [t for t in list(goal_detail.get("tasks") or []) if isinstance(t, dict)]
    task_details: dict[str, Any] = {}
    for task in tasks:
        tid = str(task.get("id") or "").strip()
        if not tid:
            continue
        t_res = _api_request("GET", f"/tasks/{tid}", timeout=20)
        if t_res and t_res.status_code == 200:
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
        # Ensure execution_artifacts is a list, otherwise skip
        if not isinstance(execution_artifacts, list):
            continue
        for idx, item in enumerate(execution_artifacts, start=1):
            if not isinstance(item, dict):
                continue
            artifact_id = str(item.get("artifact_id") or item.get("id") or "").strip() or f"{tid}-artifact-{idx:03d}"  # noqa: E501
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
    except ImportError:
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
            with open(log_path, encoding="utf-8") as f:
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
                    print(f"[{ts}] {row.get('event','')} provider={row.get('provider','')} model={row.get('model','')} success={row.get('success','')}")  # noqa: E501
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
            print(f"  {entry.get('order',0):02d} [{applied}] {entry.get('type','')} - {entry.get('name') or entry.get('id') or ''} v={entry.get('version','')}")  # noqa: E501
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
            with open(context_file, encoding="utf-8") as f:
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
            remote_grouped = _api_data(remote_res) or {}
            # Flatten remote traces into local format
            remote_flat_traces = []
            for item in remote_grouped.get("traces", {}).values():
                for trace in item:
                    remote_flat_traces.append(trace)
            traces = [svc.create_trace(**t) for t in remote_flat_traces]

    if not traces:
        print(f"Error: no traces found for goal '{goal_id}'", file=sys.stderr)
        return 1

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


# ── prompt trace full ─────────────────────────────────────────────────────────


def cmd_prompt_trace_full(args: argparse.Namespace) -> int:
    trace_id = getattr(args, "trace_id", None)
    if not trace_id:
        print("Error: --trace-id is required", file=sys.stderr)
        return 2

    t = _get_trace_svc().get_trace(trace_id)
    if t is None:
        print(f"Error: trace '{trace_id}' not found", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(t.to_dict(), indent=2))
        return 0

    print(f"Trace ID:         {t.trace_id}")
    print(f"Created At:       {_format_ts(t.created_at)}")
    print(f"Source Component: {t.source_component}")
    print(f"Request Kind:     {t.request_kind}")
    print(f"Provider:         {t.provider}")
    print(f"Model:            {t.model}")
    print(f"Success:          {t.success}")
    if t.error_type:
        print(f"Error Type:       {t.error_type}")
        print(f"Error Message:    {t.error_message}")
    print(f"Latency (ms):     {t.latency_ms}")
    print("-" * 80)
    print("Prompt:")
    print(t.final_prompt_redacted)
    if t.response_content:
        print("-" * 80)
        print("Response:")
        print(t.response_content)
    if t.template_chain:
        print("-" * 80)
        print("Template Chain:")
        for ti in t.template_chain:
            print(f"  - {ti.get('type')}: {ti.get('name')}")
    if t.usage:
        print("-" * 80)
        print("Usage:")
        for k, v in t.usage.items():
            print(f"  {k}: {v}")
    if t.tool_calls:
        print("-" * 80)
        print("Tool Calls:")
        for idx, tc in enumerate(t.tool_calls):
            print(f"  Tool {idx}: {tc.get('name')}({tc.get('arguments')})")
    return 0


# ── main ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="CLI tool to inspect prompt traces, LLM logs, and goal runtimes."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # llm-log tail
    llm_log_parser = subparsers.add_parser(
        "llm-log-tail", help="Tail the LLM log (llm_log.jsonl) or list prompt traces."
    )
    llm_log_parser.add_argument("--limit", type=int, default=20, help="Number of entries to show.")
    llm_log_parser.add_argument("--json", action="store_true", help="Output as JSON.")
    llm_log_parser.add_argument("--provider", help="Filter by LLM provider (e.g., 'ollama', 'lmstudio').")
    llm_log_parser.add_argument("--model", help="Filter by LLM model (e.g., 'llama3', 'claude-sonnet').")
    llm_log_parser.add_argument("--goal-id", help="Filter by associated goal ID.")
    llm_log_parser.add_argument("--task-id", help="Filter by associated task ID.")
    llm_log_parser.set_defaults(func=cmd_llm_log_tail)

    # prompt inspect
    prompt_inspect_parser = subparsers.add_parser(
        "inspect", help="Inspect a specific prompt trace by its ID."
    )
    prompt_inspect_parser.add_argument("--trace-id", help="The ID of the prompt trace to inspect.")
    prompt_inspect_parser.add_argument("--request-id", help="Alias for --trace-id.")
    prompt_inspect_parser.add_argument("--json", action="store_true", help="Output as JSON.")
    prompt_inspect_parser.add_argument("--full", action="store_true", help="Show full prompt/response, no truncation.")
    prompt_inspect_parser.set_defaults(func=cmd_prompt_inspect)

    # prompt render
    prompt_render_parser = subparsers.add_parser(
        "render", help="Render a planning prompt without sending it to an LLM."
    )
    prompt_render_parser.add_argument("--mode", default="generic", help="Planning mode (e.g., 'new_software_project').")
    prompt_render_parser.add_argument("--goal", required=True, help="The high-level goal text.")
    prompt_render_parser.add_argument("--language", default="en", help="Target language (e.g., 'en', 'de').")
    prompt_render_parser.add_argument("--model-family", help="LLM family for context tuning (e.g., 'claude', 'gpt').")
    prompt_render_parser.add_argument("--context-file", help="Path to a file containing additional context.")
    prompt_render_parser.add_argument("--preferred-output-format", default="json", help="Expected output format (e.g., 'json', 'yaml').")  # noqa: E501
    prompt_render_parser.add_argument("--json", action="store_true", help="Output result as JSON.")
    prompt_render_parser.add_argument("--save-trace", action="store_true", help="Save the rendered prompt to the trace service.")  # noqa: E501
    prompt_render_parser.set_defaults(func=cmd_prompt_render)


    # goal traces
    goal_traces_parser = subparsers.add_parser(
        "goal-traces", help="List all prompt traces associated with a given goal ID remotely first, then locally."  # noqa: E501
    )
    goal_traces_parser.add_argument("--goal-id", required=True, help="The ID of the goal to list traces for.")
    goal_traces_parser.add_argument("--json", action="store_true", help="Output as JSON.")
    goal_traces_parser.set_defaults(func=cmd_prompt_goal_traces)

    # prompt trace full
    prompt_trace_full_parser = subparsers.add_parser(
        "trace-full", help="Show a raw, full prompt trace artifact from local storage."
    )
    prompt_trace_full_parser.add_argument("--trace-id", help="The ID of the prompt trace to inspect.")  # noqa: E501
    prompt_trace_full_parser.add_argument("--json", action="store_true", help="Output as JSON.")
    prompt_trace_full_parser.set_defaults(func=cmd_prompt_trace_full)


    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
