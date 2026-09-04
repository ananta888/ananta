#!/usr/bin/env python3
"""Build a deterministic SPDX subset from the reviewed verification matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=ROOT / "config/licenses/python-verification.v1.json")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/python-verification-sbom.json")
    args = parser.parse_args()
    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    packages = [
        {
            "SPDXID": f"SPDXRef-Package-{item['name'].replace('_', '-').replace('.', '-')}",
            "name": item["name"],
            "versionInfo": item["version"],
            "downloadLocation": item["origin"],
            "licenseConcluded": item["license"],
        }
        for item in matrix["packages"]
    ]
    payload = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "ananta-python-verification-worker",
        "documentNamespace": "https://ananta.dev/sbom/python-verification-worker/v1",
        "packages": packages,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
