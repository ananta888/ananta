"""ananta runtime — runtime profile management commands."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

SUBCOMMANDS = ["list", "inspect", "recommend", "operations", "run"]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta runtime",
        description="List and inspect Ananta runtime profiles.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ananta runtime list\n"
            "  ananta runtime inspect developer-local\n"
            "  ananta runtime recommend\n"
            "  ananta runtime operations --health degraded\n"
            "  ananta runtime run <run-id>\n"
        ),
    )
    _configure_subparsers(p)
    return p


def _configure_subparsers(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="runtime_cmd", metavar="<action>")

    list_p = sub.add_parser("list", help="List all available runtime profiles.")
    list_p.add_argument("--json", action="store_true", help="Output as JSON.")

    inspect_p = sub.add_parser("inspect", help="Inspect one runtime profile.")
    inspect_p.add_argument("profile", help="Profile name (e.g. developer-local).")
    inspect_p.add_argument("--json", action="store_true", help="Output as JSON.")

    rec_p = sub.add_parser("recommend", help="Recommend a runtime profile for the current environment.")
    rec_p.add_argument("--json", action="store_true", help="Output as JSON.")
    rec_p.add_argument("--hardware", default="", help="Hint: laptop, workstation, server.")
    rec_p.add_argument("--use-case", default="", dest="use_case", help="Hint: dev, ci, demo, production.")

    operations_p = sub.add_parser(
        "operations",
        help="Read the authenticated Hub workflow-runtime operations projection.",
    )
    operations_p.add_argument("--runtime", default="")
    operations_p.add_argument("--mode", default="")
    operations_p.add_argument("--status", default="")
    operations_p.add_argument(
        "--health",
        choices=("healthy", "degraded", "stale", "parity_gap", "unverified"),
        default="",
    )
    operations_p.add_argument("--search", default="")
    operations_p.add_argument("--limit", type=int, default=100)
    operations_p.add_argument("--json", action="store_true", help="Output the unchanged Hub projection as JSON.")

    run_p = sub.add_parser("run", help="Inspect one workflow-runtime run through the Hub projection.")
    run_p.add_argument("run_id")
    run_p.add_argument("--json", action="store_true", help="Output the unchanged Hub run record as JSON.")

    llmi_p = sub.add_parser("llm-interceptor", help="Validate/start OpenAI-compatible interceptor.")
    llmi_p.add_argument("--config", required=True, help="Path to interceptor config JSON.")
    llmi_p.add_argument("--dry-run", action="store_true", help="Validate config and print startup summary only.")
    llmi_p.add_argument("--dev-fake-upstream", action="store_true", help="Run with fake upstream mode note (no secret output).")


def dispatch(argv: Sequence[str]) -> int:
    parser = _build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    try:
        parsed = parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    cmd = parsed.runtime_cmd
    if cmd == "list":
        return _cmd_list(parsed)
    if cmd == "inspect":
        return _cmd_inspect(parsed)
    if cmd == "recommend":
        return _cmd_recommend(parsed)
    if cmd == "operations":
        return _cmd_operations(parsed)
    if cmd == "run":
        return _cmd_run(parsed)
    if cmd == "llm-interceptor":
        return _cmd_llm_interceptor(parsed)
    parser.print_help()
    return 0
def register(subparsers: Any) -> None:
    p = subparsers.add_parser("runtime", help="List and inspect runtime profiles.")
    _configure_subparsers(p)
    p.set_defaults(_dispatch=dispatch)


# ── Implementations ────────────────────────────────────────────────────────────

def _get_catalog() -> dict:
    from agent.runtime_profiles import _RUNTIME_PROFILE_CATALOG
    return _RUNTIME_PROFILE_CATALOG


def _cmd_list(parsed) -> int:
    catalog = _get_catalog()
    if getattr(parsed, "json", False):
        out = [
            {"id": k, "label": v.get("label", k), "description": v.get("description", "")}
            for k, v in catalog.items()
        ]
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(f"{'PROFILE':<30} {'LABEL':<25} DESCRIPTION")
        print("-" * 90)
        for k, v in catalog.items():
            print(f"{k:<30} {v.get('label', ''):<25} {v.get('description', '')[:50]}")
    return 0


def _cmd_inspect(parsed) -> int:
    catalog = _get_catalog()
    pid = parsed.profile
    profile = catalog.get(pid)
    if profile is None:
        print(f"Error: Profile '{pid}' not found.", file=sys.stderr)
        print(f"Available: {', '.join(catalog)}")
        return 3
    if getattr(parsed, "json", False):
        print(json.dumps({"id": pid, **profile}, indent=2, ensure_ascii=False))
    else:
        print(f"Profile:      {pid}")
        print(f"Label:        {profile.get('label', '—')}")
        print(f"Description:  {profile.get('description', '—')}")
        print(f"Security:     {profile.get('security_posture', '—')}")
        print(f"Review mode:  {profile.get('review_mode', '—')}")
        print(f"Compose profiles: {profile.get('recommended_compose_profiles', [])}")
    return 0


def _cmd_recommend(parsed) -> int:
    import sys
    try:
        from agent.services.runtime_profile_recommender import (
            RuntimeRecommendationRequest,
            recommend_runtime_profile,
        )
        req = RuntimeRecommendationRequest(
            hardware_hint=getattr(parsed, "hardware", "") or None,
            use_case_hint=getattr(parsed, "use_case", "") or None,
        )
        rec = recommend_runtime_profile(req)
        if getattr(parsed, "json", False):
            print(json.dumps(rec.as_dict() if hasattr(rec, "as_dict") else vars(rec), indent=2))
        else:
            print(f"Recommended profile: {rec.profile_id}")
            if hasattr(rec, "reason"):
                print(f"Reason: {rec.reason}")
    except Exception as exc:
        print(f"Error: Could not run recommender: {exc}", file=sys.stderr)
        print("Fallback recommendation: developer-local")
        if getattr(parsed, "json", False):
            print(json.dumps({"profile_id": "developer-local", "reason": "fallback"}))
        else:
            print("Profile: developer-local")
    return 0


def _cmd_llm_interceptor(parsed) -> int:
    import sys

    from agent.services.llm_interceptor.config_schema import load_llm_interceptor_config
    from agent.services.llm_interceptor.openai_compat_server import (
        OpenAICompatInterceptorServer,
        run_interceptor_server,
    )

    try:
        cfg = load_llm_interceptor_config(parsed.config)
    except ValueError as exc:
        print(f"Error: invalid interceptor config: {exc}", file=sys.stderr)
        return 2

    server = OpenAICompatInterceptorServer(cfg)
    summary = server.startup_summary()
    upstream_ids = [item["id"] for item in summary["upstreams"]]
    print(
        f"LLM interceptor bind {summary['listen']['host']}:{summary['listen']['port']}{summary['listen']['prefix']} "
        f"upstreams={upstream_ids}"
    )
    if getattr(parsed, "dev_fake_upstream", False):
        print("Dev fake upstream mode requested (use test harness/fake upstream endpoint).")
    if getattr(parsed, "dry_run", False):
        return 0
    run_interceptor_server(cfg)
    return 0


def _cmd_operations(parsed) -> int:
    import sys

    from agent.cli.api_client import get_api_client
    from client_surfaces.common.workflow_runtime_projection import (
        WorkflowRuntimeProjectionError,
        operations_status_line,
        require_operations_list,
    )

    if not 1 <= int(parsed.limit) <= 500:
        print("Error: --limit must be between 1 and 500.", file=sys.stderr)
        return 2
    params = {
        key: value
        for key, value in {
            "runtime": parsed.runtime,
            "mode": parsed.mode,
            "status": parsed.status,
            "health": parsed.health,
            "search": parsed.search,
            "limit": parsed.limit,
        }.items()
        if value not in (None, "")
    }
    raw = get_api_client().get("/api/workflow-runtime/operations", params=params)
    try:
        payload = require_operations_list(raw)
    except WorkflowRuntimeProjectionError as exc:
        print(f"Error: workflow-runtime operations unavailable ({exc}).", file=sys.stderr)
        return 4
    if parsed.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(operations_status_line(payload))
    print(f"{'RUN':<28} {'RUNTIME':<12} {'MODE':<14} {'STATUS':<18} HEALTH")
    for run in payload["runs"]:
        health = "stale" if run.get("stale") else "degraded" if run.get("degraded") else "healthy"
        print(
            f"{str(run.get('run_id') or ''):<28.28} "
            f"{str(run.get('runtime') or ''):<12.12} "
            f"{str(run.get('mode') or ''):<14.14} "
            f"{str(run.get('outcome_claim') or run.get('status') or ''):<18.18} "
            f"{health}"
        )
    return 0


def _cmd_run(parsed) -> int:
    import sys
    from urllib.parse import quote

    from agent.cli.api_client import get_api_client
    from client_surfaces.common.workflow_runtime_projection import (
        WorkflowRuntimeProjectionError,
        require_operation_detail,
    )

    run_id = str(parsed.run_id or "")
    if not run_id or run_id != run_id.strip() or len(run_id) > 160:
        print("Error: run id is not canonical.", file=sys.stderr)
        return 2
    raw = get_api_client().get(f"/api/workflow-runtime/operations/runs/{quote(run_id, safe='')}")
    try:
        run = require_operation_detail(raw)
    except WorkflowRuntimeProjectionError as exc:
        print(f"Error: workflow-runtime run unavailable ({exc}).", file=sys.stderr)
        return 4
    if parsed.json:
        print(json.dumps(run, indent=2, ensure_ascii=False))
    else:
        print(f"Run:       {run.get('run_id')}")
        print(f"Runtime:   {run.get('runtime')} / {run.get('mode')}")
        print(f"Status:    {run.get('outcome_claim') or run.get('status')}")
        print(f"Health:    {'stale' if run.get('stale') else 'degraded' if run.get('degraded') else 'healthy'}")
        print(f"Evidence:  {int(run.get('verified_evidence_count') or 0)} verified")
        print(f"Open gates:{int(run.get('open_gate_count') or 0):>4}")
    return 0
