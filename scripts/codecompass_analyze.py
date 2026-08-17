#!/usr/bin/env python3
"""Standalone: analyze a CodeCompass graph JSON into portable artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ananta_codecompass.architecture_intelligence.analyze import analyze_architecture
from ananta_codecompass.architecture_intelligence.exporters import export_html, export_markdown


def main() -> int:
    parser = argparse.ArgumentParser(prog="ananta codecompass analyze")
    parser.add_argument("graph_json")
    parser.add_argument("-o", "--out", default="codecompass-out")
    args = parser.parse_args()
    records = json.loads(Path(args.graph_json).read_text(encoding="utf-8"))
    result = analyze_architecture(records, snapshot_ref=args.graph_json)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "architecture-intelligence.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (out / "report.md").write_text(export_markdown(result), encoding="utf-8")
    (out / "architecture.html").write_text(export_html(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
