"""Low-cardinality metrics adapter for provider-neutral mail operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_PROVIDERS = frozenset({"jmap", "imap", "none"})
_OPERATIONS = frozenset(
    {"discovery", "sync", "query", "metadata", "body", "attachment", "mutation", "migration", "cutover", "diagnose"}
)
_OUTCOMES = frozenset({"success", "failure", "cancelled", "blocked"})
_ERROR_CLASSES = frozenset(
    {"none", "config", "auth", "transport", "protocol", "policy", "store", "timeout", "unknown"}
)
_CHANGE_KINDS = frozenset({"created", "updated", "destroyed", "stale", "reset"})
_CIRCUIT_STATES = frozenset({"closed", "open", "half_open"})


def _bounded(value: str, allowed: frozenset[str], fallback: str) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in allowed else fallback


@dataclass(frozen=True)
class MailMetricLabels:
    provider: str
    operation: str
    outcome: str
    error_class: str

    @classmethod
    def normalize(
        cls,
        *,
        provider: str,
        operation: str,
        outcome: str,
        error_class: str = "none",
    ) -> "MailMetricLabels":
        return cls(
            provider=_bounded(provider, _PROVIDERS, "none"),
            operation=_bounded(operation, _OPERATIONS, "diagnose"),
            outcome=_bounded(outcome, _OUTCOMES, "failure"),
            error_class=_bounded(error_class, _ERROR_CLASSES, "unknown"),
        )


class MailMetricsAdapter:
    """No account, mailbox, message, URL, reason string or tenant labels."""

    def record_call(
        self,
        *,
        provider: str,
        operation: str,
        outcome: str,
        duration_seconds: float,
        error_class: str = "none",
    ) -> None:
        from agent.metrics import (
            MAIL_PROVIDER_CALL_DURATION_SECONDS,
            MAIL_PROVIDER_CALLS_TOTAL,
        )

        labels = MailMetricLabels.normalize(
            provider=provider,
            operation=operation,
            outcome=outcome,
            error_class=error_class,
        )
        MAIL_PROVIDER_CALLS_TOTAL.labels(
            labels.provider,
            labels.operation,
            labels.outcome,
            labels.error_class,
        ).inc()
        MAIL_PROVIDER_CALL_DURATION_SECONDS.labels(
            labels.provider,
            labels.operation,
            labels.outcome,
        ).observe(max(0.0, float(duration_seconds)))

    def record(self, event: Any) -> None:
        reason = str(getattr(event, "reason_code", "") or "").lower()
        error_class = "none"
        for candidate, markers in {
            "auth": ("auth", "credential", "secret"),
            "timeout": ("timeout", "deadline"),
            "transport": ("connection", "network", "http", "dns"),
            "policy": ("policy", "disabled", "denied", "forbidden"),
            "store": ("store", "persist", "database"),
            "protocol": ("jmap", "imap", "protocol"),
            "config": ("config", "endpoint"),
        }.items():
            if any(marker in reason for marker in markers):
                error_class = candidate
                break
        outcome = (
            "success"
            if str(getattr(event, "outcome", "")).lower()
            in {"ok", "success", "completed"}
            else "failure"
        )
        operation = str(getattr(event, "phase", "") or "diagnose")
        self.record_call(
            provider=str(getattr(event, "provider", "") or "none"),
            operation=operation,
            outcome=outcome,
            error_class=error_class,
            duration_seconds=max(
                0.0,
                float(getattr(event, "duration_ms", 0) or 0) / 1000.0,
            ),
        )
        if bool(getattr(event, "retryable", False)):
            self.record_retry(
                provider=str(getattr(event, "provider", "") or "none"),
                operation=operation,
                error_class=error_class,
            )

    def record_retry(
        self,
        *,
        provider: str,
        operation: str,
        error_class: str,
    ) -> None:
        from agent.metrics import MAIL_PROVIDER_RETRIES_TOTAL

        labels = MailMetricLabels.normalize(
            provider=provider,
            operation=operation,
            outcome="failure",
            error_class=error_class,
        )
        MAIL_PROVIDER_RETRIES_TOTAL.labels(
            labels.provider,
            labels.operation,
            labels.error_class,
        ).inc()

    def record_sync_changes(
        self,
        *,
        provider: str,
        change_kind: str,
        count: int,
    ) -> None:
        from agent.metrics import MAIL_SYNC_CHANGES_TOTAL

        MAIL_SYNC_CHANGES_TOTAL.labels(
            _bounded(provider, _PROVIDERS, "none"),
            _bounded(change_kind, _CHANGE_KINDS, "stale"),
        ).inc(max(0, int(count)))

    def record_circuit_state(self, provider: str, state: str) -> None:
        from agent.metrics import MAIL_CIRCUIT_STATE

        bounded_provider = _bounded(provider, _PROVIDERS, "none")
        bounded_state = _bounded(state, _CIRCUIT_STATES, "open")
        for candidate in sorted(_CIRCUIT_STATES):
            MAIL_CIRCUIT_STATE.labels(bounded_provider, candidate).set(
                1 if candidate == bounded_state else 0
            )

    @staticmethod
    def bounded_labels_for_test(**kwargs: Any) -> dict[str, str]:
        return MailMetricLabels.normalize(**kwargs).__dict__.copy()


__all__ = ["MailMetricLabels", "MailMetricsAdapter"]
