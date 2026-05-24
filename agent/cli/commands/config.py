"""ananta config — configuration management commands."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from typing import Any

SUBCOMMANDS = ["show", "validate", "export", "setup-planning", "apply-profile"]

_DEFAULT_BASE_URL = "http://localhost:5000"
_DEFAULT_USER = "admin"
_DEFAULT_PASSWORD = "test123"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta config",
        description="Manage Ananta runtime configuration.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ananta config show\n"
            "  ananta config show --json\n"
            "  ananta config validate\n"
            "  ananta config setup-planning\n"
            "  ananta config setup-planning --git-workspace --artifact-sync\n"
            "  ananta config apply-profile opencode_preconfigured\n"
            "  ananta config export > my-config.json\n"
        ),
    )
    _configure_subparsers(p)
    return p


def _configure_subparsers(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="config_cmd", metavar="<action>")

    show_p = sub.add_parser("show", help="Show effective runtime config from hub.")
    show_p.add_argument("--json", action="store_true", help="Output as JSON.")
    show_p.add_argument("--base-url", default=None, help=f"Hub base URL (default: {_DEFAULT_BASE_URL}).")

    sub.add_parser("validate", help="Validate local config.json against schema.")

    export_p = sub.add_parser("export", help="Export normalized config as JSON.")
    export_p.add_argument("--base-url", default=None)
    export_p.add_argument("--out", default="", help="Output file path (default: stdout).")

    sp_p = sub.add_parser(
        "setup-planning",
        help="[MUTATING] Apply LLM planning policy for local LMStudio runs.",
    )
    sp_p.add_argument("--base-url", default=None)
    sp_p.add_argument("--user", default=_DEFAULT_USER)
    sp_p.add_argument("--password", default=_DEFAULT_PASSWORD)
    sp_p.add_argument(
        "--git-workspace",
        action="store_true",
        default=False,
        help="Enable git workspace sharing per goal.",
    )
    sp_p.add_argument(
        "--artifact-sync",
        action="store_true",
        default=False,
        help="Enable artifact-hub sync mode.",
    )

    ap_p = sub.add_parser(
        "apply-profile",
        help="[MUTATING] Apply a named config profile to the running hub.",
    )
    ap_p.add_argument("profile", help="Profile name (e.g. opencode_preconfigured).")
    ap_p.add_argument("--base-url", default=None)
    ap_p.add_argument("--user", default=_DEFAULT_USER)
    ap_p.add_argument("--password", default=_DEFAULT_PASSWORD)
    ap_p.add_argument("--dry-run", action="store_true", help="Show profile overrides without applying.")


def dispatch(argv: Sequence[str]) -> int:
    parser = _build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    try:
        parsed = parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    cmd = parsed.config_cmd
    if cmd == "show":
        return _cmd_show(parsed)
    if cmd == "validate":
        return _cmd_validate()
    if cmd == "export":
        return _cmd_export(parsed)
    if cmd == "setup-planning":
        return _cmd_setup_planning(parsed)
    if cmd == "apply-profile":
        return _cmd_apply_profile(parsed)
    parser.print_help()
    return 0


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("config", help="Manage Ananta runtime configuration.")
    _configure_subparsers(p)
    p.set_defaults(_dispatch=dispatch)


# ── Implementations ────────────────────────────────────────────────────────────

def _base_url(parsed) -> str:
    return (
        getattr(parsed, "base_url", None)
        or os.environ.get("ANANTA_BASE_URL", "")
        or _DEFAULT_BASE_URL
    )


def _login(base_url: str, user: str, password: str) -> str:
    try:
        import requests
    except ImportError:
        print("Error: 'requests' package is required.", file=sys.stderr)
        raise
    resp = requests.post(
        f"{base_url}/login",
        json={"username": user, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json().get("data", {}).get("access_token", "")
    if not token:
        raise RuntimeError("Login failed — no token received")
    return token


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _cmd_show(parsed) -> int:
    import requests
    base = _base_url(parsed)
    try:
        r = requests.get(f"{base}/config", timeout=15)
        r.raise_for_status()
    except Exception as exc:
        print(f"Error: Could not reach hub at {base}: {exc}", file=sys.stderr)
        return 4
    data = r.json().get("data", {})
    if getattr(parsed, "json", False):
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        _print_config_summary(data)
    return 0


def _print_config_summary(data: dict) -> None:
    pp = data.get("planning_policy") or {}
    rps = pp.get("runtime_profiles") or {}
    lm = rps.get("lmstudio_laptop") or {}
    ws = data.get("workspace") or {}
    print(f"Provider:               {data.get('default_provider', '—')}")
    print(f"Model:                  {data.get('default_model', '—')}")
    print(f"Planning timeout:       {pp.get('timeout_seconds', '—')}s")
    print(f"Max output tokens:      {pp.get('max_output_tokens', '—')}")
    print(f"Segmented planning:     {pp.get('segmented_planning_enabled', '—')}")
    print(f"Max segments:           {pp.get('max_segments', '—')}")
    print(f"Repair rounds:          {pp.get('selective_repair_rounds', '—')}")
    print(f"lmstudio_laptop.timeout:{lm.get('timeout_seconds', '—')}s")
    gw = (ws.get("git_workspace") or {})
    if gw.get("enabled"):
        print(f"Git workspace:          enabled (branch_strategy={gw.get('branch_strategy', 'goal')})")
    sm = ws.get("sync_mode", "")
    if sm:
        print(f"Workspace sync_mode:    {sm}")


def _cmd_validate() -> int:
    from pathlib import Path
    cfg_path = Path("config.json")
    if not cfg_path.exists():
        print("FAIL: config.json not found in current directory.", file=sys.stderr)
        print("      Run `ananta init` or create config.json first.", file=sys.stderr)
        return 3
    try:
        with cfg_path.open() as f:
            data = json.load(f)
        print(f"OK: config.json is valid JSON ({len(data)} top-level keys).")
        return 0
    except json.JSONDecodeError as exc:
        print(f"FAIL: config.json is not valid JSON: {exc}", file=sys.stderr)
        return 3


def _cmd_export(parsed) -> int:
    import requests
    base = _base_url(parsed)
    try:
        r = requests.get(f"{base}/config", timeout=15)
        r.raise_for_status()
    except Exception as exc:
        print(f"Error: Could not reach hub at {base}: {exc}", file=sys.stderr)
        return 4
    data = r.json().get("data", {})
    out = getattr(parsed, "out", "")
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if out:
        from pathlib import Path
        Path(out).write_text(text)
        print(f"Config exported to {out}")
    else:
        print(text)
    return 0


def _build_planning_policy_payload(parsed) -> dict:
    workspace_cfg: dict = {}
    if getattr(parsed, "git_workspace", False):
        workspace_cfg["git_workspace"] = {"enabled": True, "branch_strategy": "goal"}
    if getattr(parsed, "artifact_sync", False):
        workspace_cfg["sync_mode"] = "artifact_hub_sync"

    policy: dict[str, Any] = {
        "default_provider": "lmstudio",
        "default_model": "google/gemma-4-e4b",
        "lmstudio_url": "http://192.168.178.100:1234/v1",
        "llm_config": {
            "provider": "lmstudio",
            "model": "google/gemma-4-e4b",
            "base_url": "http://192.168.178.100:1234/v1",
            "lmstudio_api_mode": "chat",
        },
        "planning_policy": {
            "delegated_planning_enabled": False,
            "allowed_planner_roles": ["planning-agent", "planner"],
            "require_review": False,
            "allow_remote_planners": False,
            "max_nodes": 8,
            "max_depth": 8,
            "timeout_seconds": 700,
            "max_output_tokens": 1600,
            "segmented_planning_enabled": True,
            "segment_context_chars": 2400,
            "max_segments": 3,
            "preferred_output_format": "json",
            "selective_repair_rounds": 1,
            "validation_profiles": {},
            "default_runtime_profile": "lmstudio_laptop",
            "runtime_profiles": {
                "lmstudio_laptop": {
                    "timeout_seconds": 300,
                    "max_output_tokens": 1600,
                    "retry_attempts": 1,
                    "retry_backoff_seconds": 1.0,
                    "segmented_planning_enabled": True,
                    "segment_context_chars": 2000,
                    "max_segments": 3,
                    "preferred_output_format": "json",
                }
            },
        },
        **({"workspace": workspace_cfg} if workspace_cfg else {}),
        "worker_runtime": {
            "todo_contract": {
                "planner_llm_enabled": False,
                "planner_llm_retry_attempts": 0,
            },
        },
        "autopilot_task_propose_hard_guard_status": "todo",
        "strategy_mode": "autopilot_no_human_review",
    }
    return policy


def _cmd_setup_planning(parsed) -> int:
    import requests
    base = _base_url(parsed)
    user = parsed.user
    password = parsed.password
    try:
        token = _login(base, user, password)
    except Exception as exc:
        print(f"Error: Login failed: {exc}", file=sys.stderr)
        return 4
    policy = _build_planning_policy_payload(parsed)
    r = requests.post(
        f"{base}/config",
        json=policy,
        headers=_auth_headers(token),
        timeout=15,
    )
    if not r.ok:
        print(f"Error: {r.status_code} {r.text[:200]}", file=sys.stderr)
        return 1
    print("Planning policy applied:")
    r2 = requests.get(f"{base}/config", timeout=15)
    if r2.ok:
        data = r2.json().get("data", {})
        _print_config_summary(data)
    return 0


def _cmd_apply_profile(parsed) -> int:
    import requests
    base = _base_url(parsed)
    profile_id = parsed.profile

    try:
        from agent.services.config_profile_service import get_config_profile_service
        profile = get_config_profile_service().get_profile(profile_id)
    except Exception as exc:
        print(f"Error loading profile '{profile_id}': {exc}", file=sys.stderr)
        return 1
    if profile is None:
        print(f"Error: Unknown profile '{profile_id}'.", file=sys.stderr)
        print("Available profiles: opencode_preconfigured, opencode_preconfigured_e2e, "
              "opencode_lmstudio_local, ananta_lmstudio_local, hermes_free_models_preconfigured")
        return 3

    overrides = profile.get("overrides", {})
    if getattr(parsed, "dry_run", False):
        print(f"Profile: {profile_id}")
        print(f"Description: {profile.get('description', '')}")
        print("Overrides (dry-run, not applied):")
        print(json.dumps(overrides, indent=2, ensure_ascii=False))
        return 0

    try:
        token = _login(base, parsed.user, parsed.password)
    except Exception as exc:
        print(f"Error: Login failed: {exc}", file=sys.stderr)
        return 4
    r = requests.post(
        f"{base}/config",
        json=overrides,
        headers=_auth_headers(token),
        timeout=15,
    )
    if not r.ok:
        print(f"Error: {r.status_code} {r.text[:200]}", file=sys.stderr)
        return 1
    print(f"Profile '{profile_id}' applied.")
    return 0
