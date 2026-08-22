"""Task-registry adapter for Hub-delegated HRM execution."""

from __future__ import annotations

import os
import json
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from agent.auth import read_file_managed_token
from worker.hrm_experiments.heartbeat import HrmCapabilityHeartbeat
from worker.hrm_experiments.protocol import receive_message, send_message


class HrmExperimentWorkerConfigurationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class HrmExperimentHubClientError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class HttpHrmExperimentHubClient:
    """Least-privilege Worker transport for Hub authorization and results."""

    def __init__(
        self,
        *,
        hub_url: str,
        bearer_token: str,
        worker_id: str,
        worker_url: str,
        timeout_seconds: float = 15.0,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(str(hub_url or "").rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HrmExperimentWorkerConfigurationError("hrm_hub_url_invalid")
        if not worker_id or not worker_url:
            raise HrmExperimentWorkerConfigurationError("hrm_worker_identity_missing")
        if not 32 <= len(bearer_token.encode("utf-8")) <= 16_384:
            raise HrmExperimentWorkerConfigurationError("hrm_worker_token_invalid")
        self._hub_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
        )
        self._token = bearer_token
        self._worker_id = worker_id
        self._worker_url = worker_url.rstrip("/")
        self._timeout = max(1.0, min(float(timeout_seconds), 60.0))
        self._ssl_context = ssl_context

    @classmethod
    def from_environment(
        cls, env: Mapping[str, str] | None = None
    ) -> "HttpHrmExperimentHubClient":
        source = os.environ if env is None else env
        hub_url = str(
            source.get("ANANTA_HRM_HUB_URL")
            or source.get("ANANTA_WORKFLOW_HUB_URL")
            or source.get("HUB_URL")
            or ""
        ).strip()
        token_file = str(
            source.get("ANANTA_HRM_HUB_TOKEN_FILE")
            or source.get("ANANTA_WORKFLOW_HUB_TOKEN_FILE")
            or source.get("AGENT_TOKEN_FILE")
            or ""
        ).strip()
        if not hub_url or not token_file:
            raise HrmExperimentWorkerConfigurationError("hrm_hub_not_configured")
        try:
            token = read_file_managed_token(
                token_file, description="HRM experiment Hub token file"
            )
        except Exception as exc:
            raise HrmExperimentWorkerConfigurationError(
                "hrm_worker_token_unavailable"
            ) from exc
        return cls(
            hub_url=hub_url,
            bearer_token=token,
            worker_id=str(source.get("AGENT_NAME") or "").strip(),
            worker_url=str(source.get("AGENT_URL") or "").strip(),
        )

    def advertise_capability(self, capability: Mapping[str, Any]) -> None:
        response = self._post(
            "capabilities",
            {"capability": dict(capability), "ttl_seconds": 120},
            65_536,
        )
        if response.get("accepted") is not True:
            raise HrmExperimentHubClientError("hrm_capability_rejected")

    def authorize(
        self, *, run_id: str, task_id: str, worker_job_id: str
    ) -> dict[str, Any]:
        response = self._post(
            "authorize",
            {
                "run_id": run_id,
                "task_id": task_id,
                "worker_job_id": worker_job_id,
            },
            2_200_000,
        )
        execution = response.get("execution")
        if response.get("authorized") is not True or not isinstance(execution, dict):
            raise HrmExperimentHubClientError("hrm_authorization_invalid")
        return execution

    def submit_result(self, *, run_id: str, result: Mapping[str, Any]) -> None:
        response = self._post(
            "results", {"run_id": run_id, "result": dict(result)}, 65_536
        )
        if response.get("accepted") is not True or response.get("run_id") != run_id:
            raise HrmExperimentHubClientError("hrm_result_rejected")

    def _post(
        self, operation: str, payload: Mapping[str, Any], response_limit: int
    ) -> dict[str, Any]:
        body = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        http_request = urllib.request.Request(
            f"{self._hub_url}/api/hrm-experiments/internal/{operation}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Ananta-Worker-ID": self._worker_id,
                "X-Ananta-Worker-URL": self._worker_url,
                "User-Agent": "ananta-hrm-experiment-worker/1",
            },
        )
        try:
            with urllib.request.urlopen(
                http_request,
                timeout=self._timeout,
                context=self._ssl_context,
            ) as response:
                raw = response.read(response_limit + 1)
        except urllib.error.HTTPError as exc:
            reason = "hrm_hub_rejected"
            try:
                decoded = json.loads(exc.read(65_537).decode("utf-8"))
                reason = str(
                    decoded.get("reason_code")
                    or ((decoded.get("error") or {}).get("code"))
                    or reason
                )
            except Exception:
                pass
            raise HrmExperimentHubClientError(reason) from exc
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise HrmExperimentHubClientError("hrm_hub_unavailable") from exc
        if len(raw) > response_limit:
            raise HrmExperimentHubClientError("hrm_hub_response_too_large")
        try:
            decoded = json.loads(raw.decode("utf-8"))
            data = decoded.get("data")
        except (UnicodeError, json.JSONDecodeError, AttributeError) as exc:
            raise HrmExperimentHubClientError("hrm_hub_response_invalid") from exc
        if not isinstance(data, dict):
            raise HrmExperimentHubClientError("hrm_hub_response_invalid")
        return data


