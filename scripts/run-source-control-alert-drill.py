#!/usr/bin/env python3
"""Exercise Source Control firing, local delivery and recovery."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import ipaddress
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

import yaml
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agent.adapters.source_control_metrics_adapter import (  # noqa: E402
    PrometheusSourceControlMetrics,
    SourceControlMetricInstruments,
)
from agent.services.source_control_observability import (  # noqa: E402
    SourceControlHealthMetricsPublisher,
    SourceControlHealthMonitor,
)

REPORT_SCHEMA = "ananta.source-control.alert-delivery-drill.v1"
RULE_PATH = REPOSITORY_ROOT / "config/monitoring/source-control-alerts.yml"
DEFAULT_OUTPUT = (
    REPOSITORY_ROOT
    / "artifacts/test-gates/source-control-alert-delivery-drill.json"
)
_ALERT_NAME = "SourceControlOperationalAlarm"
_REASON_CODE = "storage_pressure"
_RECEIVER_NAME = "source-control-drill-loopback"
_FORBIDDEN_CONTENT = (
    "access_token",
    "actor_id",
    "credential",
    "file_path",
    "private_key",
    "project_id",
    "repository_url",
    "secret",
    "tenant_id",
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Send Alertmanager-compatible firing and resolved notifications "
            "only to an ephemeral loopback receiver."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Stable redacted JSON evidence destination.",
    )
    return parser.parse_args()


def _metric_instruments(
    registry: CollectorRegistry,
) -> SourceControlMetricInstruments:
    return SourceControlMetricInstruments(
        operations_total=Counter(
            "source_control_operations_total",
            "Canonical Source Control Center operations by bounded outcome",
            ["operation", "decision", "reason_code", "status"],
            registry=registry,
        ),
        duration_seconds=Histogram(
            "source_control_operation_duration_seconds",
            "Canonical Source Control Center operation duration",
            ["operation", "status"],
            registry=registry,
        ),
        health=Gauge(
            "source_control_health",
            "Source Control Center health as a bounded one-hot gauge",
            ["status"],
            registry=registry,
        ),
        alert_state=Gauge(
            "source_control_alert_state",
            "Source Control Center bounded alarm state",
            ["reason_code", "status"],
            registry=registry,
        ),
        shadow_differences_total=Counter(
            "source_control_shadow_differences_total",
            "Content-free Source Control Center shadow comparison outcomes",
            ["operation", "decision", "status"],
            registry=registry,
        ),
    )


def _operational_rule() -> Mapping[str, Any]:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    matches = [
        rule
        for group in document.get("groups", ())
        for rule in group.get("rules", ())
        if rule.get("alert") == _ALERT_NAME
    ]
    if len(matches) != 1:
        raise RuntimeError("source_control_alert_rule_invalid")
    rule = matches[0]
    if rule.get("expr") != 'source_control_alert_state{status="firing"} == 1':
        raise RuntimeError("source_control_alert_rule_invalid")
    return rule


def _alertmanager_payload(
    *,
    status: str,
    rule: Mapping[str, Any],
) -> dict[str, object]:
    labels = {
        "alertname": _ALERT_NAME,
        "drill_scope": "loopback_test",
        "owner": str(rule["labels"]["owner"]),
        "reason_code": _REASON_CODE,
        "runbook_id": str(rule["labels"]["runbook_id"]),
        "severity": str(rule["labels"]["severity"]),
    }
    alert = {
        "status": status,
        "labels": labels,
        "annotations": dict(rule["annotations"]),
        "startsAt": "2000-01-01T00:00:00Z",
        "endsAt": (
            "2000-01-01T00:05:00Z"
            if status == "resolved"
            else "0001-01-01T00:00:00Z"
        ),
        "generatorURL": "about:blank",
        "fingerprint": hashlib.sha256(
            f"{_ALERT_NAME}:{_REASON_CODE}".encode()
        ).hexdigest()[:16],
    }
    return {
        "version": "4",
        "groupKey": f"{{}}:{{alertname=\"{_ALERT_NAME}\"}}",
        "truncatedAlerts": 0,
        "status": status,
        "receiver": _RECEIVER_NAME,
        "groupLabels": {"alertname": _ALERT_NAME},
        "commonLabels": labels,
        "commonAnnotations": dict(rule["annotations"]),
        "externalURL": "about:blank",
        "alerts": [alert],
    }


def _validated_event(
    payload: object,
    *,
    sequence: int,
) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise ValueError("source_control_alert_payload_invalid")
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).lower()
    if any(fragment in serialized for fragment in _FORBIDDEN_CONTENT):
        raise ValueError("source_control_alert_payload_contains_content")
    status = payload.get("status")
    alerts = payload.get("alerts")
    if (
        status not in {"firing", "resolved"}
        or payload.get("receiver") != _RECEIVER_NAME
        or not isinstance(alerts, list)
        or len(alerts) != 1
        or not isinstance(alerts[0], Mapping)
        or alerts[0].get("status") != status
    ):
        raise ValueError("source_control_alert_payload_invalid")
    labels = alerts[0].get("labels")
    if (
        not isinstance(labels, Mapping)
        or labels.get("alertname") != _ALERT_NAME
        or labels.get("drill_scope") != "loopback_test"
        or labels.get("reason_code") != _REASON_CODE
    ):
        raise ValueError("source_control_alert_payload_invalid")
    return {
        "sequence": sequence,
        "status": status,
        "delivery": "accepted",
        "receiver": _RECEIVER_NAME,
        "alertname": _ALERT_NAME,
    }


class _LoopbackReceiver:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        events = self.events

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if (
                    self.path != "/source-control-drill"
                    or not ipaddress.ip_address(
                        self.client_address[0]
                    ).is_loopback
                ):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 1 or length > 16_384:
                        raise ValueError("source_control_alert_payload_size")
                    payload = json.loads(self.rfile.read(length))
                    event = _validated_event(
                        payload,
                        sequence=len(events) + 1,
                    )
                except (ValueError, json.JSONDecodeError):
                    self.send_error(400)
                    return
                events.append(event)
                self.send_response(204)
                self.end_headers()

            def log_message(self, *_: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="source-control-alert-drill",
            daemon=True,
        )

    def __enter__(self) -> "_LoopbackReceiver":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def deliver(self, payload: Mapping[str, object]) -> None:
        host, port = self._server.server_address
        connection = http.client.HTTPConnection(host, port, timeout=5)
        try:
            connection.request(
                "POST",
                "/source-control-drill",
                body=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            response.read()
            if response.status != 204:
                raise RuntimeError(
                    "source_control_alert_delivery_rejected"
                )
        finally:
            connection.close()


def _sample(
    registry: CollectorRegistry,
    metric: str,
    labels: Mapping[str, str],
) -> float:
    value = registry.get_sample_value(metric, labels)
    if value is None:
        raise RuntimeError("source_control_alert_metric_missing")
    return float(value)


def _run_drill() -> dict[str, object]:
    rule = _operational_rule()
    registry = CollectorRegistry()
    metrics = PrometheusSourceControlMetrics(
        _metric_instruments(registry)
    )
    health = SourceControlHealthMonitor()
    publisher = SourceControlHealthMetricsPublisher(metrics)

    baseline = health.snapshot()
    publisher.publish(baseline)
    if baseline.health.status != "healthy":
        raise RuntimeError("source_control_alert_baseline_unhealthy")

    with _LoopbackReceiver() as receiver:
        health.set_operational_alarm(_REASON_CODE)
        firing = health.snapshot()
        publisher.publish(firing)
        if (
            firing.health.status != "degraded"
            or _sample(
                registry,
                "source_control_alert_state",
                {"reason_code": _REASON_CODE, "status": "firing"},
            )
            != 1.0
        ):
            raise RuntimeError("source_control_alert_not_firing")
        receiver.deliver(
            _alertmanager_payload(status="firing", rule=rule)
        )

        health.set_operational_alarm(_REASON_CODE, active=False)
        recovered = health.snapshot()
        publisher.publish(recovered)
        firing_value = _sample(
            registry,
            "source_control_alert_state",
            {"reason_code": _REASON_CODE, "status": "firing"},
        )
        resolved_value = _sample(
            registry,
            "source_control_alert_state",
            {"reason_code": _REASON_CODE, "status": "resolved"},
        )
        if (
            recovered.health.status != "healthy"
            or firing_value != 0.0
            or resolved_value != 1.0
        ):
            raise RuntimeError("source_control_alert_not_resolved")
        receiver.deliver(
            _alertmanager_payload(status="resolved", rule=rule)
        )
        delivery_sequence = list(receiver.events)

    if [event["status"] for event in delivery_sequence] != [
        "firing",
        "resolved",
    ]:
        raise RuntimeError("source_control_alert_delivery_sequence_invalid")

    payload: dict[str, object] = {
        "schema": REPORT_SCHEMA,
        "status": "passed",
        "drill_scope": "loopback_only",
        "pipeline": {
            "health_contract": "ananta.source-control.health.v1",
            "metrics_adapter": "PrometheusSourceControlMetrics",
            "metric": "source_control_alert_state",
            "alert_rule": _ALERT_NAME,
            "receiver": _RECEIVER_NAME,
            "production_alertmanager_exercised": False,
            "production_channels_contacted": False,
        },
        "delivery_sequence": delivery_sequence,
        "recovery": {
            "health_status": recovered.health.status,
            "firing_metric": firing_value,
            "resolved_metric": resolved_value,
        },
        "rule_digest": "sha256:"
        + hashlib.sha256(RULE_PATH.read_bytes()).hexdigest(),
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    payload["evidence_id"] = (
        "scad-" + hashlib.sha256(canonical).hexdigest()[:24]
    )
    return payload


def _write_output(path: Path, payload: Mapping[str, object]) -> None:
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)


def main() -> int:
    args = _arguments()
    try:
        payload = _run_drill()
        _write_output(args.output, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    except Exception:
        failure = {
            "schema": REPORT_SCHEMA,
            "status": "failed",
            "reason_code": "source_control_alert_drill_failed",
        }
        _write_output(args.output, failure)
        print(json.dumps(failure, indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
