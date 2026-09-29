#!/usr/bin/env python3
"""Write ``tests/test_durations.json`` (seconds per test file) from a pytest JUnit report.

    python -m pytest -n 8 --tier all --junitxml=.tmp/durations.xml tests
    python scripts/update_test_durations.py .tmp/durations.xml

The file only balances CI shards (``tests/sharding.py``); stale or missing entries make shards uneven, never
wrong. Times are rounded to 0.1 s so that re-measuring does not rewrite every line.
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tests" / "test_durations.json"


def file_of_classname(classname: str) -> str | None:
    """``tests.meet.test_x.TestY`` -> ``tests/meet/test_x.py``."""
    parts = []
    for part in classname.split("."):
        parts.append(part)
        if part.startswith("test_"):
            return "/".join(parts) + ".py"
    return None


def durations_by_file(report: Path) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for case in ET.parse(report).getroot().iter("testcase"):
        name = file_of_classname(case.get("classname", ""))
        if name:
            totals[name] += float(case.get("time", 0) or 0)
    return {name: round(seconds, 1) for name, seconds in sorted(totals.items())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report", type=Path, help="JUnit XML of a full run (--tier all)")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args(argv)
    files = durations_by_file(arguments.report)
    payload = {"schema": "ananta.test_durations.v1", "unit": "seconds", "files": files}
    arguments.output.write_text(json.dumps(payload, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    print(f"{len(files)} files, {sum(files.values()) / 60:.1f} min total -> {arguments.output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
