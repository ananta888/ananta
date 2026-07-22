"""Fail-closed policy enforcement for aggregate SFU broadcast metrics."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent.services.sfu_broadcast_metrics_port import SfuBroadcastAuditRule

CATALOG_ID = "ananta.sfu-broadcast-observability-catalog.v1"
CATALOG_VERSION = "1.0"
REQUIRED_STATISTICS = ("p50", "p95", "p99", "peak")
REQUIRED_DOMAINS = frozenset(
    {
        "join",
        "publish",
        "subscribe",
        "group",
        "route",
        "layer",
        "queue",
        "drop",
        "ingress",
        "sfu_egress",
        "turn",
        "rekey",
        "drain",
        "failover",
        "capacity",
    }
)
FORBIDDEN_LABEL_FRAGMENTS = frozenset(
    {
        "audio",
        "video_frame",
        "media_payload",
        "semantic",
        "transcript",
        "embedding",
        "key",
        "secret",
        "token",
        "credential",
        "sdp",
        "ice",
        "private_ip",
        "device",
        "identity",
        "participant",
        "receiver_id",
        "publisher_id",
        "room_id",
        "tenant_id",
        "track_id",
        "node_id",
        "original_id",
        "payload",
    }
)


class SfuBroadcastObservabilityPolicyError(ValueError):
    """A public, content-free observability policy rejection."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class LabelRule:
    name: str
    allowed_values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MetricRule:
    name: str
    domain: str
    purpose: str
    metric_type: str
    unit: str
    labels: tuple[LabelRule, ...]
    scope: str
    statistics: tuple[str, ...]
    allowed_buckets: tuple[float, ...]
    min_cohort_size: int
    cardinality_per_scope_max: int
    storage_points_per_scope_max: int
    aggregation_window_seconds: int
    retention_seconds: int
    pseudonym_rotation_seconds: int

    @property
    def projected_cardinality_per_scope(self) -> int:
        cardinality = 1
        for label in self.labels:
            cardinality *= len(label.allowed_values)
        return cardinality

    @property
    def projected_storage_points_per_scope(self) -> int:
        windows = self.retention_seconds // self.aggregation_window_seconds
        return windows * self.projected_cardinality_per_scope


@dataclass(frozen=True, slots=True)
class MetricDecision:
    metric_name: str
    emitted: bool
    reason_code: str
    labels: Mapping[str, str]
    value: int | float | None
    series_key: str | None

    def public(self) -> dict[str, object]:
        return {
            "metric_name": self.metric_name,
            "emitted": self.emitted,
            "reason_code": self.reason_code,
            "labels": dict(sorted(self.labels.items())),
            "value": self.value,
            "series_key": self.series_key,
        }


