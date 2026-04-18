#!/usr/bin/env python3
"""Export Flask route inventory for documentation drift checks."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.ai_agent import create_app  # noqa: E402
from agent.bootstrap.route_aliases import route_alias_metadata  # noqa: E402


def build_route_inventory(app, *, include_methods: bool = False, include_route_kind: bool = False) -> list[dict]:
    route_metadata = {
        **route_alias_metadata(),
        **dict(app.extensions.get("route_inventory_metadata") or {}),
    }
    routes = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        if rule.endpoint == "static":
            continue
        entry = {
            "path": rule.rule,
            "endpoint": rule.endpoint,
        }
        if include_methods:
            entry["methods"] = sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"})
        if include_route_kind:
            metadata = dict(route_metadata.get(rule.rule) or {})
            entry["route_kind"] = metadata.get("route_kind", "canonical")
            if metadata.get("canonical_path"):
                entry["canonical_path"] = metadata["canonical_path"]
        routes.append(entry)
    return routes


def main() -> int:
    parser = argparse.ArgumentParser(description="Export route inventory from Flask app")
    parser.add_argument("--output", default="docs/route-inventory.json", help="Output JSON path")
    parser.add_argument("--include-methods", action="store_true", help="Include HTTP methods")
    parser.add_argument("--include-route-kind", action="store_true", help="Include canonical/alias route metadata")
    args = parser.parse_args()

    app = create_app()
    routes = build_route_inventory(
        app,
        include_methods=args.include_methods,
        include_route_kind=args.include_route_kind,
    )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"routes": routes}, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(routes)} routes to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