class UnixHrmRunnerClient:
    def __init__(self, socket_path: str | Path, *, timeout_seconds: float = 5.0) -> None:
        path = Path(socket_path)
        if not path.is_absolute():
            raise HrmExperimentWorkerConfigurationError(
                "hrm_runner_socket_must_be_absolute"
            )
        self._socket_path = path
        self._timeout_seconds = max(0.1, min(float(timeout_seconds), 30.0))

    def execute(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        response = self._request("execute", payload)
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("hrm_runner_result_invalid")
        return result

    def capability(self) -> dict[str, Any]:
        response = self._request("capability", {})
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("hrm_runner_capability_invalid")
        return result

    def _request(self, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(self._timeout_seconds)
            connection.connect(str(self._socket_path))
            send_message(connection, {"op": operation, "payload": dict(payload)})
            response = receive_message(connection)
        if response.get("status") != "ok":
            reason = str(response.get("reason_code") or "hrm_runner_failed")
            raise RuntimeError(reason)
        return response


class RegisteredHrmExperimentTaskHandler:
    """Worker adapter with no task creation or orchestration surface."""

    def __init__(
        self,
        runner: UnixHrmRunnerClient,
        hub: HttpHrmExperimentHubClient,
        capability: Mapping[str, Any],
        heartbeat: HrmCapabilityHeartbeat | None = None,
    ) -> None:
        self._runner = runner
        self._hub = hub
        self._capability = dict(capability)
        self._heartbeat = heartbeat

    @property
    def capability_heartbeat(self) -> HrmCapabilityHeartbeat | None:
        return self._heartbeat

    def capability(self) -> dict[str, Any]:
        return dict(self._capability)

    def propose(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "status": "blocked",
            "reason": "hrm_hub_delegation_required",
            "authoritative_source": "hub",
        }

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        task = kwargs.get("task")
        context = task.get("worker_execution_context") if isinstance(task, Mapping) else None
        envelope = context.get("hrm_experiment") if isinstance(context, Mapping) else None
        run_id = str(envelope.get("run_id") or "") if isinstance(envelope, Mapping) else ""
        task_id = str(task.get("id") or "") if isinstance(task, Mapping) else ""
        worker_job_id = str(task.get("current_worker_job_id") or "") if isinstance(task, Mapping) else ""
        if not run_id or not task_id or not worker_job_id:
            return {
                "status": "failed",
                "reason_code": "hrm_execution_context_missing",
                "exit_code": 1,
            }
        try:
            if self._heartbeat is not None:
                self._heartbeat.refresh()
            authorized = self._hub.authorize(
                run_id=run_id,
                task_id=task_id,
                worker_job_id=worker_job_id,
            )
            result = self._runner.execute(authorized)
            self._hub.submit_result(run_id=run_id, result=result)
        except Exception as exc:
            reason = str(exc)[:128] or "hrm_worker_failed"
            return {
                "status": "failed",
                "reason_code": reason,
                "output": reason,
                "exit_code": 1,
            }
        return {
            "status": "completed",
            "reason_code": "hrm_experiment_completed",
            "output": "Hub-delegated HRM experiment completed.",
            "exit_code": 0,
            "hrm_experiment_result": result,
            "artifacts": list(result.get("artifacts") or []),
        }


def build_hrm_experiment_task_handler() -> RegisteredHrmExperimentTaskHandler:
    socket_path = os.environ.get("ANANTA_HRM_RUNNER_SOCKET", "").strip()
    if not socket_path:
        raise HrmExperimentWorkerConfigurationError("hrm_runner_socket_required")
    runner = UnixHrmRunnerClient(socket_path)
    hub = HttpHrmExperimentHubClient.from_environment()
    try:
        capability = runner.capability()
        hub.advertise_capability(capability)
    except Exception as exc:
        raise HrmExperimentWorkerConfigurationError(
            "hrm_runner_capability_unavailable"
        ) from exc
    heartbeat = HrmCapabilityHeartbeat(hub, capability)
    heartbeat.start()
    return RegisteredHrmExperimentTaskHandler(
        runner, hub, capability, heartbeat=heartbeat
    )


__all__ = [
    "HrmExperimentWorkerConfigurationError",
    "HrmExperimentHubClientError",
    "HttpHrmExperimentHubClient",
    "RegisteredHrmExperimentTaskHandler",
    "UnixHrmRunnerClient",
    "build_hrm_experiment_task_handler",
]
