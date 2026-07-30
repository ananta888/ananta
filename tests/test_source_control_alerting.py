from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

from agent.services.source_control_observability import (
    SourceControlHealthMetricsPublisher,
    SourceControlHealthMonitor,
)

ROOT = Path(__file__).parents[1]
RULES = ROOT / "config/monitoring/source-control-alerts.yml"
SCRIPT = ROOT / "scripts/run-source-control-alert-drill.py"
ALLOWED_METRICS = {
    "source_control_alert_state",
    "source_control_health",
}


class _Metrics:
    def __init__(self) -> None:
        self.gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def observe_duration(self, metric, seconds, labels) -> None:
        del metric, seconds, labels

    def increment(self, metric, labels) -> None:
        del metric, labels

    def set_gauge(self, metric, value, labels) -> None:
        self.gauges[(metric, tuple(sorted(labels.items())))] = float(value)

    def value(self, metric: str, **labels: str) -> float:
        return self.gauges[(metric, tuple(sorted(labels.items())))]


def test_health_metric_publisher_clears_firing_series_on_recovery() -> None:
    metrics = _Metrics()
    monitor = SourceControlHealthMonitor()
    publisher = SourceControlHealthMetricsPublisher(metrics)

    monitor.set_operational_alarm("storage_pressure")
    publisher.publish(monitor.snapshot())
    assert metrics.value(
        "source_control_alert_state",
        reason_code="storage_pressure",
        status="firing",
    ) == 1.0
    assert metrics.value(
        "source_control_alert_state",
        reason_code="storage_pressure",
        status="resolved",
    ) == 0.0

    monitor.set_operational_alarm("storage_pressure", active=False)
    publisher.publish(monitor.snapshot())
    assert metrics.value(
        "source_control_alert_state",
        reason_code="storage_pressure",
        status="firing",
    ) == 0.0
    assert metrics.value(
        "source_control_alert_state",
        reason_code="storage_pressure",
        status="resolved",
    ) == 1.0
    assert metrics.value("source_control_health", status="healthy") == 1.0


def test_source_control_alert_rules_are_bounded_and_operational() -> None:
    document = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    rules = document["groups"][0]["rules"]
    assert {rule["alert"] for rule in rules} == {
        "SourceControlHealthDegraded",
        "SourceControlMetricPipelineMissing",
        "SourceControlOperationalAlarm",
    }
    for rule in rules:
        assert rule["for"]
        assert rule["labels"]["severity"] in {"warning", "critical"}
        assert rule["labels"]["owner"]
        assert rule["labels"]["runbook_id"].startswith(
            "SRCCTRL-R6-001/"
        )
        assert rule["annotations"]["clear_condition"]
        metrics = set(
            re.findall(r"source_control_[a-z_]+", rule["expr"])
        )
        assert metrics and metrics <= ALLOWED_METRICS
        serialized = json.dumps(rule).lower()
        assert not any(
            fragment in serialized
            for fragment in (
                "access_token",
                "actor_id",
                "credential",
                "project_id",
                "repository_url",
                "tenant_id",
            )
        )


def test_prometheus_and_alertmanager_defaults_are_fail_closed() -> None:
    prometheus = yaml.safe_load(
        (ROOT / "docker/prometheus/prometheus.yml").read_text(
            encoding="utf-8"
        )
    )
    alertmanager = yaml.safe_load(
        (ROOT / "docker/alertmanager/alertmanager.yml").read_text(
            encoding="utf-8"
        )
    )

    assert prometheus["rule_files"] == [
        "/etc/prometheus/rules/source-control-alerts.yml"
    ]
    assert (
        prometheus["alerting"]["alertmanagers"][0]["static_configs"][0][
            "targets"
        ]
        == ["alertmanager:9093"]
    )
    assert alertmanager["route"]["receiver"] == (
        "operator-configuration-required"
    )
    assert alertmanager["receivers"] == [
        {"name": "operator-configuration-required"}
    ]


def test_loopback_alert_drill_delivers_firing_and_resolved(
    tmp_path: Path,
) -> None:
    output = tmp_path / "evidence.json"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert payload["drill_scope"] == "loopback_only"
    assert payload["pipeline"]["production_alertmanager_exercised"] is False
    assert payload["pipeline"]["production_channels_contacted"] is False
    assert [
        event["status"] for event in payload["delivery_sequence"]
    ] == ["firing", "resolved"]
    assert payload["recovery"] == {
        "firing_metric": 0.0,
        "health_status": "healthy",
        "resolved_metric": 1.0,
    }
    assert payload["evidence_id"].startswith("scad-")
    assert str(tmp_path) not in completed.stdout
