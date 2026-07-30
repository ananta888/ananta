#!/usr/bin/env python3
"""Deterministic-seed Source Control list/replay/dry-run/recovery harness."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import random
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEFINITION = (
    ROOT
    / "artifacts/test-gates/source-control-load-recovery-definition.json"
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
        or set(payload)
        != {"schema", "load", "scenarios", "thresholds"}
        or payload["schema"]
        != "ananta.source-control.load-recovery-definition.v1"
    ):
        raise ValueError("load_recovery_definition_invalid")
    load = payload["load"]
    thresholds = payload["thresholds"]
    if (
        not isinstance(load, dict)
        or set(load)
        != {
            "concurrency",
            "duration_seconds",
            "max_iterations",
            "page_limits",
            "seed",
        }
        or not isinstance(thresholds, dict)
        or set(thresholds)
        != {
            "cursor_p95_ms",
            "cursor_p99_ms",
            "max_error_rate",
            "minimum_requests",
            "require_replay_consistency",
        }
        or not isinstance(payload["scenarios"], list)
    ):
        raise ValueError("load_recovery_definition_invalid")
    if (
        not 1 <= int(load["concurrency"]) <= 64
        or not 1 <= int(load["max_iterations"]) <= 100000
        or not 0 <= int(load["duration_seconds"]) <= 86400
        or not isinstance(load["page_limits"], list)
        or not load["page_limits"]
        or any(not 1 <= int(value) <= 200 for value in load["page_limits"])
    ):
        raise ValueError("load_recovery_limits_invalid")
    return payload


def plan(definition: Mapping[str, Any]) -> Mapping[str, object]:
    return {
        "schema": "ananta.source-control.load-recovery-evidence.v1",
        "definition_digest": _digest(definition),
        "seed": int(definition["load"]["seed"]),
        "status": "unverified",
        "scenario_status": {
            str(name): "unverified" for name in definition["scenarios"]
        },
        "metrics": {
            "total_requests": None,
            "failed_requests": None,
            "error_rate": None,
            "cursor_p95_ms": None,
            "cursor_p99_ms": None,
            "replay_consistent": None,
        },
    }


@dataclass(frozen=True)
class RequestResult:
    payload: Mapping[str, Any]
    duration_ms: float


class HarnessClient:
    def __init__(self, *, base_url: str, token: str, timeout: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: Mapping[str, object] | None = None,
    ) -> RequestResult:
        encoded = (
            json.dumps(body, separators=(",", ":"), ensure_ascii=True).encode(
                "ascii"
            )
            if body is not None
            else None
        )
        request = urllib.request.Request(
            self._base_url + path,
            data=encoded,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
            },
        )
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024 or int(response.status) != 200:
                raise ValueError("harness_response_invalid")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("harness_response_invalid")
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            raise ValueError("harness_response_invalid")
        return RequestResult(
            payload=data,
            duration_ms=(time.monotonic() - started) * 1000,
        )


class _Accumulator:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.total = 0
        self.failed = 0
        self.cursor_latencies: list[float] = []
        self.replay_consistent = True
        self.bulk_verified = False
        self.recovery_verified = False

    def success(self, duration_ms: float, *, cursor: bool = False) -> None:
        with self.lock:
            self.total += 1
            if cursor:
                self.cursor_latencies.append(duration_ms)

    def failure(self) -> None:
        with self.lock:
            self.total += 1
            self.failed += 1


def _iteration(
    index: int,
    *,
    client: HarnessClient,
    seed: int,
    page_limits: list[int],
    accumulator: _Accumulator,
) -> None:
    rng = random.Random(seed + index)
    limit = int(rng.choice(page_limits))
    try:
        first = client.request(
            f"/api/source-control/v1/connections?limit={limit}"
        )
        accumulator.success(first.duration_ms, cursor=True)
        items = first.payload.get("items", [])
        next_cursor = first.payload.get("next_cursor")
        if isinstance(next_cursor, str) and next_cursor:
            second = client.request(
                "/api/source-control/v1/connections"
                f"?limit={limit}&cursor={next_cursor}"
            )
            accumulator.success(second.duration_ms, cursor=True)
        if isinstance(items, list) and items:
            item = items[rng.randrange(len(items))]
            if isinstance(item, Mapping):
                connection_id = str(item.get("connection_id") or "")
                etag = str(item.get("etag") or "")
                if connection_id and etag:
                    dry_run = client.request(
                        "/api/source-control/v1/bulk/plan",
                        method="POST",
                        body={
                            "mutation": "disable",
                            "targets": [
                                {
                                    "resource_id": connection_id,
                                    "expected_etag": etag,
                                }
                            ],
                            "dry_run": True,
                        },
                    )
                    accumulator.success(dry_run.duration_ms)
                    with accumulator.lock:
                        accumulator.bulk_verified = True
    except Exception:
        accumulator.failure()
    try:
        first_events = client.request(
            "/api/source-control/v1/events?after_sequence=0&limit=100"
        )
        accumulator.success(first_events.duration_ms)
        second_events = client.request(
            "/api/source-control/v1/events?after_sequence=0&limit=100"
        )
        accumulator.success(second_events.duration_ms)
        first_items = first_events.payload.get(
            "items", first_events.payload.get("events", [])
        )
        second_items = second_events.payload.get(
            "items", second_events.payload.get("events", [])
        )
        consistent = (
            isinstance(first_items, list)
            and isinstance(second_items, list)
            and len(second_items) >= len(first_items)
            and _digest(first_items)
            == _digest(second_items[: len(first_items)])
        )
        sequences = [
            int(item["sequence"])
            for item in first_items
            if isinstance(item, Mapping)
            and isinstance(item.get("sequence"), int)
        ]
        second_sequences = [
            int(item["sequence"])
            for item in second_items
            if isinstance(item, Mapping)
            and isinstance(item.get("sequence"), int)
        ]
        monotonic = (
            sequences == sorted(set(sequences))
            and second_sequences == sorted(set(second_sequences))
        )
        with accumulator.lock:
            accumulator.replay_consistent &= consistent and monotonic
            accumulator.recovery_verified = True
    except Exception:
        accumulator.failure()
        with accumulator.lock:
            accumulator.replay_consistent = False


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def execute(
    definition: Mapping[str, Any],
    *,
    base_url: str,
    token: str,
    timeout: float = 10.0,
) -> Mapping[str, object]:
    load = definition["load"]
    thresholds = definition["thresholds"]
    accumulator = _Accumulator()
    client = HarnessClient(
        base_url=base_url, token=token, timeout=timeout
    )
    deadline = time.monotonic() + int(load["duration_seconds"])
    submitted = 0
    concurrency = int(load["concurrency"])
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=concurrency
    ) as executor:
        pending: set[concurrent.futures.Future[None]] = set()
        while submitted < int(load["max_iterations"]):
            if int(load["duration_seconds"]) and time.monotonic() >= deadline:
                break
            pending.add(
                executor.submit(
                    _iteration,
                    submitted,
                    client=client,
                    seed=int(load["seed"]),
                    page_limits=[
                        int(value) for value in load["page_limits"]
                    ],
                    accumulator=accumulator,
                )
            )
            submitted += 1
            if len(pending) >= concurrency * 2:
                done, pending = concurrent.futures.wait(
                    pending,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done:
                    future.result()
        for future in concurrent.futures.as_completed(pending):
            future.result()
    total = accumulator.total
    error_rate = accumulator.failed / max(total, 1)
    p95 = _percentile(accumulator.cursor_latencies, 0.95)
    p99 = _percentile(accumulator.cursor_latencies, 0.99)
    list_passed = (
        total >= int(thresholds["minimum_requests"])
        and p95 <= float(thresholds["cursor_p95_ms"])
        and p99 <= float(thresholds["cursor_p99_ms"])
        and error_rate < float(thresholds["max_error_rate"])
    )
    replay_passed = accumulator.replay_consistent
    scenario_status = {
        "connection-list-cursor": "passed" if list_passed else "failed",
        "event-replay": "passed" if replay_passed else "failed",
        "bulk-dry-run": (
            "passed" if accumulator.bulk_verified else "unverified"
        ),
        "job-recovery-replay": (
            "passed"
            if accumulator.recovery_verified and replay_passed
            else "failed"
        ),
    }
    visible_statuses = [
        scenario_status.get(str(name), "unverified")
        for name in definition["scenarios"]
    ]
    status = (
        "failed"
        if "failed" in visible_statuses
        else "unverified"
        if "unverified" in visible_statuses
        else "passed"
    )
    return {
        "schema": "ananta.source-control.load-recovery-evidence.v1",
        "definition_digest": _digest(definition),
        "seed": int(load["seed"]),
        "status": status,
        "scenario_status": scenario_status,
        "metrics": {
            "total_requests": total,
            "failed_requests": accumulator.failed,
            "error_rate": round(error_rate, 8),
            "cursor_p95_ms": round(p95, 3),
            "cursor_p99_ms": round(p99, 3),
            "replay_consistent": accumulator.replay_consistent,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definition", type=Path, default=DEFAULT_DEFINITION)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--base-url", default="http://localhost:5000")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        definition = load_definition(args.definition)
        if args.execute:
            token = os.environ.get("SOURCE_CONTROL_HARNESS_TOKEN")
            if not token:
                raise ValueError("harness_token_required")
            report = execute(
                definition,
                base_url=args.base_url,
                token=token,
            )
        else:
            report = plan(definition)
    except (OSError, ValueError, json.JSONDecodeError):
        report = {
            "schema": "ananta.source-control.load-recovery-evidence.v1",
            "definition_digest": "0" * 64,
            "seed": 0,
            "status": "failed",
            "scenario_status": {},
            "metrics": {
                "total_requests": None,
                "failed_requests": None,
                "error_rate": None,
                "cursor_p95_ms": None,
                "cursor_p99_ms": None,
                "replay_consistent": None,
            },
        }
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
