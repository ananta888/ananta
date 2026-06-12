#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from agent.services.generated_source_line_policy_service import (
    CATEGORY_EXCLUDED,
    CATEGORY_FACADE_OR_ROUTES,
    CATEGORY_GENERATED,
    CATEGORY_PRODUCTION_SOURCE,
    CATEGORY_TESTS,
    EXCLUDED_DIR_PARTS,
    EXCLUDED_PREFIXES,
    GENERATED_PATTERNS,
    SOURCE_EXTENSIONS,
    GeneratedSourceLinePolicyService,
)

_CLASSIFIER = GeneratedSourceLinePolicyService()


def _normalize_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def _is_excluded(rel: str) -> tuple[bool, str | None]:
    if any(rel.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return True, "excluded_prefix"
    parts = set(Path(rel).parts)
    for part in sorted(EXCLUDED_DIR_PARTS):
        if part in parts:
            return True, f"excluded_dir:{part}"
    return False, None


def classify_path(path: Path, *, root: Path, extensions: set[str] | None = None) -> dict[str, Any]:
    rel = _normalize_path(path.relative_to(root))
    return _CLASSIFIER.classify_path(rel, extensions=extensions or SOURCE_EXTENSIONS)


def count_lines(path: Path) -> int:
    try:
        with path.open("rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def load_allowlist(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("allowlist") if isinstance(payload, dict) else payload
    result: dict[str, dict[str, Any]] = {}
    for row in list(rows or []):
        if not isinstance(row, dict):
            continue
        rel = str(row.get("path") or "").strip()
        if rel:
            result[rel] = row
    return result


def audit(
    *,
    root: Path,
    threshold: int,
    extensions: set[str] | None = None,
    allowlist: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    allow = allowlist or {}
    for current_root, dirnames, filenames in os.walk(root):
        current = Path(current_root)
        kept_dirs = []
        for dirname in sorted(dirnames):
            excluded_dir = current / dirname
            rel_dir = _normalize_path(excluded_dir.relative_to(root))
            excluded, _ = _is_excluded(rel_dir)
            if excluded:
                for excluded_file in sorted(excluded_dir.iterdir()) if excluded_dir.exists() else []:
                    if not excluded_file.is_file():
                        continue
                    if excluded_file.suffix.lower() not in (extensions or SOURCE_EXTENSIONS):
                        continue
                    rel = _normalize_path(excluded_file.relative_to(root))
                    rows.append(
                        {
                            "path": rel,
                            "extension": excluded_file.suffix.lower(),
                            "category": "excluded",
                            "reason": "excluded_dir_pruned",
                            "line_count": count_lines(excluded_file),
                            "over_threshold": False,
                            "allowlisted": False,
                            "allowlist_reason": "",
                            "allowlist_expires": "",
                        }
                    )
            else:
                kept_dirs.append(dirname)
        dirnames[:] = kept_dirs
        for filename in sorted(filenames):
            path = current / filename
            rel = _normalize_path(path.relative_to(root))
            classification = classify_path(path, root=root, extensions=extensions)
            if classification["category"] == CATEGORY_EXCLUDED and classification["reason"] == "extension_not_counted":
                continue
            line_count = count_lines(path)
            allowed = allow.get(rel)
            rows.append(
                {
                    **classification,
                    "line_count": line_count,
                    "over_threshold": line_count > threshold,
                    "allowlisted": bool(allowed),
                    "allowlist_reason": str((allowed or {}).get("reason") or ""),
                    "allowlist_expires": str((allowed or {}).get("expires") or ""),
                }
            )
    over_threshold = [row for row in rows if row["over_threshold"]]
    failing_source = [
        row
        for row in over_threshold
        if row["category"] in {CATEGORY_PRODUCTION_SOURCE, CATEGORY_FACADE_OR_ROUTES} and not row.get("allowlisted")
    ]
    return {
        "schema": "source_file_line_count_audit.v1",
        "root": str(root),
        "threshold": threshold,
        "extensions": sorted(extensions or SOURCE_EXTENSIONS),
        "files": rows,
        "over_threshold": over_threshold,
        "summary": {
            "total_files": len(rows),
            "over_threshold": len(over_threshold),
            "source_over_threshold": sum(
                1 for row in over_threshold if row["category"] in {CATEGORY_PRODUCTION_SOURCE, CATEGORY_FACADE_OR_ROUTES}
            ),
            "test_over_threshold": sum(1 for row in over_threshold if row["category"] == CATEGORY_TESTS),
            "generated_over_threshold": sum(1 for row in over_threshold if row["category"] == CATEGORY_GENERATED),
            "failing_source_over_threshold": len(failing_source),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit source file line counts.")
    parser.add_argument("--root", default=".", help="Repository root to scan.")
    parser.add_argument("--threshold", type=int, default=1000)
    parser.add_argument("--extensions", nargs="*", default=sorted(SOURCE_EXTENSIONS))
    parser.add_argument("--allowlist", type=Path, default=None)
    parser.add_argument("--fail-on-source-over-threshold", action="store_true")
    parser.add_argument("--over-threshold-only", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    payload = audit(
        root=root,
        threshold=max(1, int(args.threshold)),
        extensions={str(item).lower() for item in args.extensions},
        allowlist=load_allowlist(args.allowlist),
    )
    output = {**payload, "files": payload["over_threshold"]} if args.over_threshold_only else payload
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    if args.fail_on_source_over_threshold and payload["summary"]["failing_source_over_threshold"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
