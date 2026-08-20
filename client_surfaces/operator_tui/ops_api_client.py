from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class OpsApiHttpError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = int(status_code)
        self.code = str(code or f"hub_http_{status_code}")
        self.payload = dict(payload or {})
        super().__init__(str(message or self.code))


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpsApiClient:
    """Tiny TUI adapter for the hub-owned /api/ops contract."""

    def __init__(self, hub_url: str, token: str = "") -> None:
        self._hub_url = str(hub_url or "").rstrip("/")
        self._token = str(token or "")

    def git_status(self, workspace_id: str = "repo") -> dict[str, Any]:
        query = urllib.parse.urlencode({"workspace_id": workspace_id})
        return self._get(f"/api/ops/git/status?{query}")

    def docker_status(self) -> dict[str, Any]:
        return self._get("/api/ops/docker/status")

    def compose_projects(self) -> dict[str, Any]:
        return self._get("/api/ops/compose/projects")

    def workflow_runtime_operations(self, *, health: str = "") -> dict[str, Any]:
        query = urllib.parse.urlencode({"health": health}) if health else ""
        suffix = f"?{query}" if query else ""
        return self._get(f"/api/workflow-runtime/operations{suffix}")

    def workflow_runtime_run(self, run_id: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(str(run_id), safe="")
        return self._get(f"/api/workflow-runtime/operations/runs/{encoded}")

    def operation_policy_inventory(self) -> dict[str, Any]:
        return self._get("/governance/operations")

    def snapshot(self, workspace_id: str = "repo") -> dict[str, Any]:
        git = self.git_status(workspace_id)
        docker = self.docker_status()
        compose = self.compose_projects()
        return {
            "git": git,
            "docker": docker,
            "compose": compose,
            "traffic_lights": {
                "git_dirty": bool((git.get("data") or git).get("dirty")),
                "docker_engine": "green" if bool((docker.get("data") or docker).get("available")) else "red",
                "compose_health": "green" if ((compose.get("data") or compose).get("count") or 0) else "yellow",
            },
        }

    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        normalized_method = str(method or "").strip().upper()
        normalized_path = str(path or "").strip()
        if normalized_method not in {"GET", "POST"}:
            raise ValueError("hub_http_method_not_allowed")
        if (
            not normalized_path.startswith("/")
            or normalized_path.startswith("//")
            or "://" in normalized_path
        ):
            raise ValueError("hub_http_path_must_be_relative")
        url = f"{self._hub_url}{normalized_path}"
        request_headers = {"Accept": "application/json"}
        body: bytes | None = None
        if self._token:
            request_headers["Authorization"] = f"Bearer {self._token}"
        for name, value in dict(headers or {}).items():
            normalized_name = str(name or "").strip()
            if not normalized_name or normalized_name.lower() in {"authorization", "host"}:
                raise ValueError("hub_http_header_not_allowed")
            request_headers[normalized_name] = str(value)
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
            body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers=request_headers,
            method=normalized_method,
        )
        opener = urllib.request.build_opener(_NoRedirectHandler())
        try:
            with opener.open(request, timeout=max(0.1, float(timeout))) as response:
                raw = response.read(2_097_153)
        except urllib.error.HTTPError as exc:
            raw = exc.read(2_097_153)
            parsed = self._decode_payload(raw)
            code, message = self._error_fields(parsed, exc.code)
            raise OpsApiHttpError(
                status_code=exc.code,
                code=code,
                message=message,
                payload=parsed,
            ) from exc
        except urllib.error.URLError as exc:
            raise OpsApiHttpError(
                status_code=503,
                code="hub_http_unavailable",
                message="hub_http_unavailable",
            ) from exc
        if len(raw) > 2_097_152:
            raise OpsApiHttpError(
                status_code=502,
                code="hub_http_response_too_large",
                message="hub_http_response_too_large",
            )
        return self._decode_payload(raw)

    @staticmethod
    def _decode_payload(raw: bytes) -> dict[str, Any]:
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OpsApiHttpError(
                status_code=502,
                code="hub_http_response_invalid",
                message="hub_http_response_invalid",
            ) from exc
        if not isinstance(value, dict):
            raise OpsApiHttpError(
                status_code=502,
                code="hub_http_response_invalid",
                message="hub_http_response_invalid",
            )
        return value

    @staticmethod
    def _error_fields(payload: dict[str, Any], status_code: int) -> tuple[str, str]:
        nested = payload.get("error")
        nested = nested if isinstance(nested, dict) else {}
        code = str(
            nested.get("code")
            or payload.get("reason_code")
            or payload.get("message")
            or f"hub_http_{status_code}"
        )
        message = str(nested.get("message") or payload.get("message") or code)
        return code, message

    def _get(self, path: str) -> dict[str, Any]:
        url = f"{self._hub_url}{path}"
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                payload = {"status": "error", "message": str(exc)}
            return payload
        except Exception as exc:
            return {"status": "error", "message": "ops_api_unavailable", "data": {"error": str(exc)}}
