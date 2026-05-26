from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent.services.planning_summary_doctor_service import migrate_track_todos


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recompute derived planning summaries for todo track files.")
    parser.add_argument("--repo-root", default=".", help="Repository root that contains todos/")
    parser.add_argument("--write", action="store_true", help="Write recomputed summaries back to disk")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Emit JSON report")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = migrate_track_todos(repo_root=Path(args.repo_root), dry_run=not bool(args.write))
    if bool(args.json_output):
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"repo_root: {report.get('repo_root')}")
        print(f"dry_run: {'yes' if report.get('dry_run') else 'no'}")
        print(f"scanned: {report.get('scanned')} track_files: {report.get('track_files')} changed: {report.get('changed')}")
        for item in list(report.get("results") or [])[:100]:
            print(
                f"- {item.get('path')} changed={bool(item.get('changed'))} "
                f"repaired_fields={','.join(list(item.get('repaired_fields') or [])) or '-'}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
