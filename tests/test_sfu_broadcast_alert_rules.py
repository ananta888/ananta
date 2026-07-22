"""Structural privacy, allowlist and operator-surface tests for OBS-006."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
METRIC_PATTERN = re.compile(r"ananta_sfu_broadcast_[a-z_]+")
DERIVED_SUFFIXES = ("_bucket", "_sum", "_count")


def _catalog_metrics() -> set[str]:
    document = json.loads((ROOT / "config/sfu_broadcast_observability_catalog.json").read_text())
    return {metric["name"] for metric in document["metrics"]}


def _base_metric(value: str) -> str:
    for suffix in DERIVED_SUFFIXES:
        if value.endswith(suffix) and value.removesuffix(suffix) in _catalog_metrics():
            return value.removesuffix(suffix)
    return value


def test_dashboard_queries_only_registered_metrics_and_has_no_identity_variables() -> None:
    dashboard = json.loads(
        (ROOT / "config/monitoring/dashboards/sfu-broadcast.json").read_text(encoding="utf-8")
    )
    catalog = _catalog_metrics()
    expressions = [
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
        if "expr" in target
    ]
    assert dashboard["uid"] == "ananta-sfu-broadcast-v1"
    assert dashboard["version"] == 1
    assert dashboard["templating"]["list"] == []
    assert expressions
    assert {_base_metric(metric) for expression in expressions for metric in METRIC_PATTERN.findall(expression)} <= catalog
    forbidden = ("tenant_id", "room_id", "participant", "receiver_id", "publication_id", "node_id", "device")
    assert not any(value in expression for value in forbidden for expression in expressions)


def test_alerts_have_owner_duration_clear_condition_and_content_free_allowlisted_queries() -> None:
    document = yaml.safe_load(
        (ROOT / "config/monitoring/sfu-broadcast-alerts.yml").read_text(encoding="utf-8")
    )
    rules = document["groups"][0]["rules"]
    catalog = _catalog_metrics()
    assert rules
    for rule in rules:
        assert rule["for"]
        assert rule["labels"]["severity"] in {"warning", "critical"}
        assert rule["labels"]["owner"]
        assert rule["labels"]["slo_ref"]
        assert rule["labels"]["runbook_id"].startswith("SFB-GATE-010/")
        assert rule["annotations"]["clear_condition"]
        metrics = {_base_metric(metric) for metric in METRIC_PATTERN.findall(rule["expr"])}
        assert metrics and metrics <= catalog
        serialized = json.dumps(rule).lower()
        assert not any(value in serialized for value in (
            "access_token", "credential", "private_ip", "participant_id", "receiver_id", "room_id", "payload"
        ))


def test_unregistered_coverage_is_explicit_and_never_queried() -> None:
    dashboard = json.loads(
        (ROOT / "config/monitoring/dashboards/sfu-broadcast.json").read_text(encoding="utf-8")
    )
    blocked = {panel["title"]: panel for panel in dashboard["panels"] if panel["type"] == "text"}
    assert blocked["Node health / flap coverage"]["targets"] == []
    assert blocked["Privacy scan coverage"]["targets"] == []
    assert all("sfu_observability_metric_not_registered" in panel["options"]["content"] for panel in blocked.values())
    runbook = (ROOT / "docs/operations/sfu-broadcast-alerts.md").read_text(encoding="utf-8")
    assert "node-health" in runbook and "reservation" in runbook and "privacy-scan" in runbook
    assert "full OBS-006 acceptance" in runbook
