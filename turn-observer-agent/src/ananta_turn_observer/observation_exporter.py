"""Durable bounded queue and Ed25519 exporter for TURN observations."""

from __future__ import annotations

import base64
import json
import os
import ssl
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class ObservationExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class ObservationExporterConfig:
    destination_url: str
    state_path: Path
    private_key_path: Path
    client_certificate_path: Path
    client_key_path: Path
    ca_certificate_path: Path
    request_timeout_seconds: float = 3.0
    max_pending_count: int = 64
    max_state_bytes: int = 524_288
    max_pending_age_seconds: int = 300

    def __post_init__(self) -> None:
        if not self.destination_url.startswith("https://"):
            raise ValueError("turn_observation_destination_requires_https")
        if not 0.1 <= self.request_timeout_seconds <= 10.0:
            raise ValueError("turn_observation_timeout_out_of_bounds")
        if not 1 <= self.max_pending_count <= 512:
            raise ValueError("turn_observation_queue_count_out_of_bounds")
        if not 16_384 <= self.max_state_bytes <= 4_194_304:
            raise ValueError("turn_observation_queue_bytes_out_of_bounds")
        if not 10 <= self.max_pending_age_seconds <= 3_600:
            raise ValueError("turn_observation_queue_age_out_of_bounds")


class ObservationExporter:
    SIGNING_CONTEXT = b"ananta.turn-observation.v1\x00"

    def __init__(
        self,
        config: ObservationExporterConfig,
        *,
        opener: Callable[..., object] = urlopen,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._opener = opener
        self._wall_clock = wall_clock
        self._state = self._load_or_create_state()
        self._private_key = self._load_private_key()

    @property
    def boot_id(self) -> str:
        return str(self._state["boot_id"])

    def enqueue(self, unsigned_document: Mapping[str, Any]) -> Mapping[str, Any]:
        if "signature" in unsigned_document or "boot_id" in unsigned_document or "sequence" in unsigned_document:
            raise ObservationExportError("turn_observation_reserved_field_present")
        pending = list(self._state["pending"])
        if len(pending) >= self._config.max_pending_count:
            raise ObservationExportError("turn_observation_queue_full")
        sequence = int(self._state["last_sequence"]) + 1
        document = dict(unsigned_document)
        document["boot_id"] = self.boot_id
        document["sequence"] = sequence
        canonical = self._canonical(document)
        document["signature"] = base64.b64encode(
            self._private_key.sign(self.SIGNING_CONTEXT + canonical)
        ).decode("ascii")
        pending.append({"enqueued_at": int(self._wall_clock()), "document": document})
        state = {"boot_id": self.boot_id, "last_sequence": sequence, "pending": pending}
        self._persist(state)
        self._state = state
        return document

    def send_pending(self, *, deadline_seconds: float = 5.0) -> int:
        if not 0 < deadline_seconds <= 30:
            raise ObservationExportError("turn_observation_deadline_invalid")
        deadline = time.monotonic() + deadline_seconds
        sent = 0
        while self._state["pending"] and time.monotonic() < deadline:
            item = self._state["pending"][0]
            if self._wall_clock() - int(item["enqueued_at"]) > self._config.max_pending_age_seconds:
                raise ObservationExportError("turn_observation_pending_expired")
            payload = self._canonical(item["document"])
            request = Request(
                self._config.destination_url,
                data=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            context = ssl.create_default_context(cafile=str(self._config.ca_certificate_path))
            context.load_cert_chain(
                certfile=str(self._config.client_certificate_path),
                keyfile=str(self._config.client_key_path),
            )
            try:
                with self._opener(
                    request,
                    timeout=min(self._config.request_timeout_seconds, max(0.1, deadline - time.monotonic())),
                    context=context,
                ) as response:
                    status = int(response.status)
                    response_payload = response.read(4097)
            except HTTPError as exc:
                status = exc.code
                response_payload = exc.read(4097)
            except Exception as exc:  # noqa: BLE001
                raise ObservationExportError("turn_observation_delivery_unavailable") from exc
            duplicate = False
            if status == 409 and len(response_payload) <= 4096:
                try:
                    duplicate = json.loads(response_payload).get("reason_code") == "turn_observation_duplicate"
                except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                    duplicate = False
            if status not in {200, 202} and not duplicate:
                raise ObservationExportError("turn_observation_delivery_rejected")
            state = dict(self._state)
            state["pending"] = list(self._state["pending"])[1:]
            self._persist(state)
            self._state = state
            sent += 1
        return sent

    def _load_or_create_state(self) -> dict[str, Any]:
        path = self._config.state_path
        if not path.exists():
            state = {"boot_id": str(uuid.uuid4()), "last_sequence": 0, "pending": []}
            self._persist(state)
            return state
        self._require_private_mode(path)
        raw = path.read_bytes()
        if len(raw) > self._config.max_state_bytes:
            raise ObservationExportError("turn_observation_state_too_large")
        try:
            state = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ObservationExportError("turn_observation_state_invalid") from exc
        if set(state) != {"boot_id", "last_sequence", "pending"}:
            raise ObservationExportError("turn_observation_state_invalid")
        uuid.UUID(str(state["boot_id"]))
        if not isinstance(state["last_sequence"], int) or state["last_sequence"] < 0:
            raise ObservationExportError("turn_observation_state_invalid")
        if not isinstance(state["pending"], list) or len(state["pending"]) > self._config.max_pending_count:
            raise ObservationExportError("turn_observation_state_invalid")
        return state

    def _load_private_key(self) -> Ed25519PrivateKey:
        self._require_private_mode(self._config.private_key_path)
        encoded = self._config.private_key_path.read_text(encoding="ascii").strip()
        try:
            raw = base64.b64decode(encoded, validate=True)
            return Ed25519PrivateKey.from_private_bytes(raw)
        except (ValueError, TypeError) as exc:
            raise ObservationExportError("turn_observer_private_key_invalid") from exc

    def _persist(self, state: Mapping[str, Any]) -> None:
        payload = self._canonical(state)
        if len(payload) > self._config.max_state_bytes:
            raise ObservationExportError("turn_observation_state_limit_exceeded")
        path = self._config.state_path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _canonical(value: Mapping[str, Any]) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")

    @staticmethod
    def _require_private_mode(path: Path) -> None:
        if path.stat().st_mode & 0o077:
            raise ObservationExportError("turn_observer_secret_permissions_too_broad")
