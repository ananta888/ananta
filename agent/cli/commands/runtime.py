"""ananta runtime — runtime profile management commands."""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any

SUBCOMMANDS = ["list", "inspect", "recommend"]


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
    import sys
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
            EnvironmentKind,
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


import sys
