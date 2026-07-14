from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


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
