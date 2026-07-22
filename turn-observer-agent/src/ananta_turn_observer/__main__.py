"""Minimal bounded polling loop for the TURN observer sidecar."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .coturn_collector import CoturnAggregateCollector, CoturnCollectionError, CoturnCollectorConfig
from .observation_exporter import ObservationExportError, ObservationExporter, ObservationExporterConfig


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing required configuration: {name}")
    return value


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat().replace("+00:00", "Z")


def main() -> None:
    interval = int(os.environ.get("TURN_OBSERVER_POLL_INTERVAL_SECONDS", "15"))
    if not 5 <= interval <= 60:
        raise SystemExit("TURN_OBSERVER_POLL_INTERVAL_SECONDS must be between 5 and 60")
    pool_id = _required("TURN_POOL_ID")
    instance_id = _required("TURN_INSTANCE_ID")
    identity_id = _required("TURN_OBSERVER_IDENTITY_ID")
    identity_version = int(_required("TURN_OBSERVER_IDENTITY_VERSION"))
    config_version = _required("TURN_CONFIG_VERSION")
    config_path = Path(_required("TURN_CONFIG_PATH"))
    config_digest = "sha256:" + hashlib.sha256(config_path.read_bytes()).hexdigest()
    collector = CoturnAggregateCollector(CoturnCollectorConfig())
    exporter = ObservationExporter(
        ObservationExporterConfig(
            destination_url=_required("TURN_OBSERVATION_URL"),
            state_path=Path(os.environ.get("TURN_OBSERVER_STATE_PATH", "/var/lib/ananta-turn-observer/state.json")),
            private_key_path=Path(_required("TURN_OBSERVER_SIGNING_KEY_PATH")),
            client_certificate_path=Path(_required("TURN_OBSERVER_MTLS_CERT_PATH")),
            client_key_path=Path(_required("TURN_OBSERVER_MTLS_KEY_PATH")),
            ca_certificate_path=Path(_required("TURN_OBSERVER_CA_PATH")),
        )
    )
    previous = time.time()
    while True:
        started = time.time()
        health = "healthy"
        relay_ready = True
        try:
            counters = collector.collect()
        except CoturnCollectionError:
            counters = {name: None for name in CoturnAggregateCollector.METRICS.values()}
            health = "unhealthy"
            relay_ready = False
        else:
            relay_ready = any(value is not None for value in counters.values())
        document = {
            "schema_version": "turn_pool_observation.v1",
            "observation_id": str(uuid.uuid4()),
            "pool_id": pool_id,
            "instance_id": instance_id,
            "observer_identity_id": identity_id,
            "observer_identity_version": identity_version,
            "config_version": config_version,
            "config_digest": config_digest,
            "measured_at": _timestamp(started),
            "window_started_at": _timestamp(previous),
            "window_ended_at": _timestamp(started),
            "health": {"status": health, "source": "coturn_prometheus_aggregate"},
            "relay_ready": relay_ready,
            "counters": counters,
        }
        try:
            exporter.send_pending(deadline_seconds=min(5.0, interval / 2))
            exporter.enqueue(document)
            exporter.send_pending(deadline_seconds=min(5.0, interval / 2))
        except ObservationExportError:
            # The durable pending queue is retried next iteration.  No payload,
            # endpoint, certificate or key material is logged.
            pass
        previous = started
        elapsed = time.time() - started
        time.sleep(max(0.1, interval - elapsed))


if __name__ == "__main__":
    main()
