"""Concurrent live-SLO probe running real browser speech paths while the worker resolver is saturated."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Mapping

from agent.services.speech_reconciliation_resource_policy import (
    SpeechReconciliationResourcePolicy,
    SpeechReconciliationResourceRequest,
)
from ananta_contracts.speech_reconciliation import (
    SpeechReconciliationContractError,
    SpeechResourceVector,
)
from voice_runtime.peer_transcript_consensus import PeerTranscriptCandidate
from voice_runtime.speech_reconciliation_policy import (
    SpeechReconciliationPolicy,
    SpeechReconciliationQualitySample,
)
from worker.speech_reconciliation.resolver import SpeechReconciliationResolver
from scripts.benchmark.semantic_media_program_measurements import (
    ProgramBenchmarkExecutionError,
)


# The probe runs the frontend toolchain from the repository root.
_PROBE_ROOT = Path(__file__).resolve().parents[2]


def _concurrent_live_slo_probe(
    *,
    factor: int,
    candidates: tuple[PeerTranscriptCandidate, ...],
    saturate: bool,
    iterations: int,
    deadline: float,
) -> dict[str, int | bool]:
    """Run real browser speech paths while the worker resolver is saturated.

    Admission and overload probes use the Hub-owned resource policy.  The
    background loop calls the worker's canonical reconciliation resolver; the
    foreground subprocess measures actual DelayBuffer, transcript projection
    and quality/UI state operations.  No sleep-derived latency is recorded.
    """

    resource_policy = SpeechReconciliationResourcePolicy()
    admitted = resource_policy.evaluate(
        SpeechReconciliationResourceRequest(
            mode="immediate",
            requested_factor=factor,
            user_max_factor=factor,
            live_call_active=False,
            foreground_load_micros=0,
            charging=True,
            minute_of_day=720,
        )
    )
    live_pressure = resource_policy.evaluate(
        SpeechReconciliationResourceRequest(
            mode="immediate",
            requested_factor=factor,
            user_max_factor=factor,
            live_call_active=True,
            foreground_load_micros=0,
            charging=True,
            minute_of_day=720,
        )
    )
    foreground_pressure = resource_policy.evaluate(
        SpeechReconciliationResourceRequest(
            mode="immediate",
            requested_factor=factor,
            user_max_factor=factor,
            live_call_active=False,
            foreground_load_micros=SpeechReconciliationResourcePolicy.MAX_FOREGROUND_LOAD_MICROS + 1,
            charging=True,
            minute_of_day=720,
        )
    )
    try:
        SpeechResourceVector(cpu_time_ms=1).subtract(SpeechResourceVector(cpu_time_ms=2))
        budget_overrun_blocked = False
    except SpeechReconciliationContractError:
        budget_overrun_blocked = True
    resource_limit = SpeechReconciliationPolicy().decide(
        SpeechReconciliationQualitySample(
            current_factor=factor,
            authorized_factor=factor,
            unresolved_high_quality_conflicts=1,
            quality_score=0.9,
            previous_quality_score=0.8,
            evidence_count=1,
            resource_remaining=False,
            evaluation_budget_reserved=True,
        )
    )
    if not admitted.allowed or admitted.effective_factor != factor:
        raise ProgramBenchmarkExecutionError("program_benchmark_offline_resource_admission_failed")

    stop = threading.Event()
    started = threading.Event()
    resolver_cycles = 0
    resolver = SpeechReconciliationResolver()

    def worker_load() -> None:
        nonlocal resolver_cycles
        started.set()
        while not stop.is_set() and time.monotonic() < deadline:
            resolved = resolver.resolve(candidates)
            if not resolved.publishable or resolved.transcript is None:
                return
            resolver_cycles += 1

    thread: threading.Thread | None = None
    if saturate:
        thread = threading.Thread(
            target=worker_load,
            name=f"semantic-media-offline-factor-{factor}",
            daemon=True,
        )
        thread.start()
        if not started.wait(timeout=1):
            raise ProgramBenchmarkExecutionError("program_benchmark_offline_saturation_not_started")
    executable = _PROBE_ROOT / "frontend-angular/node_modules/.bin/vite-node"
    if not executable.is_file():
        stop.set()
        if thread is not None:
            thread.join(timeout=1)
        raise ProgramBenchmarkExecutionError("program_benchmark_live_slo_runtime_missing")
    remaining = min(30.0, max(0.1, deadline - time.monotonic()))
    try:
        completed = subprocess.run(
            [
                str(executable),
                "scripts/benchmark/semantic_media_live_slo_probe.ts",
                "--iterations",
                str(iterations),
            ],
            cwd=_PROBE_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=remaining,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProgramBenchmarkExecutionError("program_benchmark_live_slo_probe_timeout") from exc
    finally:
        stop.set()
        if thread is not None:
            thread.join(timeout=2)
            if thread.is_alive():
                raise ProgramBenchmarkExecutionError("program_benchmark_offline_saturation_unbounded")
    if completed.returncode != 0:
        raise ProgramBenchmarkExecutionError("program_benchmark_live_slo_probe_failed")
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ProgramBenchmarkExecutionError("program_benchmark_live_slo_report_invalid") from exc
    expected_fields = {
        "schema",
        "iterations",
        "sound_p50_ms",
        "sound_p95_ms",
        "sound_p99_ms",
        "text_p50_ms",
        "text_p95_ms",
        "text_p99_ms",
        "ui_p50_ms",
        "ui_p95_ms",
        "ui_p99_ms",
        "projection_count",
        "probe_cpu_micros",
        "probe_ram_bytes",
    }
    if (
        not isinstance(report, Mapping)
        or set(report) != expected_fields
        or report.get("schema") != "ananta.semantic-media-live-slo-probe.v1"
        or any(type(value) is not int or value < 0 for name, value in report.items() if name != "schema")
    ):
        raise ProgramBenchmarkExecutionError("program_benchmark_live_slo_report_invalid")
    return {
        "saturation_active": saturate,
        "resolver_cycles": resolver_cycles,
        "resource_policy_admitted": admitted.allowed and admitted.effective_factor == factor,
        "live_pressure_blocked": not live_pressure.allowed and live_pressure.action == "pause",
        "foreground_pressure_blocked": not foreground_pressure.allowed and foreground_pressure.action == "pause",
        "budget_overrun_blocked": budget_overrun_blocked,
        "resource_limit_stopped": resource_limit.action == "stop"
        and resource_limit.reason_code == "speech_reconciliation_resource_limit",
        **{name: int(value) for name, value in report.items() if name != "schema"},
    }
