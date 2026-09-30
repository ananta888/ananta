"""Content-free loopback transport codec and deterministic payload fixtures of the semantic-media program benchmark."""

from __future__ import annotations

import base64
import hashlib
import json
import selectors
import socket
import struct
import threading
import time
from typing import Any, Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ananta_contracts.semantic_speech import validate_semantic_frame
from ananta_contracts.semantic_visual import (
    canonical_json as canonical_visual_json,
)
from ananta_contracts.semantic_visual import validate_semantic_scene
from ananta_contracts.speech_evidence_sync import (
    canonical_json as canonical_evidence_json,
)
from ananta_contracts.speech_evidence_sync import (
    validate_payload as validate_evidence_payload,
)
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
from scripts.benchmark.semantic_media_program_measurements import (
    ProgramBenchmarkExecutionError,
)


_HEADER = struct.Struct("!QQ")
_FIXED_EXPIRY_MS = 4_102_444_800_000


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
