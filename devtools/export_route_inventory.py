#!/usr/bin/env python3
"""Export Flask route inventory for documentation drift checks."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.ai_agent import create_app


def main() -> int:
    parser = argparse.ArgumentParser(description="Export route inventory from Flask app")
    parser.add_argument("--output", default="docs/route-inventory.json", help="Output JSON path")
    parser.add_argument("--include-methods", action="store_true", help="Include HTTP methods")
    args = parser.parse_args()

    app = create_app()
    routes = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        if rule.endpoint == "static":
            continue
        entry = {
            "path": rule.rule,
            "endpoint": rule.endpoint,
        }
        if args.include_methods:
            entry["methods"] = sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"})
        routes.append(entry)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"routes": routes}, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(routes)} routes to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
