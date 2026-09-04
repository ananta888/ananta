#!/usr/bin/env python3
"""Validate the installed optional verification stack against its license matrix."""

from __future__ import annotations

import argparse
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=ROOT / "config/licenses/python-verification.v1.json")
    args = parser.parse_args()
    payload = json.loads(args.matrix.read_text(encoding="utf-8"))
    failures: list[str] = []
    for package in payload["packages"]:
        if package["role"] == "conditional-transitive":
            continue
        try:
            installed = version(package["name"])
        except PackageNotFoundError:
            failures.append(f"{package['name']}:missing")
            continue
        if installed != package["version"]:
            failures.append(f"{package['name']}:expected={package['version']}:actual={installed}")
    print(json.dumps({"schema": "ananta.verification-supply-chain-check.v1", "failures": failures}, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
