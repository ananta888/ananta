"""Obsidian Vault CLI commands for ANANTA (OBS-010)."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

OBSIDIAN_SUBCOMMANDS = [
    "scan-vault",
    "sync-rag",
    "export-graph",
    "list-excluded",
    "validate-config",
]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta rag obsidian",
        description="Manage Obsidian Vault integration for ANANTA/CodeCompass.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ananta rag obsidian scan-vault --vault my_vault\n"
            "  ananta rag obsidian sync-rag --vault my_vault\n"
            "  ananta rag obsidian export-graph --vault my_vault\n"
            "  ananta rag obsidian list-excluded --vault my_vault\n"
            "  ananta rag obsidian validate-config\n"
        ),
    )
    _configure_subparsers(p)
    return p


def _configure_subparsers(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="obsidian_cmd", metavar="<action>")

    scan_p = sub.add_parser("scan-vault", help="Scan a vault directory and show file statistics.")
    scan_p.add_argument("--vault", required=True, help="Vault name (from config).")
    scan_p.add_argument("--json", action="store_true", help="Output as JSON.")

    sync_p = sub.add_parser("sync-rag", help="[MUTATING] Sync vault contents to the RAG index.")
    sync_p.add_argument("--vault", required=True, help="Vault name (from config).")
    sync_p.add_argument("--force", action="store_true", help="Force full re-index.")
    sync_p.add_argument("--dry-run", action="store_true", help="Show what would be indexed without writing.")

    graph_p = sub.add_parser("export-graph", help="Export vault graph (nodes/edges) to JSON.")
    graph_p.add_argument("--vault", required=True, help="Vault name (from config).")
    graph_p.add_argument("--output", default="-", help="Output file path (default: stdout).")

    excl_p = sub.add_parser("list-excluded", help="List files excluded by privacy filter.")
    excl_p.add_argument("--vault", required=True, help="Vault name (from config).")
    excl_p.add_argument("--json", action="store_true", help="Output as JSON.")

    sub.add_parser("validate-config", help="Validate all configured Obsidian vault profiles.")


def dispatch(argv: Sequence[str]) -> int:
    parser = _build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    try:
        parsed = parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    cmd = parsed.obsidian_cmd

    if cmd == "scan-vault":
        return _cmd_scan_vault(parsed)
    elif cmd == "sync-rag":
        return _cmd_sync_rag(parsed)
    elif cmd == "export-graph":
        return _cmd_export_graph(parsed)
    elif cmd == "list-excluded":
        return _cmd_list_excluded(parsed)
    elif cmd == "validate-config":
        return _cmd_validate_config()
    else:
        parser.print_help()
        return 0


def _load_vault_profile(vault_name: str):
    """Load a vault profile from config."""
    try:
        from agent.config import settings
        from agent.obsidian_config import load_vault_profiles

        raw = settings.obsidian_vaults
        profiles = load_vault_profiles(raw)
        if vault_name not in profiles:
            print(
                f"Error: vault '{vault_name}' not found in config. "
                f"Available: {list(profiles.keys())}",
                file=sys.stderr,
            )
            return None
        profile = profiles[vault_name]
        if not profile.name:
            profile = profile.model_copy(update={"name": vault_name})
        return profile
    except Exception as e:
        print(f"Error loading vault config: {e}", file=sys.stderr)
        return None


def _cmd_scan_vault(parsed) -> int:
    profile = _load_vault_profile(parsed.vault)
    if profile is None:
        return 1
    try:
        from rag_helper.application.vault_scanner import scan

        files = scan(profile)
        result = {
            "vault": parsed.vault,
            "path": profile.path,
            "file_count": len(files),
            "files_by_ext": {},
        }
        for vf in files:
            result["files_by_ext"].setdefault(vf.ext, 0)
            result["files_by_ext"][vf.ext] += 1

        if getattr(parsed, "json", False):
            print(json.dumps(result, indent=2))
        else:
            print(f"Vault: {result['vault']} ({result['path']})")
            print(f"Files: {result['file_count']}")
            for ext, count in result["files_by_ext"].items():
                print(f"  .{ext}: {count}")
        return 0
    except Exception as e:
        print(f"Error scanning vault: {e}", file=sys.stderr)
        return 1


def _cmd_sync_rag(parsed) -> int:
    print(
        f"Error: 'ananta rag obsidian sync-rag' is not yet fully implemented.",
        file=sys.stderr,
    )
    return 1


def _cmd_export_graph(parsed) -> int:
    print(
        f"Error: 'ananta rag obsidian export-graph' is not yet fully implemented.",
        file=sys.stderr,
    )
    return 1


def _cmd_list_excluded(parsed) -> int:
    profile = _load_vault_profile(parsed.vault)
    if profile is None:
        return 1
    try:
        from rag_helper.application.vault_scanner import scan
        from rag_helper.application.privacy_filter import list_excluded

        files = scan(profile)
        excluded = list_excluded(profile, files)

        if getattr(parsed, "json", False):
            print(json.dumps(excluded, indent=2))
        else:
            if not excluded:
                print("No files excluded by privacy filter.")
            else:
                print(f"Excluded files ({len(excluded)}):")
                for item in excluded:
                    print(f"  {item['rel_path']}  [{item['mechanism']}] {item['reason']}")
        return 0
    except Exception as e:
        print(f"Error listing excluded files: {e}", file=sys.stderr)
        return 1


def _cmd_validate_config() -> int:
    try:
        from agent.config import settings
        from agent.obsidian_config import load_vault_profiles

        raw = settings.obsidian_vaults
        if not raw:
            print("No Obsidian vaults configured (obsidian_vaults is empty).")
            return 0

        profiles = load_vault_profiles(raw)
        errors = []
        for name, profile in profiles.items():
            import os

            if not os.path.isdir(profile.path):
                errors.append(f"  Vault '{name}': path does not exist: {profile.path}")

        if errors:
            print("Validation errors:")
            for e in errors:
                print(e)
            return 1

        print(f"OK: {len(profiles)} vault(s) configured and valid.")
        for name, p in profiles.items():
            status = "enabled" if p.enabled else "disabled"
            print(f"  {name}: {p.path} [{status}]")
        return 0
    except Exception as e:
        print(f"Error validating config: {e}", file=sys.stderr)
        return 1


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("obsidian", help="Manage Obsidian Vault integration.")
    _configure_subparsers(p)
    p.set_defaults(_dispatch=dispatch)
