"""CLI commands for prompt trace inspection. PTI-019, PTI-020, PTI-021, PTI-022, PTI-023."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any


def _get_trace_svc():
    from agent.services.prompt_trace_service import get_prompt_trace_service
    return get_prompt_trace_service()


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

    if getattr(args, "json", False):
        grouped: dict[str, list] = {}
        for t in traces:
            kind = t.request_kind or "unknown"
            grouped.setdefault(kind, []).append(t.to_dict())
        print(json.dumps({"goal_id": goal_id, "traces": grouped}, indent=2))
        return 0

    if not traces:
        print(f"No traces found for goal {goal_id}")
        return 0

    grouped_display: dict[str, list] = {}
    for t in traces:
        kind = t.request_kind or "unknown"
        grouped_display.setdefault(kind, []).append(t)

    for kind, group in sorted(grouped_display.items()):
        print(f"\n=== {kind} ({len(group)}) ===")
        rows = []
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
        _print_table(rows, ["ts", "provider", "model", "ok", "trace_id", "preview"])

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
    else:
        print("Usage: ananta prompt {inspect,render,goal-traces} --help")
        return 2


def run_llm_log_command(args: argparse.Namespace) -> int:
    cmd = getattr(args, "llm_log_cmd", None)
    if cmd == "tail":
        return cmd_llm_log_tail(args)
    else:
        print("Usage: ananta llm-log {tail} --help")
        return 2
