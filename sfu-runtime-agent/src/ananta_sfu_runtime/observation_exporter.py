"""Bounded signed observation exporter for runtime-extension mode."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import requests

from .key_providers import NonExportableKeyProvider, key_provider_from_environment
from .livekit_probe import LiveKitProbe, LiveKitProbeError


class RuntimeObservationExportError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimeObservationExporterConfig:
    destination_url: str
    runtime_id: str
    tenant_id: str
    cluster_id: str
    region: str
    producer_id: str
    producer_fencing_token: int
    config_path: Path
    image_digest: str
    capability_manifest_path: Path
    state_path: Path
    client_certificate_path: Path
    client_key_path: Path
    ca_certificate_path: Path
    poll_interval_seconds: int = 10
    ttl_ms: int = 30_000
    request_timeout_seconds: float = 3.0
    queue_count_max: int = 64
    queue_bytes_max: int = 1_048_576
    queue_age_seconds_max: int = 120

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "RuntimeObservationExporterConfig":
        required = lambda name: str(environment.get(name) or "").strip()
        return cls(
            destination_url=required("ANANTA_SFU_OBSERVATION_URL"),
            runtime_id=required("ANANTA_SFU_RUNTIME_ID"),
            tenant_id=required("ANANTA_SFU_TENANT_ID"),
            cluster_id=required("ANANTA_SFU_CLUSTER_ID"),
            region=required("ANANTA_SFU_REGION"),
            producer_id=required("ANANTA_SFU_OBSERVATION_PRODUCER_ID"),
            producer_fencing_token=int(required("ANANTA_SFU_OBSERVATION_FENCING_TOKEN")),
            config_path=Path(required("ANANTA_SFU_RUNTIME_CONFIG_PATH")),
            image_digest=required("ANANTA_SFU_RUNTIME_IMAGE_DIGEST"),
            capability_manifest_path=Path(required("ANANTA_SFU_CAPABILITY_MANIFEST_PATH")),
            state_path=Path(required("ANANTA_SFU_OBSERVATION_STATE_PATH")),
            client_certificate_path=Path(required("ANANTA_SFU_OBSERVATION_CLIENT_CERT")),
            client_key_path=Path(required("ANANTA_SFU_OBSERVATION_CLIENT_KEY")),
            ca_certificate_path=Path(required("ANANTA_SFU_OBSERVATION_CA")),
        )

    def __post_init__(self) -> None:
        if not self.destination_url.startswith("https://"):
            raise ValueError("runtime_observation_https_required")
        if not self.image_digest.startswith("sha256:") or len(self.image_digest) != 71:
            raise ValueError("runtime_observation_image_digest_invalid")
        for value in (
            self.runtime_id,
            self.tenant_id,
            self.cluster_id,
            self.region,
            self.producer_id,
        ):
            if not value or len(value) > 128:
                raise ValueError("runtime_observation_scope_invalid")
        if self.producer_fencing_token < 1:
            raise ValueError("runtime_observation_fencing_invalid")
        if not 5 <= self.poll_interval_seconds <= 60 or not 5_000 <= self.ttl_ms <= 60_000:
            raise ValueError("runtime_observation_interval_invalid")
        if not 1 <= self.queue_count_max <= 256 or not 65_536 <= self.queue_bytes_max <= 4_194_304:
            raise ValueError("runtime_observation_queue_bound_invalid")


class RuntimeObservationExporter:
    _DOMAIN = b"ananta.sfu-runtime-observation.v2\x00"

    def __init__(
        self,
        config: RuntimeObservationExporterConfig,
        *,
        key_provider: NonExportableKeyProvider,
        probe: LiveKitProbe,
        session: requests.Session | None = None,
        clock=time.time,
    ) -> None:
        self._config = config
        self._keys = key_provider
        self._probe = probe
        self._session = session or requests.Session()
        self._clock = clock
        self._capabilities = self._load_capabilities()
        self._state = self._load_state()

    def collect_and_enqueue(self) -> Mapping[str, object]:
        measured_at_ms = int(self._clock() * 1_000)
        try:
            sample = self._probe.collect()
        except LiveKitProbeError:
            sample = {"liveness": None}
        pressure = self._pressure(sample)
        capacity = {
            "receiver_limit": None,
            "room_limit": None,
            "egress_bps": None,
            "memory_bytes_limit": self._cgroup_limit("memory.max"),
        }
        sequence = int(self._state["last_sequence"]) + 1
        unsigned = {
            "schema_version": "sfu_runtime_observation.v2",
            "producer_mode": "authenticated_runtime_extension",
            "scope": {
                "tenant_id": self._config.tenant_id,
                "cluster_id": self._config.cluster_id,
                "region": self._config.region,
                "runtime_id": self._config.runtime_id,
                "observed_node_id": None,
                "node_binding_authority": "non_authoritative_observation",
            },
            "producer_id": self._config.producer_id,
            "producer_fencing_token": self._config.producer_fencing_token,
            "boot_id": self._state["boot_id"],
            "sequence": sequence,
            "measured_at_ms": measured_at_ms,
            "valid_until_ms": measured_at_ms + self._config.ttl_ms,
            "config_digest": "sha256:" + hashlib.sha256(self._config.config_path.read_bytes()).hexdigest(),
            "image_digest": self._config.image_digest,
            "capability_digest": "sha256:" + hashlib.sha256(_canonical(self._capabilities)).hexdigest(),
            "capabilities": self._capabilities,
            "health": {
                "liveness": sample.get("liveness"),
                "control_ready": sample.get("liveness"),
                "media_ready": sample.get("liveness"),
                "admission_ready": sample.get("liveness") if all(value is not None for value in pressure.values()) else None,
            },
            "capacity": capacity,
            "pressure": pressure,
            "labels": {"source": "runtime_extension_local_probe"},
            "proof": None,
        }
        signature = self._keys.sign(self._DOMAIN + _canonical(unsigned))
        document = dict(unsigned)
        document["proof"] = {
            "algorithm": self._keys.algorithm,
            "key_id": self._keys.key_id,
            "signature": base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii"),
        }
        pending = list(self._state["pending"])
        if len(pending) >= self._config.queue_count_max:
            raise RuntimeObservationExportError("runtime_observation_queue_full")
        pending.append({"enqueued_at": int(self._clock()), "document": document})
        next_state = {
            "schema_version": "sfu_runtime_observation_export_state.v1",
            "boot_id": self._state["boot_id"],
            "last_sequence": sequence,
            "pending": pending,
        }
        self._persist(next_state)
        self._state = next_state
        return document

    def deliver(self, *, deadline_seconds: float = 5.0) -> int:
        deadline = time.monotonic() + min(max(deadline_seconds, 0.1), 30)
        sent = 0
        while self._state["pending"] and time.monotonic() < deadline:
            item = self._state["pending"][0]
            if self._clock() - int(item["enqueued_at"]) > self._config.queue_age_seconds_max:
                raise RuntimeObservationExportError("runtime_observation_queue_item_stale")
            try:
                response = self._session.post(
                    self._config.destination_url,
                    data=_canonical(item["document"]),
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                    cert=(
                        str(self._config.client_certificate_path),
                        str(self._config.client_key_path),
                    ),
                    verify=str(self._config.ca_certificate_path),
                    timeout=min(self._config.request_timeout_seconds, max(0.1, deadline - time.monotonic())),
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                raise RuntimeObservationExportError("runtime_observation_delivery_unavailable") from exc
            if response.status_code not in {200, 202}:
                raise RuntimeObservationExportError("runtime_observation_delivery_rejected")
            next_state = dict(self._state)
            next_state["pending"] = list(self._state["pending"])[1:]
            self._persist(next_state)
            self._state = next_state
            sent += 1
        return sent

    def run_forever(self) -> None:
        while True:
            started = time.monotonic()
            try:
                self.deliver(deadline_seconds=self._config.poll_interval_seconds / 3)
                self.collect_and_enqueue()
                self.deliver(deadline_seconds=self._config.poll_interval_seconds / 3)
            except RuntimeObservationExportError:
                pass
            time.sleep(max(0.1, self._config.poll_interval_seconds - (time.monotonic() - started)))

    def _load_capabilities(self) -> list[Mapping[str, str]]:
        raw = self._config.capability_manifest_path.read_bytes()
        if len(raw) > 65_536:
            raise RuntimeObservationExportError("runtime_capability_manifest_oversize")
        value = json.loads(raw)
        if not isinstance(value, list) or len(value) > 32:
            raise RuntimeObservationExportError("runtime_capability_manifest_invalid")
        names: set[str] = set()
        for claim in value:
            if (
                not isinstance(claim, Mapping)
                or set(claim) != {"name", "state"}
                or claim["state"] not in {"supported", "unsupported", "unknown"}
                or not isinstance(claim["name"], str)
                or not claim["name"]
                or len(claim["name"]) > 64
                or claim["name"] in names
            ):
                raise RuntimeObservationExportError("runtime_capability_manifest_invalid")
            names.add(claim["name"])
        return value

    def _load_state(self) -> dict[str, object]:
        if not self._config.state_path.exists():
            state = {
                "schema_version": "sfu_runtime_observation_export_state.v1",
                "boot_id": str(uuid.uuid4()),
                "last_sequence": 0,
                "pending": [],
            }
            self._persist(state)
            return state
        raw = self._config.state_path.read_bytes()
        if len(raw) > self._config.queue_bytes_max:
            raise RuntimeObservationExportError("runtime_observation_state_oversize")
        value = json.loads(raw)
        if set(value) != {"schema_version", "boot_id", "last_sequence", "pending"}:
            raise RuntimeObservationExportError("runtime_observation_state_invalid")
        if len(value["pending"]) > self._config.queue_count_max:
            raise RuntimeObservationExportError("runtime_observation_state_invalid")
        value["boot_id"] = str(uuid.uuid4())
        value["last_sequence"] = 0
        self._persist(value)
        return value

    def _persist(self, state: Mapping[str, object]) -> None:
        payload = _canonical(state)
        if len(payload) > self._config.queue_bytes_max:
            raise RuntimeObservationExportError("runtime_observation_state_oversize")
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
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _cgroup_limit(name: str) -> int | None:
        try:
            value = Path("/sys/fs/cgroup", name).read_text(encoding="ascii").strip()
            return None if value == "max" else int(value)
        except (OSError, ValueError):
            return None

    def _pressure(self, sample: Mapping[str, object]) -> dict[str, float | None]:
        memory_limit = self._cgroup_limit("memory.max")
        memory_current = self._cgroup_limit("memory.current")
        memory_ratio = (
            None
            if not memory_limit or memory_current is None
            else min(1.0, memory_current / memory_limit)
        )
        return {
            "cpu_ratio": None,
            "memory_ratio": memory_ratio,
            "fd_ratio": None,
            "udp_port_ratio": None,
            "packet_drop_ratio": None,
        }


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def main() -> None:
    config = RuntimeObservationExporterConfig.from_environment(os.environ)
    exporter = RuntimeObservationExporter(
        config,
        key_provider=key_provider_from_environment(os.environ),
        probe=LiveKitProbe(),
    )
    exporter.run_forever()


__all__ = [
    "RuntimeObservationExportError",
    "RuntimeObservationExporter",
    "RuntimeObservationExporterConfig",
    "main",
]
