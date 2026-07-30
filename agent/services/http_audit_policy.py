"""Policy for durable HTTP audit events versus bounded read observability."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HttpAuditDecision:
    in_scope: bool
    emit_start: bool
    emit_completion: bool
    observe_metrics: bool
    reason_code: str


class HttpAuditPolicy:
    """Keep security-significant traffic durable without serializing reads."""

    _SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
    _READ_ONLY_OPERATIONS = frozenset(
        {
            ("POST", "/api/source-control/v1/bulk/plan"),
        }
    )

    @staticmethod
    def _in_scope(path: str) -> bool:
        normalized = str(path or "")
        return (
            normalized == "/api"
            or normalized.startswith("/api/")
            or normalized == "/v1/mcp"
            or normalized.startswith("/v1/mcp/")
        )

    @classmethod
    def _is_read_only(cls, *, method: str, path: str) -> bool:
        normalized_method = str(method or "").upper()
        normalized_path = str(path or "")
        return (
            normalized_method in cls._SAFE_METHODS
            or (normalized_method, normalized_path) in cls._READ_ONLY_OPERATIONS
        )

    def request_started(self, *, method: str, path: str) -> HttpAuditDecision:
        if not self._in_scope(path):
            return HttpAuditDecision(False, False, False, False, "outside_audit_scope")
        if self._is_read_only(method=method, path=path):
            return HttpAuditDecision(
                True,
                False,
                False,
                False,
                "read_outcome_required",
            )
        return HttpAuditDecision(True, True, False, False, "mutation_durable")

    def request_completed(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
    ) -> HttpAuditDecision:
        if not self._in_scope(path):
            return HttpAuditDecision(False, False, False, False, "outside_audit_scope")
        safe_read = self._is_read_only(method=method, path=path)
        failed = int(status_code) >= 400
        if safe_read and not failed:
            return HttpAuditDecision(
                True,
                False,
                False,
                True,
                "read_success_metrics_only",
            )
        return HttpAuditDecision(
            True,
            False,
            True,
            False,
            "read_failure_durable" if safe_read else "mutation_durable",
        )


_policy = HttpAuditPolicy()


def get_http_audit_policy() -> HttpAuditPolicy:
    return _policy


__all__ = [
    "HttpAuditDecision",
    "HttpAuditPolicy",
    "get_http_audit_policy",
]
