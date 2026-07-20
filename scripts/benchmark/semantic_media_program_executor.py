#!/usr/bin/env python3
"""Bounded, content-free executor for the semantic-media program benchmark.

The executor measures work.  It does not manufacture latency samples and it
does not make rollout decisions.  Live rows use real IPv4 UDP loopback sockets
and the production secure-envelope/semantic contracts.  Offline rows exercise
the production speech reconciliation resolver.  The evaluator in
``semantic_media_program.py`` remains the release-policy owner.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import platform
import resource
import selectors
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from agent.services.semantic_media_program_evidence import canonical_sha256, source_hash
from agent.services.speech_reconciliation_resource_policy import (
    SpeechReconciliationResourcePolicy,
    SpeechReconciliationResourceRequest,
)
from ananta_contracts.semantic_speech import validate_semantic_frame
from ananta_contracts.semantic_visual import canonical_json as canonical_visual_json
from ananta_contracts.semantic_visual import validate_semantic_scene
from ananta_contracts.speech_evidence_sync import (
    canonical_json as canonical_evidence_json,
)
from ananta_contracts.speech_evidence_sync import validate_payload as validate_evidence_payload
from ananta_contracts.speech_reconciliation import SpeechReconciliationContractError, SpeechResourceVector
from ananta_contracts.webrtc_security import (
    AuthenticatedMetadata,
    EnvelopeRecipient,
    EnvelopeScope,
    SecureEnvelopeV1,
    canonical_security_json,
    open_secure_envelope,
    seal_secure_envelope,
    validate_secure_envelope,
)
from voice_runtime.backends.base import TranscriptionCandidate
from voice_runtime.peer_transcript_consensus import PeerTranscriptCandidate
from voice_runtime.speech_reconciliation_policy import (
    SpeechReconciliationPolicy,
    SpeechReconciliationQualitySample,
)
from worker.speech_reconciliation.resolver import SpeechReconciliationResolver

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "config/semantic-media-program-benchmark.v1.json"
PRODUCT_SOURCE_PATHS = (
    "agent/services/semantic_media_feature_flags.py",
    "agent/services/semantic_media_program_evidence.py",
    "agent/services/speech_reconciliation_resource_policy.py",
    "ananta_contracts/speech_reconciliation.py",
    "ananta_contracts/speech_evidence_sync.py",
    "ananta_contracts/semantic_speech.py",
    "ananta_contracts/semantic_visual.py",
    "ananta_contracts/webrtc_datachannel.py",
    "ananta_contracts/webrtc_security.py",
    "config/semantic-media-program-benchmark.v1.json",
    "scripts/benchmark/semantic_media_program.py",
    "scripts/benchmark/semantic_media_program_executor.py",
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
METRIC_FIELDS = (
    "ingress_bytes",
    "egress_bytes",
    "turn_bytes",
    "cpu_micros",
    "gpu_micros",
    "ram_bytes",
    "vram_bytes",
    "disk_bytes",
    "energy_microwh",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "worst_burst_bytes",
    "recovery_ms",
    "open_resources",
)
_HEADER = struct.Struct("!QQ")
_FIXED_EXPIRY_MS = 4_102_444_800_000
_NVIDIA_SMI = shutil.which("nvidia-smi")


class ProgramBenchmarkExecutionError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class MetricState:
    status: str
    method: str
    reason_code: str

    def as_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "method": self.method,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class ModeMeasurement:
    values: Mapping[str, int | None]
    availability: Mapping[str, MetricState]
    latency_samples: tuple[float, ...]
    expected_deliveries: int
    completed_deliveries: int
    valid_deliveries: int
    saturation: Mapping[str, int | bool] | None = None

    def as_dict(self, *, binding_sha256: str) -> dict[str, Any]:
        return {
            "binding_sha256": binding_sha256,
            "values": dict(self.values),
            "availability": {
                name: self.availability[name].as_dict() for name in METRIC_FIELDS
            },
            "latency_sample_count": len(self.latency_samples),
            "expected_deliveries": self.expected_deliveries,
            "completed_deliveries": self.completed_deliveries,
            "valid_deliveries": self.valid_deliveries,
            "offline_saturation": dict(self.saturation) if self.saturation is not None else None,
        }


@dataclass(frozen=True, slots=True)
class _ResourceStart:
    wall_ns: int
    cpu_ns: int
    io_bytes: int | None
    energy_uj: int | None
    gpu: tuple[int, int] | None


class ResourceMonitor:
    """Measure process resources behind one small sampling interface (SRP)."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._maximum_rss = _rss_bytes()
        self._maximum_fds = _open_file_descriptors()
        self._maximum_vram = 0
        self._gpu_utilization_sum = 0
        self._gpu_samples = 0
        self._thread: threading.Thread | None = None
        self._start: _ResourceStart | None = None

    def start(self) -> None:
        if self._start is not None:
            raise ProgramBenchmarkExecutionError("program_benchmark_monitor_reused")
        self._start = _ResourceStart(
            wall_ns=time.perf_counter_ns(),
            cpu_ns=time.process_time_ns(),
            io_bytes=_process_io_bytes(),
            energy_uj=_energy_uj(),
            gpu=_gpu_snapshot(),
        )
        if self._start.gpu is not None:
            self._maximum_vram = self._start.gpu[1]
        self._thread = threading.Thread(target=self._sample, name="semantic-media-program-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> tuple[dict[str, int | None], dict[str, MetricState]]:
        if self._start is None:
            raise ProgramBenchmarkExecutionError("program_benchmark_monitor_not_started")
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                raise ProgramBenchmarkExecutionError("program_benchmark_monitor_unbounded")
        cpu_micros = max(1, (time.process_time_ns() - self._start.cpu_ns) // 1000)
        end_io = _process_io_bytes()
        end_energy = _energy_uj()
        end_gpu = _gpu_snapshot()
        values: dict[str, int | None] = {
            "cpu_micros": cpu_micros,
            "ram_bytes": self._maximum_rss,
            "open_resources": self._maximum_fds,
            "disk_bytes": None,
            "energy_microwh": None,
            "gpu_micros": None,
            "vram_bytes": None,
        }
        availability = {
            "cpu_micros": _measured("process_time_ns"),
            "ram_bytes": _measured("proc_rss_sampler"),
            "open_resources": _measured("proc_fd_sampler"),
            "disk_bytes": _unavailable("proc_io_unavailable"),
            "energy_microwh": _unavailable("rapl_energy_counter_unavailable"),
            "gpu_micros": _unavailable("gpu_sampler_unavailable"),
            "vram_bytes": _unavailable("gpu_sampler_unavailable"),
        }
        if self._start.io_bytes is not None and end_io is not None:
            values["disk_bytes"] = max(0, end_io - self._start.io_bytes)
            availability["disk_bytes"] = _measured("proc_process_io")
        if self._start.energy_uj is not None and end_energy is not None and end_energy >= self._start.energy_uj:
            values["energy_microwh"] = (end_energy - self._start.energy_uj) * 1000 // 3600
            availability["energy_microwh"] = _measured("linux_rapl_energy_counter")
        if self._start.gpu is not None and end_gpu is not None:
            wall_micros = max(1, (time.perf_counter_ns() - self._start.wall_ns) // 1000)
            average_utilization = (
                self._gpu_utilization_sum // self._gpu_samples
                if self._gpu_samples
                else (self._start.gpu[0] + end_gpu[0]) // 2
            )
            values["gpu_micros"] = wall_micros * average_utilization // 100
            values["vram_bytes"] = max(self._maximum_vram, self._start.gpu[1], end_gpu[1])
            availability["gpu_micros"] = _measured("nvidia_smi_device_sampler")
            availability["vram_bytes"] = _measured("nvidia_smi_device_sampler")
        return values, availability

    def _sample(self) -> None:
        while not self._stop.wait(0.01):
            self._maximum_rss = max(self._maximum_rss, _rss_bytes())
            self._maximum_fds = max(self._maximum_fds, _open_file_descriptors())
            gpu = _gpu_snapshot()
            if gpu is not None:
                self._gpu_utilization_sum += gpu[0]
                self._gpu_samples += 1
                self._maximum_vram = max(self._maximum_vram, gpu[1])


class SecurePacketCodec:
    """Uses the production AES-GCM envelope for both comparison arms."""

    def __init__(self, *, binding_sha256: str, mode: str, topology: str) -> None:
        self._binding = binding_sha256
        self._mode = mode
        self._topology = topology
        self._key = hashlib.sha256(f"{binding_sha256}:{mode}:key".encode()).digest()

    def seal(self, sequence: int, value: bytes) -> bytes:
        nonce = hashlib.sha256(f"{self._binding}:{self._mode}:{sequence}".encode()).digest()[:12]
        envelope = SecureEnvelopeV1(
            version=1,
            scope=EnvelopeScope("room" if self._topology == "group" else "session", "benchmark-session"),
            sender_id="benchmark-sender",
            recipient=EnvelopeRecipient("group" if self._topology == "group" else "peer", "benchmark-audience"),
            epoch=1,
            sequence=sequence,
            key_id="benchmark-key",
            payload_type=f"{self._topology}.{self._mode}.v1",
            expires_at_ms=_FIXED_EXPIRY_MS,
            nonce_b64=base64.b64encode(nonce).decode("ascii"),
            aad=AuthenticatedMetadata(
                "bulk" if self._topology == "evidence" else ("media" if self._mode == "ordinary" else "semantic"),
                "binary",
                self._binding,
            ),
            ciphertext_b64="",
        )
        sealed = seal_secure_envelope(key=self._key, plaintext=value, envelope=envelope)
        return canonical_security_json(sealed.to_dict())

    def open(self, value: bytes) -> bytes:
        try:
            raw = json.loads(value)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProgramBenchmarkExecutionError("program_benchmark_secure_packet_invalid") from exc
        envelope = validate_secure_envelope(raw, check_time=False)
        if envelope.aad.contract_digest != self._binding:
            raise ProgramBenchmarkExecutionError("program_benchmark_secure_packet_binding_mismatch")
        return open_secure_envelope(key=self._key, envelope=envelope)


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


def _receive_loop(
    *,
    selector: selectors.BaseSelector,
    codec: SecurePacketCodec,
    expected_values: Mapping[int, bytes],
    state: dict[str, Any],
    expected_deliveries: int,
    recovery_sequence: int,
    stop: threading.Event,
    deadline: float,
) -> None:
    while not stop.is_set() and state["completed"] < expected_deliveries and time.monotonic() < deadline:
        for key, _ in selector.select(timeout=0.01):
            receiver = key.fileobj
            if not isinstance(receiver, socket.socket):
                continue
            while True:
                try:
                    packet, _address = receiver.recvfrom(65_535)
                except BlockingIOError:
                    break
                received_ns = time.perf_counter_ns()
                if len(packet) < _HEADER.size:
                    continue
                sequence, sent_ns = _HEADER.unpack_from(packet)
                state["completed"] += 1
                state["ingress"] += len(packet)
                state["latencies"].append(max(0.0, (received_ns - sent_ns) / 1_000_000))
                try:
                    opened = codec.open(packet[_HEADER.size :])
                except (ProgramBenchmarkExecutionError, ValueError):
                    opened = b""
                state["valid"] += int(opened == expected_values.get(sequence))
                if sequence == recovery_sequence:
                    state["recovery_received_ns"] = received_ns


def _ordinary_value(raw: bytes, _sequence: int, _binding: str) -> bytes:
    return raw


def _ordinary_evidence_value(raw: bytes, sequence: int, binding: str) -> bytes:
    return _evidence_chunk(raw, sequence, binding, arm="ordinary")


def _semantic_visual_value(raw: bytes, sequence: int, binding: str) -> bytes:
    digest = hashlib.sha256(raw).hexdigest()
    scene = {
        "schema": "ananta.semantic-scene.v1",
        "scene_id": f"scene-{sequence}",
        "session_id": "benchmark-session",
        "contract_id": "benchmark-contract",
        "contract_digest": binding,
        "epoch": 1,
        "sequence": sequence,
        "source_frame_digest": digest,
        "coordinate_space": {"unit": "normalized", "origin": "top_left", "width": 1, "height": 1},
        "timebase": {"unit": "milliseconds", "captured_at_ms": sequence * 250, "duration_ms": 250},
        "provenance": {
            "source": "heuristic",
            "algorithm": "benchmark-contract-probe",
            "version": "1.0.0",
            "authoritative": False,
        },
        "nodes": [],
        "security": {"classification": "derived_semantic_metadata", "raw_media_included": False},
    }
    validated = validate_semantic_scene(scene)
    return canonical_visual_json(validated, max_bytes=256 * 1024)


def _semantic_speech_value(raw: bytes, sequence: int, binding: str) -> bytes:
    digest = hashlib.sha256(raw).hexdigest()
    buckets = tuple(round(sum(raw[index::8]) / max(1, len(raw[index::8])) / 255, 6) for index in range(8))
    frame = validate_semantic_frame(
        {
            "context": {
                "session_id": "benchmark-session",
                "epoch": 1,
                "turn_id": f"turn-{sequence}",
                "revision": 1,
                "sender_id": "benchmark-sender",
                "audience_id": "benchmark-audience",
                "consent_version": 1,
                "expires_at_ms": _FIXED_EXPIRY_MS,
                "contract_digest": binding,
                "source_digest": digest,
            },
            "frame_id": f"speech-frame-{sequence}",
            "algorithm_version": "benchmark-contract-probe.v1",
            "start_ms": (sequence - 1) * 250,
            "end_ms": sequence * 250,
            "confidence": 1.0,
            "prosody": buckets,
            "residual": (),
        }
    )
    semantic_bytes = json.dumps(
        frame.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return _evidence_chunk(semantic_bytes, sequence, binding, arm="semantic")


def _evidence_chunk(value: bytes, sequence: int, binding: str, *, arm: str) -> bytes:
    nonce = hashlib.sha256(f"{binding}:evidence:{arm}:{sequence}".encode()).digest()[:12]
    key = hashlib.sha256(f"{binding}:evidence-key:{arm}".encode()).digest()
    encrypted = AESGCM(key).encrypt(nonce, value, binding.encode())
    chunk = {
        "traffic_class": "evidence_bulk",
        "offer_id": "benchmark-offer",
        "group_id": "benchmark-group",
        "chunk_index": sequence - 1,
        "chunk_count": sequence,
        "plaintext_bytes": len(value),
        "plaintext_digest": hashlib.sha256(value).hexdigest(),
        "ciphertext_digest": hashlib.sha256(encrypted).hexdigest(),
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "ciphertext_b64": base64.b64encode(encrypted).decode("ascii"),
    }
    validated = validate_evidence_payload("chunk", chunk)
    return canonical_evidence_json(validated)


def _fixture_unit(seed: int, index: int, size: int) -> bytes:
    """Return a deterministic, non-media byte fixture without retaining it."""

    seed_bytes = hashlib.sha256(f"{seed}:{index}".encode()).digest()
    block = bytearray(size)
    for offset in range(size):
        # Bounded repeating gradients model change without embedding user data.
        block[offset] = (seed_bytes[offset % len(seed_bytes)] + index + offset // 64) % 256
    return bytes(block)


def _offline_candidates(factor: int, binding_sha256: str) -> tuple[PeerTranscriptCandidate, ...]:
    rows: list[PeerTranscriptCandidate] = []
    for index in range(factor):
        candidate_id = f"candidate-{index + 1}"
        transcript = TranscriptionCandidate(
            candidate_id=candidate_id,
            backend="benchmark-local",
            model="deterministic-contract-probe",
            model_revision="1.0.0",
            manifest_digest=hashlib.sha256(b"benchmark-model-manifest").hexdigest(),
            text="alpha beta gamma delta",
            confidence=1.0,
            status="succeeded",
            source_audio_digest=binding_sha256,
            lineage_id=candidate_id,
        )
        rows.append(
            PeerTranscriptCandidate(
                transcript=transcript,
                source_id=f"benchmark-source-{index + 1}",
                source_family=binding_sha256,
                contributor_digest=hashlib.sha256(f"contributor:{index}".encode()).hexdigest(),
                revision=1,
                lineage_digest=hashlib.sha256(f"lineage:{index}".encode()).hexdigest(),
                signature_digest=hashlib.sha256(f"signature:{index}".encode()).hexdigest(),
                authority_micros=1_000_000,
                quality_micros=1_000_000,
            )
        )
    return tuple(rows)


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
    executable = ROOT / "frontend-angular/node_modules/.bin/vite-node"
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
            cwd=ROOT,
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


def _delivery_score(value: ModeMeasurement) -> int:
    if value.expected_deliveries <= 0:
        return 0
    return value.valid_deliveries * 1_000_000 // value.expected_deliveries


def _load_policy() -> dict[str, Any]:
    try:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProgramBenchmarkExecutionError("program_benchmark_policy_invalid") from exc
    if policy.get("schema") != "ananta.semantic-media-program-benchmark-policy.v1":
        raise ProgramBenchmarkExecutionError("program_benchmark_policy_invalid")
    return policy


def _hardware_descriptor() -> dict[str, Any]:
    return {
        "system": platform.system(),
        "machine": platform.machine(),
        "kernel": platform.release(),
        "logical_cpus": os.cpu_count() or 1,
        "page_size": int(os.sysconf("SC_PAGE_SIZE")),
        "physical_memory_bytes": int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")),
        "gpu_sampler": _gpu_snapshot() is not None,
        "energy_sampler": _energy_uj() is not None,
    }


def _rss_bytes() -> int:
    try:
        fields = (Path("/proc/self/statm")).read_text(encoding="ascii").split()
        return int(fields[1]) * int(os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, IndexError):
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if platform.system() == "Darwin" else value * 1024)


def _open_file_descriptors() -> int:
    try:
        return len(tuple(Path("/proc/self/fd").iterdir()))
    except OSError:
        return 0


def _process_io_bytes() -> int | None:
    try:
        values = {
            key.rstrip(":"): int(value)
            for key, value in (line.split() for line in Path("/proc/self/io").read_text(encoding="ascii").splitlines())
        }
        return values["read_bytes"] + values["write_bytes"]
    except (OSError, ValueError, KeyError):
        return None


def _energy_uj() -> int | None:
    paths = sorted(Path("/sys/class/powercap").glob("**/energy_uj"))
    if not paths:
        return None
    try:
        return sum(int(path.read_text(encoding="ascii").strip()) for path in paths)
    except (OSError, ValueError):
        return None


def _gpu_snapshot() -> tuple[int, int] | None:
    if _NVIDIA_SMI is None:
        return None
    try:
        completed = subprocess.run(
            [
                _NVIDIA_SMI,
                "--query-gpu=utilization.gpu,memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        rows = [tuple(int(part.strip()) for part in line.split(",")) for line in completed.stdout.splitlines() if line]
    except ValueError:
        return None
    if not rows or any(len(row) != 2 for row in rows):
        return None
    utilization = sum(row[0] for row in rows) // len(rows)
    vram_bytes = sum(row[1] for row in rows) * 1024 * 1024
    return utilization, vram_bytes


def _percentile_ms(values: Sequence[float], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return max(1, math.ceil(ordered[min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)]))


def _worst_burst(events: Sequence[tuple[int, int]]) -> int:
    buckets: dict[int, int] = {}
    for sent_ns, size in events:
        bucket = sent_ns // 10_000_000
        buckets[bucket] = buckets.get(bucket, 0) + size
    return max(buckets.values(), default=0)


def _measured(method: str) -> MetricState:
    return MetricState("measured", method, "")


def _unavailable(reason_code: str) -> MetricState:
    return MetricState("unavailable", "unavailable", reason_code)


def _not_applicable(reason_code: str) -> MetricState:
    return MetricState("not_applicable", "not_applicable", reason_code)


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