class SfuBroadcastObservabilityPolicy:
    """Evaluates metric samples without retaining original scope identifiers."""

    _TOP_LEVEL_KEYS = frozenset(
        {
            "$schema",
            "catalog_id",
            "version",
            "decision_policy",
            "forbidden_data",
            "statistics_policy",
            "pseudonymization",
            "metrics",
            "audit_events",
        }
    )

    def __init__(self, catalog: Mapping[str, Any], *, pseudonym_secret: bytes) -> None:
        self._require_exact_keys(catalog, self._TOP_LEVEL_KEYS)
        if catalog.get("catalog_id") != CATALOG_ID or catalog.get("version") != CATALOG_VERSION:
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_catalog_version_invalid")

        decision_policy = self._as_mapping(catalog.get("decision_policy"))
        statistics_policy = self._as_mapping(catalog.get("statistics_policy"))
        pseudonymization = self._as_mapping(catalog.get("pseudonymization"))
        if tuple(statistics_policy.get("required_statistics", ())) != REQUIRED_STATISTICS:
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_statistics_policy_invalid")
        if pseudonymization.get("algorithm") != "hmac-sha256":
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_pseudonym_algorithm_invalid")

        minimum_secret_bytes = self._positive_int(pseudonymization.get("minimum_secret_bytes"))
        if len(pseudonym_secret) < minimum_secret_bytes:
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_pseudonym_secret_invalid")
        if pseudonymization.get("truncation_bits") != 96 or pseudonymization.get("output_prefix") != "sfb1":
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_pseudonym_contract_invalid")

        metrics_value = catalog.get("metrics")
        if not isinstance(metrics_value, Sequence) or isinstance(metrics_value, (str, bytes)):
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_catalog_invalid")
        rules = tuple(self._parse_metric(value) for value in metrics_value)
        names = [rule.name for rule in rules]
        if len(names) != len(set(names)) or {rule.domain for rule in rules} != REQUIRED_DOMAINS:
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_metric_coverage_invalid")

        audit_events_value = catalog.get("audit_events")
        if not isinstance(audit_events_value, Sequence) or isinstance(audit_events_value, (str, bytes)):
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_audit_catalog_invalid")
        audit_rules = tuple(self._parse_audit_event(value) for value in audit_events_value)
        audit_names = [name for name, _rule in audit_rules]
        if not audit_names or len(audit_names) != len(set(audit_names)):
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_audit_catalog_invalid")

        self._decision_policy = dict(decision_policy)
        self._pseudonym_secret = bytes(pseudonym_secret)
        self._rules = {rule.name: rule for rule in rules}
        self._audit_rules = dict(audit_rules)

    @classmethod
    def from_path(cls, path: Path, *, pseudonym_secret: bytes) -> "SfuBroadcastObservabilityPolicy":
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_catalog_unavailable") from exc
        if not isinstance(document, Mapping):
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_catalog_invalid")
        return cls(document, pseudonym_secret=pseudonym_secret)

    @property
    def metrics(self) -> Mapping[str, MetricRule]:
        return self._rules

    @property
    def audit_events(self) -> Mapping[str, SfuBroadcastAuditRule]:
        return self._audit_rules

    def evaluate(
        self,
        metric_name: str,
        *,
        value: int | float,
        labels: Mapping[str, str],
        scope_id: str,
        cohort_size: int,
        now_seconds: int | float,
    ) -> MetricDecision:
        rule = self._rules.get(metric_name)
        if rule is None:
            self._reject("unknown_metric_reason_code")
        assert rule is not None

        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_metric_value_invalid")
        if value < 0:
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_metric_value_invalid")
        if isinstance(cohort_size, bool) or not isinstance(cohort_size, int) or cohort_size < 0:
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_cohort_size_invalid")
        if not isinstance(labels, Mapping):
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_labels_invalid")

        label_rules = {label.name: label for label in rule.labels}
        supplied_names = set(labels)
        for supplied_name in supplied_names:
            if not isinstance(supplied_name, str):
                self._reject("unknown_label_reason_code")
            if self._is_forbidden_label(supplied_name):
                self._reject("forbidden_label_reason_code")
            if supplied_name not in label_rules:
                self._reject("unknown_label_reason_code")
        if supplied_names != set(label_rules):
            self._reject("missing_label_reason_code")

        public_labels: dict[str, str] = {}
        for name, label_rule in label_rules.items():
            candidate = labels[name]
            if not isinstance(candidate, str) or candidate not in label_rule.allowed_values:
                self._reject("invalid_label_value_reason_code")
            public_labels[name] = candidate

        if cohort_size < rule.min_cohort_size:
            return MetricDecision(
                metric_name=rule.name,
                emitted=False,
                reason_code=self._reason("small_cohort_reason_code"),
                labels={},
                value=None,
                series_key=None,
            )

        scope_pseudonym = self._scope_pseudonym(
            scope_id,
            now_seconds=now_seconds,
            rotation_seconds=rule.pseudonym_rotation_seconds,
        )
        public_labels["scope_pseudonym"] = scope_pseudonym
        series_document = {"metric": rule.name, "labels": dict(sorted(public_labels.items()))}
        series_key = hashlib.sha256(
            json.dumps(series_document, sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest()
        return MetricDecision(
            metric_name=rule.name,
            emitted=True,
            reason_code="sfu_observability_sample_accepted",
            labels=public_labels,
            value=value,
            series_key=series_key,
        )

    def _scope_pseudonym(
        self,
        scope_id: str,
        *,
        now_seconds: int | float,
        rotation_seconds: int,
    ) -> str:
        if not isinstance(scope_id, str) or not scope_id:
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_scope_invalid")
        encoded_scope = scope_id.encode("utf-8")
        if len(encoded_scope) > 1024 or not math.isfinite(float(now_seconds)) or now_seconds < 0:
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_scope_invalid")
        epoch = int(now_seconds) // rotation_seconds
        message = b"ananta.sfu-observability.v1\0" + str(epoch).encode("ascii") + b"\0" + encoded_scope
        digest = hmac.new(self._pseudonym_secret, message, hashlib.sha256).hexdigest()[:24]
        return f"sfb1.{epoch}.{digest}"

    def _parse_metric(self, value: object) -> MetricRule:
        metric = self._as_mapping(value)
        required_keys = frozenset(
            {
                "name",
                "domain",
                "purpose",
                "metric_type",
                "unit",
                "labels",
                "scope",
                "statistics",
                "allowed_buckets",
                "min_cohort_size",
                "suppression_rule",
                "cardinality_per_scope_max",
                "storage_points_per_scope_max",
                "aggregation_window_seconds",
                "retention_seconds",
                "pseudonym_rotation_seconds",
                "query_rbac",
                "export_destination",
                "privacy_class",
            }
        )
        self._require_exact_keys(metric, required_keys)
        labels_value = metric.get("labels")
        if not isinstance(labels_value, Sequence) or isinstance(labels_value, (str, bytes)):
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_catalog_invalid")
        labels: list[LabelRule] = []
        for raw_label in labels_value:
            label = self._as_mapping(raw_label)
            self._require_exact_keys(label, frozenset({"name", "allowed_values"}))
            name = label.get("name")
            values = label.get("allowed_values")
            if not isinstance(name, str) or self._is_forbidden_label(name):
                raise SfuBroadcastObservabilityPolicyError("sfu_observability_catalog_forbidden_label")
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                raise SfuBroadcastObservabilityPolicyError("sfu_observability_catalog_invalid")
            allowed_values = tuple(values)
            if not allowed_values or any(not isinstance(item, str) for item in allowed_values):
                raise SfuBroadcastObservabilityPolicyError("sfu_observability_catalog_invalid")
            if len(allowed_values) != len(set(allowed_values)):
                raise SfuBroadcastObservabilityPolicyError("sfu_observability_catalog_invalid")
            labels.append(LabelRule(name=name, allowed_values=allowed_values))
        if len({label.name for label in labels}) != len(labels):
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_catalog_invalid")

        buckets_value = metric.get("allowed_buckets")
        if not isinstance(buckets_value, Sequence) or isinstance(buckets_value, (str, bytes)):
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_catalog_invalid")
        buckets = tuple(float(bucket) for bucket in buckets_value)
        if len(buckets) < 2 or any(not math.isfinite(bucket) or bucket < 0 for bucket in buckets):
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_bucket_policy_invalid")
        if tuple(sorted(set(buckets))) != buckets:
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_bucket_policy_invalid")

        aggregation = self._positive_int(metric.get("aggregation_window_seconds"))
        retention = self._positive_int(metric.get("retention_seconds"))
        rotation = self._positive_int(metric.get("pseudonym_rotation_seconds"))
        rule = MetricRule(
            name=self._required_string(metric.get("name")),
            domain=self._required_string(metric.get("domain")),
            purpose=self._required_string(metric.get("purpose")),
            metric_type=self._required_string(metric.get("metric_type")),
            unit=self._required_string(metric.get("unit")),
            labels=tuple(labels),
            scope=self._required_string(metric.get("scope")),
            statistics=tuple(metric.get("statistics", ())),
            allowed_buckets=buckets,
            min_cohort_size=self._positive_int(metric.get("min_cohort_size")),
            cardinality_per_scope_max=self._positive_int(metric.get("cardinality_per_scope_max")),
            storage_points_per_scope_max=self._positive_int(metric.get("storage_points_per_scope_max")),
            aggregation_window_seconds=aggregation,
            retention_seconds=retention,
            pseudonym_rotation_seconds=rotation,
        )
        if rule.statistics != REQUIRED_STATISTICS or metric.get("suppression_rule") != "drop_window_below_min_cohort":
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_statistics_policy_invalid")
        if retention % aggregation or rotation % aggregation:
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_window_alignment_invalid")
        if rule.projected_cardinality_per_scope > rule.cardinality_per_scope_max:
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_cardinality_budget_exceeded")
        if rule.projected_storage_points_per_scope > rule.storage_points_per_scope_max:
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_storage_budget_exceeded")
        query_rbac = self._as_mapping(metric.get("query_rbac"))
        self._require_exact_keys(
            query_rbac,
            frozenset({"roles", "max_queries_per_minute", "max_rows_per_query"}),
        )
        if not query_rbac.get("roles"):
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_query_policy_invalid")
        self._positive_int(query_rbac.get("max_queries_per_minute"))
        self._positive_int(query_rbac.get("max_rows_per_query"))
        return rule

    def _parse_audit_event(self, value: object) -> tuple[str, SfuBroadcastAuditRule]:
        event = self._as_mapping(value)
        self._require_exact_keys(
            event,
            frozenset({"name", "outcomes", "reason_codes", "labels", "cardinality_max"}),
        )
        name = self._required_string(event.get("name"))
        if not name.startswith("ananta_sfu_broadcast_"):
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_audit_catalog_invalid")
        outcomes = self._bounded_vocabulary(event.get("outcomes"), maximum=16)
        reason_codes = self._bounded_vocabulary(event.get("reason_codes"), maximum=16)
        labels_value = event.get("labels")
        if not isinstance(labels_value, Sequence) or isinstance(labels_value, (str, bytes)):
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_audit_catalog_invalid")
        label_values: dict[str, frozenset[str]] = {}
        cardinality = 1
        for raw_label in labels_value:
            label = self._as_mapping(raw_label)
            self._require_exact_keys(label, frozenset({"name", "allowed_values"}))
            label_name = self._required_string(label.get("name"))
            if label_name in label_values or self._is_forbidden_label(label_name):
                raise SfuBroadcastObservabilityPolicyError("sfu_observability_catalog_forbidden_label")
            values = self._bounded_vocabulary(label.get("allowed_values"), maximum=16)
            label_values[label_name] = values
            cardinality *= len(values)
        cardinality_max = self._positive_int(event.get("cardinality_max"))
        if not label_values or cardinality > cardinality_max or cardinality_max > 128:
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_cardinality_budget_exceeded")
        return name, SfuBroadcastAuditRule(outcomes, reason_codes, label_values)

    @staticmethod
    def _bounded_vocabulary(value: object, *, maximum: int) -> frozenset[str]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_audit_catalog_invalid")
        items = tuple(value)
        if (
            not 1 <= len(items) <= maximum
            or len(set(items)) != len(items)
            or any(not isinstance(item, str) or not item or len(item) > 64 for item in items)
        ):
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_audit_catalog_invalid")
        return frozenset(items)

    def _reason(self, key: str) -> str:
        value = self._decision_policy.get(key)
        if not isinstance(value, str) or not value.startswith("sfu_observability_"):
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_decision_policy_invalid")
        return value

    def _reject(self, key: str) -> None:
        raise SfuBroadcastObservabilityPolicyError(self._reason(key))

    @staticmethod
    def _is_forbidden_label(name: str) -> bool:
        normalized = name.casefold()
        return any(fragment in normalized for fragment in FORBIDDEN_LABEL_FRAGMENTS)

    @staticmethod
    def _as_mapping(value: object) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_catalog_invalid")
        return value

    @staticmethod
    def _required_string(value: object) -> str:
        if not isinstance(value, str) or not value:
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_catalog_invalid")
        return value

    @staticmethod
    def _positive_int(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_catalog_invalid")
        return value

    @staticmethod
    def _require_exact_keys(value: Mapping[str, Any], expected: frozenset[str]) -> None:
        if set(value) != set(expected):
            raise SfuBroadcastObservabilityPolicyError("sfu_observability_catalog_invalid")


__all__ = [
    "CATALOG_ID",
    "CATALOG_VERSION",
    "FORBIDDEN_LABEL_FRAGMENTS",
    "MetricDecision",
    "MetricRule",
    "REQUIRED_DOMAINS",
    "REQUIRED_STATISTICS",
    "SfuBroadcastObservabilityPolicy",
    "SfuBroadcastObservabilityPolicyError",
]
