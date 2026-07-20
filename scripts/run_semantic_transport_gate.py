#!/usr/bin/env python3
"""Produce or verify the deterministic, content-free semantic transport gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent.repositories.semantic_relay_repository import (
    InMemorySemanticRelayRepository,
    SemanticRelayEnvelope,
)
from agent.services.semantic_relay_limits import SemanticRelayLimits
from ananta_contracts.webrtc_datachannel import DataChannelContractError, parse_wire_message

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts/test-gates/semantic-transport.json"
SEED = 87_431
SOURCE_FILES = (
    "ananta_contracts/webrtc_datachannel.py",
    "agent/db_models/semantic_relay.py",
    "agent/repositories/semantic_relay_repository.py",
    "agent/repositories/semantic_relay_shared_store.py",
    "agent/services/semantic_relay_authorization.py",
    "agent/services/semantic_relay_composition.py",
    "agent/services/semantic_relay_limits.py",
    "agent/services/semantic_relay_observability.py",
    "agent/services/semantic_relay_rate_limiter.py",
    "agent/services/semantic_relay_service.py",
    "agent/services/share_session_relay_membership.py",
    "agent/services/share_view_security_service.py",
    "frontend-angular/src/app/services/webrtc-datachannel.service.ts",
    "frontend-angular/src/app/services/webrtc-chunk-reassembly.store.ts",
    "frontend-angular/src/app/services/webrtc-priority-send-queue.ts",
    "frontend-angular/src/app/services/webrtc-send-operation.ts",
    "frontend-angular/src/app/services/webrtc-session.service.ts",
    "frontend-angular/src/app/services/webrtc-transport.service.ts",
    "scripts/benchmark/semantic_transport_saturation.ts",
    "scripts/run_semantic_transport_gate.py",
    "migrations/versions/d8e9f0a1b2c3_add_semantic_relay_store.py",
    "migrations/versions/e9f0a1b2c3d4_add_semantic_relay_wire_metadata.py",
    "schemas/webrtc/bounded_chunk.v1.json",
    "schemas/webrtc/datachannel_message.v1.json",
    "tests/security/test_semantic_transport_fuzz.py",
    "tests/chaos/test_semantic_transport_recovery.py",
    "tests/integration/test_semantic_relay_multi_hub.py",
    "frontend-angular/src/app/services/webrtc-priority-send-queue.spec.ts",
    "frontend-angular/src/app/services/webrtc-chunk-reassembly.store.spec.ts",
    "frontend-angular/src/app/services/webrtc-transport.service.spec.ts",
)
FORBIDDEN_KEYS = frozenset({"payload", "ciphertext", "transcript", "audio", "frame", "plaintext"})


def _source_hash() -> str:
    digest = hashlib.sha256()
    for relative in SOURCE_FILES:
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update((ROOT / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _row(index: int, traffic_class: str = "evidence_bulk") -> SemanticRelayEnvelope:
    return SemanticRelayEnvelope(
        message_id=f"gate-{index}",
        tenant_id="tenant-gate",
        session_id="session-gate",
        epoch=1,
        sender_id="sender-gate",
        audience_id="audience-gate",
        traffic_class=traffic_class,
        payload_bytes=1,
        payload_digest=f"{index:064x}",
        ciphertext="opaque",
        expires_at=100,
    )


def _relay_probe() -> dict[str, int | bool]:
    limits = SemanticRelayLimits(
        max_global_messages=34,
        max_global_bytes=100,
        priority_reserve_messages=2,
        priority_reserve_bytes=2,
        max_session_messages=34,
        max_session_bytes=100,
        max_peer_messages=34,
        max_peer_bytes=100,
    )
    repository = InMemorySemanticRelayRepository(limits)
    for index in range(32):
        repository.append(_row(index), now=0)
    duplicate = repository.append(_row(31), now=1)
    control = repository.append(_row(32, "control"), now=1)
    transcript = repository.append(_row(33, "transcript"), now=1)
    evidence_page = repository.read_after(
        tenant_id="tenant-gate",
        session_id="session-gate",
        audience_id="audience-gate",
        cursor=0,
        limit=100,
        now=2,
        traffic_class="evidence_bulk",
    )
    class_pages = {
        traffic_class: repository.read_after(
            tenant_id="tenant-gate",
            session_id="session-gate",
            audience_id="audience-gate",
            cursor=0,
            limit=100,
            now=2,
            traffic_class=traffic_class,
        )
        for traffic_class in ("control", "transcript")
    }
    acknowledged = {
        traffic_class: repository.acknowledge(
            tenant_id="tenant-gate",
            session_id="session-gate",
            audience_id="audience-gate",
            cursor=32 if traffic_class == "evidence_bulk" else 1,
            now=3,
            traffic_class=traffic_class,
        )
        for traffic_class in ("evidence_bulk", "control", "transcript")
    }
    snapshot = repository.snapshot()
    return {
        "contiguous_cursor": [row.cursor for row in evidence_page] == list(range(1, 33)),
        "traffic_cursor_isolation": all(
            [row.cursor for row in class_pages[traffic_class]] == [1] for traffic_class in ("control", "transcript")
        ),
        "duplicate_cursor": duplicate.cursor,
        "control_cursor": control.cursor,
        "transcript_cursor": transcript.cursor,
        "ack_complete": acknowledged == {"evidence_bulk": 32, "control": 1, "transcript": 1},
        "remaining_messages": snapshot["messages"],
        "cursor_scopes": snapshot["cursors"],
    }


def _preparse_probe() -> bool:
    try:
        parse_wire_message(b"ANANTA-DC1 evidence_bulk 1048577 1\n{")
    except DataChannelContractError as exc:
        return exc.reason_code == "payload_too_large"
    return False


def _saturation_probe() -> dict[str, int | str]:
    executable = ROOT / "frontend-angular/node_modules/.bin/vite-node"
    if not executable.is_file():
        raise RuntimeError("transport_saturation_runtime_missing")
    completed = subprocess.run(
        [str(executable), "scripts/benchmark/semantic_transport_saturation.ts"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError("transport_saturation_probe_failed")
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("transport_saturation_report_invalid") from exc
    required = {
        "schema",
        "sample_count",
        "bulk_saturated_samples",
        "initial_bulk_messages",
        "final_bulk_messages",
        "control_sent",
        "revision_sent",
        "bulk_sent",
        "control_p95_ms",
        "revision_p95_ms",
        "timer_count",
        "bulk_eviction_count",
    }
    if (
        not isinstance(report, dict)
        or set(report) != required
        or report.get("schema") != "ananta.semantic-transport-saturation.v1"
        or any(type(value) is not int or value < 0 for key, value in report.items() if key != "schema")
    ):
        raise RuntimeError("transport_saturation_report_invalid")
    return report


def _required_suite_probe() -> dict[str, int]:
    suites = (
        (
            "python_exit_code",
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/security/test_semantic_transport_fuzz.py",
                "tests/chaos/test_semantic_transport_recovery.py",
                "tests/integration/test_semantic_relay_multi_hub.py",
            ],
            ROOT,
        ),
        (
            "angular_exit_code",
            [
                "npx",
                "vitest",
                "run",
                "src/app/services/webrtc-priority-send-queue.spec.ts",
                "src/app/services/webrtc-chunk-reassembly.store.spec.ts",
                "src/app/services/webrtc-transport.service.spec.ts",
            ],
            ROOT / "frontend-angular",
        ),
    )
    results: dict[str, int] = {}
    for identifier, command, cwd in suites:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                check=False,
                capture_output=True,
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired):
            results[identifier] = 124
        else:
            results[identifier] = int(completed.returncode)
    return results


def expected_document(saturation: dict[str, int | str] | None = None) -> dict[str, Any]:
    relay = _relay_probe()
    measured = saturation or _saturation_probe()
    control_p95 = int(measured["control_p95_ms"])
    revision_p95 = int(measured["revision_p95_ms"])
    sample_count = int(measured["sample_count"])
    suites = _required_suite_probe()
    checks = {
        "bounded_preparse": _preparse_probe(),
        "contiguous_cursor": bool(relay["contiguous_cursor"]),
        "traffic_cursor_isolation": bool(relay["traffic_cursor_isolation"]),
        "duplicate_idempotent": relay["duplicate_cursor"] == 32,
        "priority_reserve": relay["control_cursor"] == 1 and relay["transcript_cursor"] == 1,
        "ack_cleanup": bool(relay["ack_complete"]) and relay["remaining_messages"] == 0,
        "cursor_scope_bounded": relay["cursor_scopes"] == 3,
        "bulk_saturation_measured": (
            sample_count >= 128
            and measured["bulk_saturated_samples"] == sample_count
            and measured["initial_bulk_messages"] == 64
            and measured["final_bulk_messages"] == 64
        ),
        "control_latency_gate": control_p95 <= 50 and measured["control_sent"] == sample_count,
        "transcript_latency_gate": revision_p95 <= 50 and measured["revision_sent"] == sample_count,
        "browser_timer_gate": measured["timer_count"] == 0,
        "required_suites_executed": suites == {"python_exit_code": 0, "angular_exit_code": 0},
    }
    document: dict[str, Any] = {
        "schema_version": "ananta.semantic-transport-gate.v2",
        "gate": "ASMP-TRN-010",
        "seed": SEED,
        "source_sha256": _source_hash(),
        "source_files": list(SOURCE_FILES),
        "thresholds": {
            "control_p95_ms": 50,
            "transcript_p95_ms": 50,
            "max_remaining_messages": 0,
            "max_timer_leaks": 0,
            "max_cursor_scopes": 3,
        },
        "measurements": {
            "control_p95_ms": control_p95,
            "transcript_p95_ms": revision_p95,
            "saturation_samples": sample_count,
            "bulk_saturated_samples": int(measured["bulk_saturated_samples"]),
            "control_sent": int(measured["control_sent"]),
            "transcript_sent": int(measured["revision_sent"]),
            "bulk_evictions": int(measured["bulk_eviction_count"]),
            "remaining_messages": relay["remaining_messages"],
            "timer_leaks": int(measured["timer_count"]),
            "cursor_scopes": relay["cursor_scopes"],
        },
        "checks": checks,
        "required_test_files": [
            "tests/security/test_semantic_transport_fuzz.py",
            "tests/chaos/test_semantic_transport_recovery.py",
            "tests/integration/test_semantic_relay_multi_hub.py",
            "frontend-angular/src/app/services/webrtc-priority-send-queue.spec.ts",
            "frontend-angular/src/app/services/webrtc-chunk-reassembly.store.spec.ts",
            "frontend-angular/src/app/services/webrtc-transport.service.spec.ts",
        ],
        "test_suites": suites,
        "content_policy": {
            "content_free": True,
            "forbidden_fields": sorted(FORBIDDEN_KEYS),
        },
        "measurement_method": "monotonic_product_queue_and_executed_recovery_suites",
        "passed": all(checks.values()),
    }
    _assert_content_free(document)
    return document


def _assert_content_free(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key.lower() in FORBIDDEN_KEYS:
                raise RuntimeError(f"transport_gate_forbidden_field:{'.'.join((*path, key))}")
            _assert_content_free(nested, (*path, key))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_content_free(nested, (*path, str(index)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    expected = expected_document()
    rendered = json.dumps(expected, indent=2, sort_keys=True) + "\n"
    if args.write:
        ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
        ARTIFACT.write_text(rendered)
    if args.verify:
        if (
            not expected["passed"]
            or not ARTIFACT.exists()
            or not _verify_persisted(json.loads(ARTIFACT.read_text(encoding="utf-8")))
        ):
            raise SystemExit("semantic_transport_gate_stale")
    if not args.write and not args.verify:
        print(rendered, end="")
    return 0 if expected["passed"] else 1


def _verify_persisted(document: Any) -> bool:
    if not isinstance(document, dict):
        return False
    try:
        _assert_content_free(document)
    except RuntimeError:
        return False
    return (
        document.get("schema_version") == "ananta.semantic-transport-gate.v2"
        and document.get("gate") == "ASMP-TRN-010"
        and document.get("seed") == SEED
        and document.get("source_sha256") == _source_hash()
        and document.get("source_files") == list(SOURCE_FILES)
        and document.get("measurement_method") == "monotonic_product_queue_and_executed_recovery_suites"
        and document.get("passed") is True
        and isinstance(document.get("checks"), dict)
        and all(document["checks"].values())
        and document.get("measurements", {}).get("saturation_samples", 0) >= 128
        and document.get("measurements", {}).get("bulk_saturated_samples")
        == document.get("measurements", {}).get("saturation_samples")
        and document.get("measurements", {}).get("control_p95_ms", 51) <= 50
        and document.get("measurements", {}).get("transcript_p95_ms", 51) <= 50
        and document.get("measurements", {}).get("timer_leaks", 1) == 0
        and document.get("test_suites") == {"python_exit_code": 0, "angular_exit_code": 0}
    )


if __name__ == "__main__":
    raise SystemExit(main())
