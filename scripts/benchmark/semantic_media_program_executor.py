#!/usr/bin/env python3
"""Bounded, content-free executor for the semantic-media program benchmark.

The executor measures work.  It does not manufacture latency samples and it
does not make rollout decisions.  Live rows use real IPv4 UDP loopback sockets
and the production secure-envelope/semantic contracts.  Offline rows exercise
the production speech reconciliation resolver.  The evaluator in
``semantic_media_program.py`` remains the release-policy owner.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import selectors
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from agent.services.semantic_media_program_evidence import (
    canonical_sha256,
    source_hash,
)
from worker.speech_reconciliation.resolver import SpeechReconciliationResolver
from scripts.benchmark.semantic_media_program_measurements import (
    _delivery_score,
    _measured,
    METRIC_FIELDS,
    MetricState,
    ModeMeasurement,
    _not_applicable,
    _percentile_ms,
    ProgramBenchmarkExecutionError,
    _unavailable,
    _worst_burst,
)
from scripts.benchmark.semantic_media_program_resources import (
    _hardware_descriptor,
    ResourceMonitor,
)
from scripts.benchmark.semantic_media_program_payloads import (
    _fixture_unit,
    _HEADER,
    _offline_candidates,
    _ordinary_evidence_value,
    _ordinary_value,
    _receive_loop,
    SecurePacketCodec,
    _semantic_speech_value,
    _semantic_visual_value,
)
from scripts.benchmark.semantic_media_program_live_probe import (
    _concurrent_live_slo_probe,
)


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "config/semantic-media-program-benchmark.v1.json"
PRODUCT_SOURCE_PATHS = (
    "agent/services/semantic_media_feature_flags.py",
    "agent/services/semantic_media_program_evidence.py",
    "agent/models/semantic_media_content_policy.py",
    "agent/services/speech_reconciliation_resource_policy.py",
    "ananta_contracts/speech_reconciliation.py",
    "ananta_contracts/speech_evidence_sync.py",
    "ananta_contracts/speech_evidence_sync_payloads.py",
    "ananta_contracts/speech_evidence_sync_primitives.py",
    "ananta_contracts/semantic_speech.py",
    "ananta_contracts/semantic_visual.py",
    "ananta_contracts/webrtc_datachannel.py",
    "ananta_contracts/webrtc_security.py",
    "config/semantic-media-program-benchmark.v1.json",
    "scripts/benchmark/semantic_media_program.py",
    "scripts/benchmark/semantic_media_program_executor.py",
    "scripts/benchmark/semantic_media_program_measurements.py",
    "scripts/benchmark/semantic_media_program_resources.py",
    "scripts/benchmark/semantic_media_program_payloads.py",
    "scripts/benchmark/semantic_media_program_live_probe.py",
    "scripts/benchmark/semantic_media_live_slo_probe.ts",
    "frontend-angular/src/app/services/semantic-speech-quality-controller.service.ts",
    "frontend-angular/src/app/services/speech-delay-buffer.service.ts",
    "frontend-angular/src/app/services/speech-transcript-revision.store.ts",
    "voice_runtime/speech_reconciliation_policy.py",
    "worker/speech_reconciliation/resolver.py",
)
QUALITY_EVIDENCE_PATHS = {
    "pair": ROOT / "artifacts/test-gates/semantic-visual.json",
    "group": ROOT / "artifacts/test-gates/semantic-visual.json",
    "evidence": ROOT / "artifacts/test-gates/speech-privacy.json",
    "offline": ROOT / "artifacts/test-gates/speech-reconciliation-factor.json",
}


class LoopbackScenarioExecutor:
    """Measure one live matrix point over real kernel UDP sockets."""

    def __init__(self, policy: Mapping[str, Any], *, deadline: float) -> None:
        self._policy = policy
        self._deadline = deadline

    def run(
        self,
        *,
        mode: str,
        topology: str,
        window_seconds: int,
        receivers: int,
        binding_sha256: str,
    ) -> ModeMeasurement:
        self._require_time()
        fixture = self._policy["source_fixture"]
        units = window_seconds * int(fixture["units_per_second"])
        raw_units = tuple(
            _fixture_unit(self._policy["seed"], index, int(fixture["ordinary_unit_bytes"]))
            for index in range(units)
        )
        if topology == "evidence":
            transform = _ordinary_evidence_value if mode == "ordinary" else _semantic_speech_value
        else:
            transform = _ordinary_value if mode == "ordinary" else _semantic_visual_value
        codec = SecurePacketCodec(binding_sha256=binding_sha256, mode=mode, topology=topology)
        expected_values: dict[int, bytes] = {}
        packets: list[bytes] = []
        monitor = ResourceMonitor()
        monitor.start()
        receiver_sockets: list[socket.socket] = []
        sender: socket.socket | None = None
        selector = selectors.DefaultSelector()
        try:
            for index, raw in enumerate(raw_units, start=1):
                expected_values[index] = transform(raw, index, binding_sha256)
                sealed = codec.seal(index, expected_values[index])
                packet = _HEADER.pack(index, 0) + sealed
                if len(packet) > int(self._policy["execution_limits"]["maximum_datagram_bytes"]):
                    raise ProgramBenchmarkExecutionError("program_benchmark_datagram_limit_exceeded")
                packets.append(packet)
            recovery_sequence = units + 1
            expected_values[recovery_sequence] = transform(raw_units[-1], recovery_sequence, binding_sha256)
            recovery_packet = _HEADER.pack(recovery_sequence, 0) + codec.seal(
                recovery_sequence, expected_values[recovery_sequence]
            )
            for _ in range(receivers):
                receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
                receiver.bind(("127.0.0.1", 0))
                receiver.setblocking(False)
                receiver_sockets.append(receiver)
                selector.register(receiver, selectors.EVENT_READ)
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            expected_deliveries = units * receivers + 1
            state: dict[str, Any] = {
                "completed": 0,
                "valid": 0,
                "ingress": 0,
                "latencies": [],
                "recovery_received_ns": None,
            }
            stop = threading.Event()
            receiver_thread = threading.Thread(
                target=_receive_loop,
                kwargs={
                    "selector": selector,
                    "codec": codec,
                    "expected_values": expected_values,
                    "state": state,
                    "expected_deliveries": expected_deliveries,
                    "recovery_sequence": recovery_sequence,
                    "stop": stop,
                    "deadline": min(
                        self._deadline,
                        time.monotonic() + int(self._policy["execution_limits"]["maximum_row_seconds"]),
                    ),
                },
                name="semantic-media-program-loopback-receiver",
                daemon=True,
            )
            receiver_thread.start()
            egress = 0
            send_events: list[tuple[int, int]] = []
            for sequence, packet in enumerate(packets, start=1):
                for receiver in receiver_sockets:
                    sent_ns = time.perf_counter_ns()
                    stamped = _HEADER.pack(sequence, sent_ns) + packet[_HEADER.size :]
                    sent = sender.sendto(stamped, receiver.getsockname())
                    egress += sent
                    send_events.append((sent_ns, sent))
            sender.close()
            sender = None
            recovery_started_ns = time.perf_counter_ns()
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            stamped_recovery = _HEADER.pack(recovery_sequence, recovery_started_ns) + recovery_packet[_HEADER.size :]
            sent = sender.sendto(stamped_recovery, receiver_sockets[0].getsockname())
            egress += sent
            send_events.append((recovery_started_ns, sent))
            receiver_thread.join(
                timeout=min(
                    int(self._policy["execution_limits"]["receive_grace_milliseconds"]) / 1000,
                    max(0.01, self._deadline - time.monotonic()),
                )
            )
            stop.set()
            receiver_thread.join(timeout=1)
            if receiver_thread.is_alive():
                raise ProgramBenchmarkExecutionError("program_benchmark_receive_loop_unbounded")
            resource_values, resource_availability = monitor.stop()
            monitor = None  # type: ignore[assignment]
            latencies = tuple(float(value) for value in state["latencies"])
            recovery_received = state["recovery_received_ns"]
            recovery_ms = (
                max(1, math.ceil((int(recovery_received) - recovery_started_ns) / 1_000_000))
                if recovery_received is not None
                else None
            )
            values: dict[str, int | None] = {
                **resource_values,
                "ingress_bytes": int(state["ingress"]),
                "egress_bytes": egress,
                "turn_bytes": None,
                "latency_p50_ms": _percentile_ms(latencies, 0.50),
                "latency_p95_ms": _percentile_ms(latencies, 0.95),
                "latency_p99_ms": _percentile_ms(latencies, 0.99),
                "worst_burst_bytes": _worst_burst(send_events),
                "recovery_ms": recovery_ms,
            }
            availability: dict[str, MetricState] = {
                **resource_availability,
                "ingress_bytes": _measured("udp_socket_receive_count"),
                "egress_bytes": _measured("udp_socket_send_count"),
                "turn_bytes": _unavailable("turn_endpoint_not_configured"),
                "latency_p50_ms": _measured("perf_counter_packet_timestamps"),
                "latency_p95_ms": _measured("perf_counter_packet_timestamps"),
                "latency_p99_ms": _measured("perf_counter_packet_timestamps"),
                "worst_burst_bytes": _measured("ten_millisecond_send_buckets"),
                "recovery_ms": _measured("udp_sender_recreation_probe")
                if recovery_ms is not None
                else _unavailable("recovery_probe_not_received"),
            }
            return ModeMeasurement(
                values=values,
                availability=availability,
                latency_samples=latencies,
                expected_deliveries=expected_deliveries,
                completed_deliveries=int(state["completed"]),
                valid_deliveries=int(state["valid"]),
                saturation=None,
            )
        finally:
            if isinstance(monitor, ResourceMonitor):
                try:
                    monitor.stop()
                except ProgramBenchmarkExecutionError:
                    pass
            if sender is not None:
                sender.close()
            for receiver in receiver_sockets:
                try:
                    selector.unregister(receiver)
                except (KeyError, ValueError):
                    pass
                receiver.close()
            selector.close()

    def _require_time(self) -> None:
        if time.monotonic() >= self._deadline:
            raise ProgramBenchmarkExecutionError("program_benchmark_execution_timeout")


class OfflineScenarioExecutor:
    """Measure the real deterministic reconciliation resolver at one factor."""

    def __init__(self, policy: Mapping[str, Any], *, deadline: float) -> None:
        self._policy = policy
        self._deadline = deadline
        self._resolver = SpeechReconciliationResolver()

    def run(self, *, mode: str, factor: int, binding_sha256: str) -> ModeMeasurement:
        if time.monotonic() >= self._deadline:
            raise ProgramBenchmarkExecutionError("program_benchmark_execution_timeout")
        candidates = _offline_candidates(factor, binding_sha256)
        iterations = int(self._policy["execution_limits"]["offline_iterations"])
        expected = "alpha beta gamma delta"
        latencies: list[float] = []
        valid = 0
        burst = 0
        monitor = ResourceMonitor()
        monitor.start()
        recovery_ms: int | None = None
        saturation: Mapping[str, int | bool] | None = None
        try:
            for _ in range(iterations):
                started = time.perf_counter_ns()
                if mode == "ordinary":
                    result = candidates[0].transcript.text
                    hashlib.sha256(result.encode()).digest()
                else:
                    resolved = self._resolver.resolve(candidates)
                    result = resolved.transcript.text if resolved.publishable and resolved.transcript else ""
                latencies.append((time.perf_counter_ns() - started) / 1_000_000)
                valid += int(result.casefold().strip(".") == expected)
            checkpoint = json.dumps(
                {
                    "binding_sha256": binding_sha256,
                    "factor": factor,
                    "mode": mode,
                    "candidate_digest": canonical_sha256([item.transcript.candidate_id for item in candidates]),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            burst = len(checkpoint)
            with tempfile.TemporaryDirectory(prefix="ananta-program-benchmark-") as directory:
                checkpoint_path = Path(directory) / "checkpoint.json"
                recovery_started = time.perf_counter_ns()
                with checkpoint_path.open("wb") as handle:
                    handle.write(checkpoint)
                    handle.flush()
                    os.fsync(handle.fileno())
                recovered = checkpoint_path.read_bytes()
                recovery_ms = max(1, math.ceil((time.perf_counter_ns() - recovery_started) / 1_000_000))
                if recovered != checkpoint:
                    recovery_ms = None
            saturation = _concurrent_live_slo_probe(
                factor=factor,
                candidates=candidates,
                saturate=mode == "semantic",
                iterations=max(8, iterations * 2),
                deadline=self._deadline,
            )
            resource_values, resource_availability = monitor.stop()
            monitor = None  # type: ignore[assignment]
            resource_values["cpu_micros"] = int(resource_values["cpu_micros"] or 0) + int(
                saturation["probe_cpu_micros"]
            )
            resource_values["ram_bytes"] = int(resource_values["ram_bytes"] or 0) + int(
                saturation["probe_ram_bytes"]
            )
            resource_availability["cpu_micros"] = _measured("process_time_plus_node_cpu_usage")
            resource_availability["ram_bytes"] = _measured("proc_rss_plus_node_rss")
        finally:
            if isinstance(monitor, ResourceMonitor):
                try:
                    monitor.stop()
                except ProgramBenchmarkExecutionError:
                    pass
        not_applicable = _not_applicable("offline_workload_has_no_network_transport")
        values: dict[str, int | None] = {
            **resource_values,
            "ingress_bytes": None,
            "egress_bytes": None,
            "turn_bytes": None,
            "latency_p50_ms": _percentile_ms(latencies, 0.50),
            "latency_p95_ms": _percentile_ms(latencies, 0.95),
            "latency_p99_ms": _percentile_ms(latencies, 0.99),
            "worst_burst_bytes": burst,
            "recovery_ms": recovery_ms,
        }
        availability: dict[str, MetricState] = {
            **resource_availability,
            "ingress_bytes": not_applicable,
            "egress_bytes": not_applicable,
            "turn_bytes": not_applicable,
            "latency_p50_ms": _measured("perf_counter_resolver_iterations"),
            "latency_p95_ms": _measured("perf_counter_resolver_iterations"),
            "latency_p99_ms": _measured("perf_counter_resolver_iterations"),
            "worst_burst_bytes": _measured("checkpoint_write_size"),
            "recovery_ms": _measured("fsync_checkpoint_restore")
            if recovery_ms is not None
            else _unavailable("checkpoint_restore_failed"),
        }
        return ModeMeasurement(
            values=values,
            availability=availability,
            latency_samples=tuple(latencies),
            expected_deliveries=iterations,
            completed_deliveries=iterations,
            valid_deliveries=valid,
            saturation=saturation,
        )


def run_benchmark(*, timeout_seconds: int | None = None) -> dict[str, Any]:
    policy = _load_policy()
    maximum = int(policy["execution_limits"]["maximum_run_seconds"])
    bounded_timeout = maximum if timeout_seconds is None else int(timeout_seconds)
    if bounded_timeout < 10 or bounded_timeout > maximum:
        raise ProgramBenchmarkExecutionError("program_benchmark_timeout_invalid")
    deadline = time.monotonic() + bounded_timeout
    source_digest = source_hash(ROOT, PRODUCT_SOURCE_PATHS)
    policy_digest = source_hash(ROOT, ("config/semantic-media-program-benchmark.v1.json",))
    hardware_digest = canonical_sha256(_hardware_descriptor())
    model_digest = canonical_sha256(
        {
            "visual_contract": "ananta.semantic-scene.v1",
            "speech_contract": "ananta.semantic-speech.v1",
            "offline_resolver": "SpeechReconciliationResolver",
            "weights": "none-deterministic-contract-path",
        }
    )
    fixture_digest = canonical_sha256(policy["source_fixture"])
    quality_bindings = {
        topology: _quality_binding(path) for topology, path in QUALITY_EVIDENCE_PATHS.items()
    }
    config = {
        "duration_seconds": int(policy["source_fixture"]["duration_seconds"]),
        "width": int(policy["source_fixture"]["width"]),
        "height": int(policy["source_fixture"]["height"]),
        "framerate": int(policy["source_fixture"]["framerate"]),
        "audio_format": str(policy["source_fixture"]["audio_format"]),
        "network_profiles": list(policy["matrix"]["network_profiles"]),
        "hardware_sha256": hardware_digest,
        "model_sha256": model_digest,
        "policy_sha256": policy_digest,
        "source_sha256": source_digest,
        "fixture_sha256": fixture_digest,
        "seed": int(policy["seed"]),
        "timeout_seconds": bounded_timeout,
        "execution_mode": "measured-product-contract-loopback",
        "quality_bindings": quality_bindings,
    }
    rows: list[dict[str, Any]] = []
    live_executor = LoopbackScenarioExecutor(policy, deadline=deadline)
    for topology in policy["matrix"]["live_topologies"]:
        for window in policy["matrix"]["windows_seconds"]:
            for receivers in policy["matrix"]["receiver_counts"]:
                binding = _comparison_binding(
                    config,
                    topology=str(topology),
                    window_seconds=int(window),
                    receivers=int(receivers),
                    offline_factor=1,
                    network_profile=str(policy["matrix"]["network_profiles"][0]),
                )
                order = ("semantic", "ordinary") if int(binding[0], 16) % 2 else ("ordinary", "semantic")
                measured = {
                    mode: live_executor.run(
                        mode=mode,
                        topology=str(topology),
                        window_seconds=int(window),
                        receivers=int(receivers),
                        binding_sha256=binding,
                    )
                    for mode in order
                }
                rows.append(
                    _row(
                        config=config,
                        topology=str(topology),
                        window_seconds=int(window),
                        receivers=int(receivers),
                        offline_factor=1,
                        network_profile=str(policy["matrix"]["network_profiles"][0]),
                        binding=binding,
                        ordinary=measured["ordinary"],
                        semantic=measured["semantic"],
                        quality_binding=quality_bindings[str(topology)],
                        minimum_quality=int(policy["thresholds"]["minimum_quality_score_micros"]),
                    )
                )
    offline_executor = OfflineScenarioExecutor(policy, deadline=deadline)
    for factor in policy["matrix"]["offline_factors"]:
        binding = _comparison_binding(
            config,
            topology="offline",
            window_seconds=20,
            receivers=2,
            offline_factor=int(factor),
            network_profile="offline",
        )
        order = ("semantic", "ordinary") if int(binding[0], 16) % 2 else ("ordinary", "semantic")
        measured = {
            mode: offline_executor.run(mode=mode, factor=int(factor), binding_sha256=binding)
            for mode in order
        }
        rows.append(
            _row(
                config=config,
                topology="offline",
                window_seconds=20,
                receivers=2,
                offline_factor=int(factor),
                network_profile="offline",
                binding=binding,
                ordinary=measured["ordinary"],
                semantic=measured["semantic"],
                quality_binding=quality_bindings["offline"],
                minimum_quality=int(policy["thresholds"]["minimum_quality_score_micros"]),
            )
        )
    return {
        "schema": "ananta.semantic-media-program-benchmark.v2",
        "run_config": config,
        "measurement_contract": {
            "clock": "perf_counter_ns",
            "live_transport": "udp-ipv4-loopback",
            "security": "production-aes-gcm-envelope",
            "offline_runtime": "production-speech-reconciliation-resolver",
            "metric_fields": list(METRIC_FIELDS),
            "policy_sha256": policy_digest,
        },
        "rows": rows,
    }


def current_source_sha256() -> str:
    return source_hash(ROOT, PRODUCT_SOURCE_PATHS)


def current_policy() -> dict[str, Any]:
    return _load_policy()


def current_quality_bindings() -> dict[str, dict[str, Any]]:
    return {topology: _quality_binding(path) for topology, path in QUALITY_EVIDENCE_PATHS.items()}


def _row(
    *,
    config: Mapping[str, Any],
    topology: str,
    window_seconds: int,
    receivers: int,
    offline_factor: int,
    network_profile: str,
    binding: str,
    ordinary: ModeMeasurement,
    semantic: ModeMeasurement,
    quality_binding: Mapping[str, Any],
    minimum_quality: int,
) -> dict[str, Any]:
    ordinary_score = _delivery_score(ordinary)
    semantic_score = _delivery_score(semantic)
    external_passed = quality_binding["status"] == "passed"
    quality_values = {
        "ordinary_score_micros": ordinary_score,
        "semantic_score_micros": semantic_score,
        "minimum_score_micros": minimum_quality,
        "external_gate_passed": external_passed,
        "passed": ordinary_score >= minimum_quality
        and semantic_score >= minimum_quality
        and external_passed,
        "external_evidence_sha256": quality_binding["evidence_sha256"],
    }
    quality = {**quality_values, "decision_sha256": canonical_sha256(quality_values)}
    ordinary_egress = ordinary.values.get("egress_bytes")
    semantic_egress = semantic.values.get("egress_bytes")
    claimed_savings = (
        quality["passed"] is True
        and isinstance(ordinary_egress, int)
        and isinstance(semantic_egress, int)
        and semantic_egress < ordinary_egress
    )
    return {
        "topology": topology,
        "window_seconds": window_seconds,
        "receivers": receivers,
        "offline_factor": offline_factor,
        "network_profile": network_profile,
        "comparison_binding_sha256": binding,
        "quality": quality,
        "claimed_savings": claimed_savings,
        "ordinary": ordinary.as_dict(binding_sha256=binding),
        "semantic": semantic.as_dict(binding_sha256=binding),
    }


def _comparison_binding(
    config: Mapping[str, Any],
    *,
    topology: str,
    window_seconds: int,
    receivers: int,
    offline_factor: int,
    network_profile: str,
) -> str:
    return canonical_sha256(
        {
            "hardware_sha256": config["hardware_sha256"],
            "model_sha256": config["model_sha256"],
            "policy_sha256": config["policy_sha256"],
            "source_sha256": config["source_sha256"],
            "fixture_sha256": config["fixture_sha256"],
            "topology": topology,
            "window_seconds": window_seconds,
            "receivers": receivers,
            "offline_factor": offline_factor,
            "network_profile": network_profile,
        }
    )


def recompute_comparison_binding(config: Mapping[str, Any], row: Mapping[str, Any]) -> str:
    return _comparison_binding(
        config,
        topology=str(row["topology"]),
        window_seconds=int(row["window_seconds"]),
        receivers=int(row["receivers"]),
        offline_factor=int(row["offline_factor"]),
        network_profile=str(row["network_profile"]),
    )


def _quality_binding(path: Path) -> dict[str, Any]:
    try:
        encoded = path.read_bytes()
        document = json.loads(encoded)
    except (OSError, json.JSONDecodeError):
        return {"status": "unavailable", "evidence_sha256": None}
    status = document.get("status")
    passed = status == "passed" if isinstance(status, str) else document.get("passed") is True
    return {
        "status": "passed" if passed else "failed",
        "evidence_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _load_policy() -> dict[str, Any]:
    try:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProgramBenchmarkExecutionError("program_benchmark_policy_invalid") from exc
    if policy.get("schema") != "ananta.semantic-media-program-benchmark-policy.v1":
        raise ProgramBenchmarkExecutionError("program_benchmark_policy_invalid")
    return policy


__all__ = [
    "METRIC_FIELDS",
    "PRODUCT_SOURCE_PATHS",
    "ProgramBenchmarkExecutionError",
    "current_policy",
    "current_quality_bindings",
    "current_source_sha256",
    "recompute_comparison_binding",
    "run_benchmark",
]
