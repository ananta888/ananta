from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


class WorkerPolicyService:
    """Filter workers by task capabilities and llm scope constraints."""

    @staticmethod
    def _is_local_worker_url(url: str) -> bool:
        try:
            host = str(urlparse(str(url or "").strip()).hostname or "").strip().lower()
        except Exception:
            host = ""
        return host in {"localhost", "127.0.0.1", "::1", "host.docker.internal"}

    @staticmethod
    def _required_capabilities(task: Any) -> list[str]:
        values = list(getattr(task, "required_capabilities", None) or [])
        return [str(v).strip().lower() for v in values if str(v).strip()]

    @staticmethod
    def _task_llm_scope(task: Any) -> str | None:
        task_ctx = dict(getattr(task, "worker_execution_context", None) or {})
        ws_policy = dict(task_ctx.get("workspace_context_policy") or {})
        scope = str(ws_policy.get("llm_scope") or task_ctx.get("llm_scope") or "").strip().lower()
        return scope or None

    def filter_candidates(
        self,
        *,
        task: Any,
        workers: list[Any],
        policy_cfg: dict[str, Any] | None = None,
    ) -> tuple[list[Any], list[dict[str, Any]]]:
        cfg = dict(policy_cfg or {})
        enabled = bool(cfg.get("enabled", False))
        enforce_caps = bool(cfg.get("enforce_required_capabilities", True))
        enforce_scope = bool(cfg.get("enforce_llm_scope", True))
        if not enabled:
            return list(workers or []), []

        required_caps = self._required_capabilities(task)
        llm_scope = self._task_llm_scope(task)
        accepted: list[Any] = []
        rejected: list[dict[str, Any]] = []
        for worker in list(workers or []):
            worker_url = str(getattr(worker, "url", "") or "").strip()
            worker_caps = [str(v).strip().lower() for v in list(getattr(worker, "capabilities", None) or []) if str(v).strip()]
            missing = [cap for cap in required_caps if cap not in set(worker_caps)]
            if enforce_caps and missing:
                rejected.append({"worker_url": worker_url, "reason_code": "missing_capability", "missing_capabilities": missing})
                continue
            if enforce_scope and llm_scope == "local_only" and not self._is_local_worker_url(worker_url):
                rejected.append({"worker_url": worker_url, "reason_code": "llm_scope_denied", "llm_scope": llm_scope})
                continue
            accepted.append(worker)
        return accepted, rejected


_SERVICE = WorkerPolicyService()


def get_worker_policy_service() -> WorkerPolicyService:
    return _SERVICE
