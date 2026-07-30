#!/usr/bin/env python3
"""Execute the explicit Source Control Hub/worker container smoke definition."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEFINITION = (
    ROOT
    / "artifacts/test-gates/source-control-container-smoke-definition.json"
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()


def load_definition(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "checks", "limits"}
        or payload["schema"]
        != "ananta.source-control.container-smoke-definition.v1"
        or not isinstance(payload["checks"], list)
        or not isinstance(payload["limits"], dict)
    ):
        raise ValueError("container_smoke_definition_invalid")
    return payload


def plan(definition: Mapping[str, Any]) -> Mapping[str, object]:
    return {
        "schema": "ananta.source-control.container-smoke-evidence.v1",
        "definition_digest": _digest(definition),
        "status": "unverified",
        "results": [
            {
                "id": str(check["id"]),
                "status": "unverified",
                "http_status": None,
                "duration_ms": None,
                "body_digest": None,
                "reason_code": "execution_required",
            }
            for check in definition["checks"]
        ],
    }


def execute(
    definition: Mapping[str, Any],
    *,
    hub_url: str,
    worker_url: str,
    token: str | None,
) -> Mapping[str, object]:
    limits = definition["limits"]
    timeout = float(limits["request_timeout_seconds"])
    max_bytes = int(limits["max_response_bytes"])
    started = time.monotonic()
    results: list[dict[str, object]] = []
    for check in definition["checks"]:
        if (
            time.monotonic() - started
            > float(limits["total_timeout_seconds"])
        ):
            results.append(
                {
                    "id": str(check["id"]),
                    "status": "failed",
                    "http_status": None,
                    "duration_ms": None,
                    "body_digest": None,
                    "reason_code": "total_timeout",
                }
            )
            continue
        base = hub_url if check["surface"] == "hub" else worker_url
        headers = {"Accept": "application/json"}
        if check["auth"] == "bearer":
            if not token:
                results.append(
                    {
                        "id": str(check["id"]),
                        "status": "unverified",
                        "http_status": None,
                        "duration_ms": None,
                        "body_digest": None,
                        "reason_code": "bearer_token_missing",
                    }
                )
                continue
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            base.rstrip("/") + str(check["path"]),
            method=str(check["method"]),
            headers=headers,
        )
        request_started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(max_bytes + 1)
                status_code = int(response.status)
            if len(body) > max_bytes:
                raise ValueError("response_too_large")
            json.loads(body.decode("utf-8"))
            expected = {int(value) for value in check["expected_status"]}
            passed = status_code in expected
            result = {
                "id": str(check["id"]),
                "status": "passed" if passed else "failed",
                "http_status": status_code,
                "duration_ms": round(
                    (time.monotonic() - request_started) * 1000, 3
                ),
                "body_digest": hashlib.sha256(body).hexdigest(),
                "reason_code": None if passed else "unexpected_http_status",
            }
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            result = {
                "id": str(check["id"]),
                "status": "failed",
                "http_status": None,
                "duration_ms": round(
                    (time.monotonic() - request_started) * 1000, 3
                ),
                "body_digest": None,
                "reason_code": "container_check_failed",
            }
        results.append(result)
    statuses = {str(item["status"]) for item in results}
    overall = (
        "failed"
        if "failed" in statuses
        else "unverified"
        if "unverified" in statuses
        else "passed"
    )
    return {
        "schema": "ananta.source-control.container-smoke-evidence.v1",
        "definition_digest": _digest(definition),
        "status": overall,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definition", type=Path, default=DEFAULT_DEFINITION)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--hub-url", default="http://localhost:5000")
    parser.add_argument("--worker-url", default="http://localhost:5001")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        definition = load_definition(args.definition)
        report = (
            execute(
                definition,
                hub_url=args.hub_url,
                worker_url=args.worker_url,
                token=os.environ.get("SOURCE_CONTROL_SMOKE_TOKEN"),
            )
            if args.execute
            else plan(definition)
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "schema": "ananta.source-control.container-smoke-evidence.v1",
            "definition_digest": "0" * 64,
            "status": "failed",
            "results": [],
        }
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
