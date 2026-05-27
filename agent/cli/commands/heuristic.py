"""ananta heuristic — heuristic format management commands.

Commands:
  list      — list all heuristics from the registry index
  show      — show details of one heuristic
  validate  — validate a heuristic JSON/YAML file
  normalize — normalize a heuristic JSON/YAML file to canonical form
  catalog   — validate all active heuristics in the catalog
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from typing import Any

SUBCOMMANDS = ["list", "show", "validate", "normalize", "catalog"]

_DEFAULT_HEURISTICS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "heuristics")
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta heuristic",
        description="Manage heuristic definitions: validate, normalize, inspect.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ananta heuristic list\n"
            "  ananta heuristic list --domain chat_codecompass\n"
            "  ananta heuristic show chat_codecompass_symbol_lookup_default\n"
            "  ananta heuristic validate heuristics/active/my.heuristic.json\n"
            "  ananta heuristic normalize my_draft.heuristic.yaml --out out.json\n"
            "  ananta heuristic catalog\n"
        ),
    )
    _configure_subparsers(p)
    return p


def _configure_subparsers(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="heuristic_cmd", metavar="<action>")

    list_p = sub.add_parser("list", help="List all registered heuristics.")
    list_p.add_argument("--domain", default="", help="Filter by domain.")
    list_p.add_argument("--status", default="", help="Filter by status (active/candidate/etc).")
    list_p.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON.")
    list_p.add_argument("--index", default="", help="Path to index.json (default: heuristics/index.json).")

    show_p = sub.add_parser("show", help="Show details of one heuristic.")
    show_p.add_argument("heuristic_id", help="Heuristic ID to show.")
    show_p.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON.")
    show_p.add_argument("--index", default="", help="Path to index.json.")

    validate_p = sub.add_parser("validate", help="Validate a heuristic file (JSON or YAML).")
    validate_p.add_argument("file", help="Path to .heuristic.json or .heuristic.yaml file.")
    validate_p.add_argument("--strict", action="store_true", help="Fail on warnings too.")
    validate_p.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON.")

    norm_p = sub.add_parser("normalize", help="Normalize a heuristic file to canonical JSON.")
    norm_p.add_argument("file", help="Path to .heuristic.json or .heuristic.yaml file.")
    norm_p.add_argument("--out", default="", help="Output path (default: stdout).")
    norm_p.add_argument("--dry-run", action="store_true", help="Show result without writing.")

    catalog_p = sub.add_parser("catalog", help="Validate all active heuristics.")
    catalog_p.add_argument("--dir", default="", help="Heuristics directory (default: heuristics/active/).")
    catalog_p.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON.")


def dispatch(argv: Sequence[str]) -> int:
    parser = _build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    try:
        parsed = parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    cmd = parsed.heuristic_cmd
    if cmd == "list":
        return _cmd_list(parsed)
    if cmd == "show":
        return _cmd_show(parsed)
    if cmd == "validate":
        return _cmd_validate(parsed)
    if cmd == "normalize":
        return _cmd_normalize(parsed)
    if cmd == "catalog":
        return _cmd_catalog(parsed)
    parser.print_help()
    return 0


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("heuristic", help="Manage heuristic definitions.")
    _configure_subparsers(p)
    p.set_defaults(_dispatch=dispatch)


# ── list ──────────────────────────────────────────────────────────────────────

def _load_index(index_path: str) -> dict[str, Any] | None:
    if not index_path:
        index_path = os.path.join(_DEFAULT_HEURISTICS_DIR, "index.json")
    if not os.path.isfile(index_path):
        return None
    try:
        with open(index_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _cmd_list(parsed: argparse.Namespace) -> int:
    index = _load_index(getattr(parsed, "index", ""))
    if index is None:
        print("ERROR: heuristics/index.json not found.", file=sys.stderr)
        return 1

    entries = index.get("heuristics") or []
    domain_filter = (getattr(parsed, "domain", "") or "").strip()
    status_filter = (getattr(parsed, "status", "") or "").strip()

    if domain_filter:
        entries = [e for e in entries if e.get("domain") == domain_filter]
    if status_filter:
        entries = [e for e in entries if e.get("status") == status_filter]

    if getattr(parsed, "as_json", False):
        print(json.dumps({"heuristics": entries}, indent=2))
        return 0

    if not entries:
        print("No heuristics found.")
        return 0

    print(f"{'ID':<50} {'DOMAIN':<22} {'STATUS':<12} {'VERSION'}")
    print("-" * 100)
    for e in entries:
        print(
            f"{e.get('heuristic_id', ''):<50} "
            f"{e.get('domain', ''):<22} "
            f"{e.get('status', ''):<12} "
            f"{e.get('version', '')}"
        )
    print(f"\n{len(entries)} heuristic(s) listed.")
    return 0


# ── show ──────────────────────────────────────────────────────────────────────

def _cmd_show(parsed: argparse.Namespace) -> int:
    hid = parsed.heuristic_id
    index = _load_index(getattr(parsed, "index", ""))
    if index is None:
        print("ERROR: heuristics/index.json not found.", file=sys.stderr)
        return 1

    entry = next((e for e in (index.get("heuristics") or []) if e.get("heuristic_id") == hid), None)
    if entry is None:
        print(f"ERROR: heuristic '{hid}' not found in index.", file=sys.stderr)
        return 1

    # Try to load the full file
    file_path = entry.get("file") or ""
    if file_path and not os.path.isabs(file_path):
        file_path = os.path.join(_DEFAULT_HEURISTICS_DIR, "active", os.path.basename(file_path))
    full: dict[str, Any] = {}
    if file_path and os.path.isfile(file_path):
        try:
            with open(file_path, encoding="utf-8") as f:
                full = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass

    data = full or entry
    if getattr(parsed, "as_json", False):
        print(json.dumps(data, indent=2))
        return 0

    print(f"Heuristic: {hid}")
    print(f"  Version:      {data.get('version', '')}")
    print(f"  Domain:       {data.get('domain', '')}")
    print(f"  Status:       {data.get('status', '')}")
    print(f"  Safety class: {data.get('safety_class', '')}")
    print(f"  Description:  {data.get('description', '')}")
    runtime = data.get("runtime") or {}
    if isinstance(runtime, dict):
        print(f"  Runtime mode: {runtime.get('mode', '')}")
    caps = data.get("capabilities") or []
    print(f"  Capabilities: {', '.join(caps) if caps else '(none)'}")
    return 0


# ── validate ──────────────────────────────────────────────────────────────────

def _cmd_validate(parsed: argparse.Namespace) -> int:
    file_path = parsed.file
    strict = getattr(parsed, "strict", False)
    as_json = getattr(parsed, "as_json", False)

    from agent.services.heuristic_runtime.heuristic_catalog_validator import HeuristicCatalogValidator
    from agent.services.heuristic_runtime.format_validator import HeuristicFormatValidator

    if not os.path.isfile(file_path):
        print(f"ERROR: File not found: {file_path}", file=sys.stderr)
        return 1

    catalog_val = HeuristicCatalogValidator()
    format_val = HeuristicFormatValidator()

    file_result = catalog_val.validate_file(file_path)
    format_codes: list[str] = []
    format_warnings: list[str] = []

    if file_result.passed:
        try:
            with open(file_path, encoding="utf-8") as f:
                raw = json.load(f) if file_path.endswith(".json") else {}
            if raw:
                fv = format_val.validate(raw)
                format_codes = fv.reason_codes
                format_warnings = fv.warnings
        except (OSError, json.JSONDecodeError):
            pass

    all_errors = list(file_result.errors) + format_codes
    all_warnings = list(file_result.warnings) + format_warnings
    passed = file_result.passed and len(format_codes) == 0
    if strict and all_warnings:
        passed = False

    if as_json:
        print(json.dumps({
            "passed": passed,
            "file": file_path,
            "errors": all_errors,
            "warnings": all_warnings,
        }, indent=2))
        return 0 if passed else 1

    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {file_path}")
    for err in all_errors:
        print(f"  ERROR: {err}")
    for warn in all_warnings:
        print(f"  WARN:  {warn}")
    if passed and not all_warnings:
        print("  OK — no issues found.")
    return 0 if passed else 1


# ── normalize ────────────────────────────────────────────────────────────────

def _cmd_normalize(parsed: argparse.Namespace) -> int:
    file_path = parsed.file
    out_path = getattr(parsed, "out", "")
    dry_run = getattr(parsed, "dry_run", False)

    from agent.services.heuristic_runtime.heuristic_normalizer import HeuristicNormalizer
    normalizer = HeuristicNormalizer()

    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
    except OSError as exc:
        print(f"ERROR: Cannot read file: {exc}", file=sys.stderr)
        return 1

    if file_path.endswith(".yaml") or file_path.endswith(".yml"):
        result = normalizer.normalize_from_yaml(content)
    else:
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            print(f"ERROR: JSON parse error: {exc}", file=sys.stderr)
            return 1
        result = normalizer.normalize(raw, source_format="json")

    if not result.success:
        print(f"ERROR: Normalization failed: {result.reason_code}", file=sys.stderr)
        return 1

    normalized_text = json.dumps(result.normalized, indent=2, ensure_ascii=False)

    for warn in result.warnings:
        print(f"WARN: {warn}", file=sys.stderr)

    if dry_run or not out_path:
        print(normalized_text)
        return 0

    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(normalized_text)
        print(f"Written: {out_path}  (hash: {result.content_hash[:12]}...)")
    except OSError as exc:
        print(f"ERROR: Cannot write: {exc}", file=sys.stderr)
        return 1
    return 0


# ── catalog ───────────────────────────────────────────────────────────────────

def _cmd_catalog(parsed: argparse.Namespace) -> int:
    active_dir = getattr(parsed, "dir", "") or os.path.join(_DEFAULT_HEURISTICS_DIR, "active")
    as_json = getattr(parsed, "as_json", False)

    from agent.services.heuristic_runtime.heuristic_catalog_validator import HeuristicCatalogValidator
    validator = HeuristicCatalogValidator()
    result = validator.validate_directory(active_dir)

    if as_json:
        print(json.dumps({
            "total": result.total,
            "passed": result.passed,
            "failed": result.failed,
            "files": [
                {"file": r.file, "passed": r.passed, "errors": r.errors, "warnings": r.warnings}
                for r in result.results
            ],
        }, indent=2))
        return 0 if result.failed == 0 else 1

    status = "PASS" if result.failed == 0 else "FAIL"
    print(f"[{status}] Catalog: {active_dir}")
    print(f"  Total: {result.total}  Passed: {result.passed}  Failed: {result.failed}")
    for r in result.results:
        if not r.passed:
            print(f"  FAIL: {r.file}")
            for err in r.errors:
                print(f"    ERROR: {err}")
    return 0 if result.failed == 0 else 1
