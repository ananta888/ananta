#!/usr/bin/env python3
"""Aggregate deterministic source-control release evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agent.services.source_control_release_gate import (
    SourceControlReleaseGateError,
    evaluate_source_control_release_gate,
)


def _read_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "evidence",
        "production_verification",
    }:
        raise SourceControlReleaseGateError("invalid release-gate manifest")
    if payload["schema_version"] != "1.0":
        raise SourceControlReleaseGateError("unsupported manifest schema")
    if not isinstance(payload["evidence"], list):
        raise SourceControlReleaseGateError("evidence must be a list")
    production = payload["production_verification"]
    if production is not None and not isinstance(production, dict):
        raise SourceControlReleaseGateError(
            "production_verification must be an object or null"
        )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        payload = _read_manifest(args.manifest)
        report = evaluate_source_control_release_gate(
            payload["evidence"],
            production_payload=payload["production_verification"],
        )
        output = json.dumps(
            report.to_dict(),
            indent=2,
            sort_keys=True,
        ) + "\n"
    except (OSError, json.JSONDecodeError, SourceControlReleaseGateError) as exc:
        output = json.dumps(
            {
                "schema_version": "1.0",
                "release_allowed": False,
                "error": type(exc).__name__,
            },
            indent=2,
            sort_keys=True,
        ) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            print(output, end="")
        return 2
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0 if report.release_allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
