from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from agent.config import settings
from agent.repository import agent_repo
from agent.services.lmstudio_request_registry import (
    active_counts as local_active_goal_counts,
    active_task_counts as local_active_task_counts,
    cancel_all as local_cancel_all,
    cancel_goal as local_cancel_goal,
    cancel_task as local_cancel_task,
)

_LOG = logging.getLogger(__name__)


class RequestCancellationService:
    """Cancel in-flight provider requests for goal/task/global scopes."""

    def __init__(self) -> None:
        self._timeout_seconds = 1.5
        # PRI-008: retry unreachable workers up to N times with a short delay.
        self._retry_attempts = 2
        self._retry_delay_s = 0.4

    @staticmethod
    def _is_hub() -> bool:
        return str(getattr(settings, "role", "") or "").strip().lower() == "hub"

    @staticmethod
    def _my_url() -> str:
        return str(settings.agent_url or f"http://localhost:{settings.port}").rstrip("/")

    def _worker_targets(self) -> list[tuple[str, str | None]]:
        targets: list[tuple[str, str | None]] = []
        my_url = self._my_url()
        try:
            for agent in agent_repo.get_all():
                role = str(getattr(agent, "role", "") or "").strip().lower()
                url = str(getattr(agent, "url", "") or "").strip().rstrip("/")
                if role != "worker" or not url or url == my_url:
                    continue
                token = str(getattr(agent, "token", "") or "").strip() or None
                targets.append((url, token))
        except Exception:
            _LOG.debug("request_cancel_worker_target_scan_failed", exc_info=True)
        return targets

    def _post(self, url: str, endpoint: str, *, token: str | None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST to a worker endpoint with retry on connection errors and 5xx responses (PRI-008).

        4xx responses are not retried (client errors — retrying won't help).
        """
        full_url = f"{url}{endpoint}"
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        last_error: str = ""
        last_status: int = 0
        for attempt in range(max(1, self._retry_attempts)):
            try:
                response = requests.post(full_url, json=payload or {}, headers=headers, timeout=self._timeout_seconds)
                last_status = int(response.status_code)
                try:
                    body = response.json()
                except Exception:
                    body = {"raw": response.text[:500]}
                if last_status < 500:
                    # 2xx/3xx = success; 4xx = client error (not retriable).
                    return {
                        "url": full_url,
                        "ok": last_status < 400,
                        "status_code": last_status,
                        "body": body,
                        "attempts": attempt + 1,
                    }
                # 5xx: server-side transient error — retry if attempts remain.
                last_error = f"http_{last_status}"
            except Exception as exc:
                last_error = str(exc)
                last_status = 0
            if attempt < self._retry_attempts - 1:
                _LOG.debug(
                    "worker_cancel_retry attempt=%s url=%s error=%s",
                    attempt + 1, full_url, last_error,
                )
                time.sleep(self._retry_delay_s)
        _LOG.warning(
            "worker_cancel_failed_all_attempts url=%s attempts=%s status=%s error=%s",
            full_url, self._retry_attempts, last_status, last_error,
        )
        return {"url": full_url, "ok": False, "status_code": last_status, "error": last_error, "attempts": self._retry_attempts}

    def _fanout(self, endpoint: str, *, payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        targets = self._worker_targets()
        if not targets:
            return []
        max_workers = max(1, min(8, len(targets)))
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self._post, worker_url, endpoint, token=worker_token, payload=payload)
                for worker_url, worker_token in targets
            ]
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append({"ok": False, "status_code": 0, "error": str(exc)})
        return results

    def cancel_goal_requests(self, *, goal_id: str, include_workers: bool = True) -> dict[str, Any]:
        goal_id_norm = str(goal_id or "").strip()
        local_killed = local_cancel_goal(goal_id_norm)
        fanout_results: list[dict[str, Any]] = []
        if include_workers and self._is_hub():
            fanout_results = self._fanout(f"/internal/goals/{goal_id_norm}/kill-requests")
        return {
            "goal_id": goal_id_norm,
            "sessions_killed_local": int(local_killed),
            "fanout": fanout_results,
            "active_goal_counts_local": local_active_goal_counts(),
            "active_task_counts_local": local_active_task_counts(),
        }

    def cancel_task_requests(self, *, task_id: str, include_workers: bool = True) -> dict[str, Any]:
        task_id_norm = str(task_id or "").strip()
        local_killed = local_cancel_task(task_id_norm)
        fanout_results: list[dict[str, Any]] = []
        if include_workers and self._is_hub():
            fanout_results = self._fanout(f"/internal/tasks/{task_id_norm}/kill-requests")
        return {
            "task_id": task_id_norm,
            "sessions_killed_local": int(local_killed),
            "fanout": fanout_results,
            "active_goal_counts_local": local_active_goal_counts(),
            "active_task_counts_local": local_active_task_counts(),
        }

    def cancel_all_requests(self, *, include_workers: bool = True) -> dict[str, Any]:
        local_killed = local_cancel_all()
        fanout_results: list[dict[str, Any]] = []
        if include_workers and self._is_hub():
            fanout_results = self._fanout("/internal/goals/kill-all-requests")
        return {
            "sessions_killed_local": int(local_killed),
            "fanout": fanout_results,
            "active_goal_counts_local": local_active_goal_counts(),
            "active_task_counts_local": local_active_task_counts(),
        }


_SERVICE = RequestCancellationService()


def get_request_cancellation_service() -> RequestCancellationService:
    return _SERVICE
