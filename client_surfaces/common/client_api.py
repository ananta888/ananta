from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

from client_surfaces.common.degraded_state import is_retriable_state, map_status_to_degraded_state
from client_surfaces.common.types import ClientProfile, ClientResponse

TransportFn = Callable[[str, str, dict[str, str], bytes | None, float], tuple[int, str]]


class AnantaApiClient:
    def __init__(self, profile: ClientProfile, *, transport: TransportFn | None = None) -> None:
        self.profile = profile
        self._transport = transport or self._default_transport

    @staticmethod
    def _default_transport(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, str]:
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = int(response.status)
                raw = response.read().decode("utf-8", "replace")
                return status, raw
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            return int(exc.code), raw
        except urllib.error.URLError as exc:
            raise ConnectionError(str(exc)) from exc

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.profile.auth_token:
            headers["Authorization"] = f"Bearer {self.profile.auth_token}"
        return headers

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> ClientResponse:
        url = f"{self.profile.base_url.rstrip('/')}/{path.lstrip('/')}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        try:
            status_code, raw = self._transport(method, url, self._headers(), body, self.profile.timeout_seconds)
        except ConnectionError as exc:
            return ClientResponse(
                ok=False,
                status_code=None,
                state="backend_unreachable",
                data=None,
                error=str(exc),
                retriable=True,
            )
        parse_error = False
        parsed: Any = None
        if raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parse_error = True

        state = map_status_to_degraded_state(status_code, parse_error=parse_error)
        ok = state == "healthy"
        return ClientResponse(
            ok=ok,
            status_code=status_code,
            state=state,
            data=parsed,
            error=None if ok else f"request_failed:{state}",
            retriable=is_retriable_state(state),
        )

    def get_health(self) -> ClientResponse:
        return self._request_json("GET", "/health")

    def get_capabilities(self) -> ClientResponse:
        return self._request_json("GET", "/capabilities")

    def get_dashboard_read_model(
        self, *, benchmark_task_kind: str = "analysis", include_task_snapshot: bool = True
    ) -> ClientResponse:
        query = (
            f"benchmark_task_kind={benchmark_task_kind}&include_task_snapshot={'1' if include_task_snapshot else '0'}"
        )
        return self._request_json("GET", f"/dashboard/read-model?{query}")

    def get_assistant_read_model(self) -> ClientResponse:
        return self._request_json("GET", "/assistant/read-model")

    def get_config(self) -> ClientResponse:
        return self._request_json("GET", "/config")

    def set_config(self, config_payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", "/config", payload=config_payload)

    def list_providers(self) -> ClientResponse:
        return self._request_json("GET", "/providers")

    def list_provider_catalog(self) -> ClientResponse:
        return self._request_json("GET", "/providers/catalog")

    def get_llm_benchmarks(self, *, task_kind: str | None = None, top_n: int | None = None) -> ClientResponse:
        parts: list[str] = []
        if task_kind:
            parts.append(f"task_kind={task_kind}")
        if isinstance(top_n, int) and top_n > 0:
            parts.append(f"top_n={top_n}")
        suffix = f"?{'&'.join(parts)}" if parts else ""
        return self._request_json("GET", f"/llm/benchmarks{suffix}")

    def get_llm_benchmarks_config(self) -> ClientResponse:
        return self._request_json("GET", "/llm/benchmarks/config")

    def get_system_contracts(self) -> ClientResponse:
        return self._request_json("GET", "/api/system/contracts")

    def list_agents(self) -> ClientResponse:
        return self._request_json("GET", "/api/system/agents")

    def get_stats(self) -> ClientResponse:
        return self._request_json("GET", "/api/system/stats")

    def get_stats_history(self) -> ClientResponse:
        return self._request_json("GET", "/api/system/stats/history")

    def get_audit_logs(self, *, limit: int = 50, offset: int = 0) -> ClientResponse:
        return self._request_json("GET", f"/api/system/audit-logs?limit={max(1, limit)}&offset={max(0, offset)}")

    def list_goals(self) -> ClientResponse:
        return self._request_json("GET", "/goals")

    def list_goal_modes(self) -> ClientResponse:
        return self._request_json("GET", "/goals/modes")

    def get_goal(self, goal_id: str) -> ClientResponse:
        return self._request_json("GET", f"/goals/{goal_id}")

    def get_goal_detail(self, goal_id: str) -> ClientResponse:
        return self._request_json("GET", f"/goals/{goal_id}/detail")

    def get_goal_plan(self, goal_id: str) -> ClientResponse:
        return self._request_json("GET", f"/goals/{goal_id}/plan")

    def patch_goal_plan_node(self, goal_id: str, node_id: str, patch_payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("PATCH", f"/goals/{goal_id}/plan/nodes/{node_id}", payload=patch_payload)

    def get_goal_governance_summary(self, goal_id: str) -> ClientResponse:
        return self._request_json("GET", f"/goals/{goal_id}/governance-summary")

    def create_goal(self, payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", "/goals", payload=payload)

    def list_tasks(self) -> ClientResponse:
        return self._request_json("GET", "/tasks")

    def get_task(self, task_id: str) -> ClientResponse:
        return self._request_json("GET", f"/tasks/{task_id}")

    def patch_task(self, task_id: str, patch_payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("PATCH", f"/tasks/{task_id}", payload=patch_payload)

    def assign_task(self, task_id: str, payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", f"/tasks/{task_id}/assign", payload=payload)

    def propose_task_step(self, task_id: str, payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", f"/tasks/{task_id}/step/propose", payload=payload)

    def execute_task_step(self, task_id: str, payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", f"/tasks/{task_id}/step/execute", payload=payload)

    def get_task_timeline(
        self,
        *,
        team_id: str | None = None,
        agent: str | None = None,
        status: str | None = None,
        error_only: bool = False,
        limit: int = 200,
    ) -> ClientResponse:
        parts = [f"limit={max(1, limit)}"]
        if team_id:
            parts.append(f"team_id={team_id}")
        if agent:
            parts.append(f"agent={agent}")
        if status:
            parts.append(f"status={status}")
        if error_only:
            parts.append("error_only=1")
        return self._request_json("GET", f"/tasks/timeline?{'&'.join(parts)}")

    def get_task_orchestration_read_model(self) -> ClientResponse:
        return self._request_json("GET", "/tasks/orchestration/read-model")

    def get_task_logs(self, task_id: str) -> ClientResponse:
        return self._request_json("GET", f"/tasks/{task_id}/logs")

    def list_archived_tasks(self, *, limit: int = 100, offset: int = 0) -> ClientResponse:
        return self._request_json("GET", f"/tasks/archived?limit={max(1, limit)}&offset={max(0, offset)}")

    def restore_archived_task(self, task_id: str) -> ClientResponse:
        return self._request_json("POST", f"/tasks/archived/{task_id}/restore", payload={})

    def cleanup_archived_tasks(self, payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", "/tasks/archived/cleanup", payload=payload)

    def delete_archived_task(self, task_id: str) -> ClientResponse:
        return self._request_json("DELETE", f"/tasks/archived/{task_id}")

    def list_artifacts(self) -> ClientResponse:
        return self._request_json("GET", "/artifacts")

    def get_artifact(self, artifact_id: str) -> ClientResponse:
        return self._request_json("GET", f"/artifacts/{artifact_id}")

    def extract_artifact(self, artifact_id: str) -> ClientResponse:
        return self._request_json("POST", f"/artifacts/{artifact_id}/extract", payload={})

    def index_artifact(self, artifact_id: str, payload: dict[str, Any] | None = None) -> ClientResponse:
        return self._request_json("POST", f"/artifacts/{artifact_id}/rag-index", payload=payload or {})

    def get_artifact_rag_status(self, artifact_id: str) -> ClientResponse:
        return self._request_json("GET", f"/artifacts/{artifact_id}/rag-status")

    def get_artifact_rag_preview(self, artifact_id: str, *, limit: int = 5) -> ClientResponse:
        return self._request_json("GET", f"/artifacts/{artifact_id}/rag-preview?limit={max(1, limit)}")

    def list_approvals(self) -> ClientResponse:
        return self._request_json("GET", "/approvals")

    def list_repairs(self) -> ClientResponse:
        return self._request_json("GET", "/repairs")

    def list_knowledge_collections(self) -> ClientResponse:
        return self._request_json("GET", "/knowledge/collections")

    def list_knowledge_index_profiles(self) -> ClientResponse:
        return self._request_json("GET", "/knowledge/index-profiles")

    def get_knowledge_collection(self, collection_id: str) -> ClientResponse:
        return self._request_json("GET", f"/knowledge/collections/{collection_id}")

    def index_knowledge_collection(self, collection_id: str, payload: dict[str, Any] | None = None) -> ClientResponse:
        return self._request_json("POST", f"/knowledge/collections/{collection_id}/index", payload=payload or {})

    def search_knowledge_collection(self, collection_id: str, *, query: str, top_k: int = 5) -> ClientResponse:
        return self._request_json(
            "POST",
            f"/knowledge/collections/{collection_id}/search",
            payload={"query": query, "top_k": max(1, top_k)},
        )

    def list_templates(self) -> ClientResponse:
        return self._request_json("GET", "/templates")

    def get_template_variable_registry(self) -> ClientResponse:
        return self._request_json("GET", "/templates/variable-registry")

    def get_template_sample_contexts(self) -> ClientResponse:
        return self._request_json("GET", "/templates/sample-contexts")

    def validate_template(self, payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", "/templates/validate", payload=payload)

    def preview_template(self, payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", "/templates/preview", payload=payload)

    def template_validation_diagnostics(self, payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", "/templates/validation-diagnostics", payload=payload)

    def list_teams(self) -> ClientResponse:
        return self._request_json("GET", "/teams")

    def list_blueprints(self) -> ClientResponse:
        return self._request_json("GET", "/teams/blueprints")

    def list_blueprint_catalog(self) -> ClientResponse:
        return self._request_json("GET", "/teams/blueprints/catalog")

    def get_blueprint(self, blueprint_id: str) -> ClientResponse:
        return self._request_json("GET", f"/teams/blueprints/{blueprint_id}")

    def list_team_types(self) -> ClientResponse:
        return self._request_json("GET", "/teams/types")

    def list_team_roles(self) -> ClientResponse:
        return self._request_json("GET", "/teams/roles")

    def list_roles_for_team_type(self, type_id: str) -> ClientResponse:
        return self._request_json("GET", f"/teams/types/{type_id}/roles")

    def activate_team(self, team_id: str) -> ClientResponse:
        return self._request_json("POST", f"/teams/{team_id}/activate", payload={})

    def get_instruction_layer_model(self) -> ClientResponse:
        return self._request_json("GET", "/instruction-layers/model")

    def get_instruction_layers_effective(
        self,
        *,
        owner_username: str | None = None,
        task_id: str | None = None,
        goal_id: str | None = None,
        session_id: str | None = None,
        usage_key: str | None = None,
        profile_id: str | None = None,
        overlay_id: str | None = None,
    ) -> ClientResponse:
        parts: list[str] = []
        if owner_username:
            parts.append(f"owner_username={owner_username}")
        if task_id:
            parts.append(f"task_id={task_id}")
        if goal_id:
            parts.append(f"goal_id={goal_id}")
        if session_id:
            parts.append(f"session_id={session_id}")
        if usage_key:
            parts.append(f"usage_key={usage_key}")
        if profile_id:
            parts.append(f"profile_id={profile_id}")
        if overlay_id:
            parts.append(f"overlay_id={overlay_id}")
        suffix = f"?{'&'.join(parts)}" if parts else ""
        return self._request_json("GET", f"/instruction-layers/effective{suffix}")

    def list_instruction_profiles(self, *, owner_username: str | None = None) -> ClientResponse:
        suffix = f"?owner_username={owner_username}" if owner_username else ""
        return self._request_json("GET", f"/instruction-profiles{suffix}")

    def list_instruction_overlays(
        self,
        *,
        owner_username: str | None = None,
        attachment_kind: str | None = None,
        attachment_id: str | None = None,
    ) -> ClientResponse:
        parts: list[str] = []
        if owner_username:
            parts.append(f"owner_username={owner_username}")
        if attachment_kind:
            parts.append(f"attachment_kind={attachment_kind}")
        if attachment_id:
            parts.append(f"attachment_id={attachment_id}")
        suffix = f"?{'&'.join(parts)}" if parts else ""
        return self._request_json("GET", f"/instruction-overlays{suffix}")

    def select_instruction_profile(self, profile_id: str) -> ClientResponse:
        return self._request_json("POST", f"/instruction-profiles/{profile_id}/select", payload={})

    def select_instruction_overlay(self, overlay_id: str, payload: dict[str, Any] | None = None) -> ClientResponse:
        return self._request_json("POST", f"/instruction-overlays/{overlay_id}/select", payload=payload or {})

    def link_instruction_overlay(self, overlay_id: str, payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", f"/instruction-overlays/{overlay_id}/attach", payload=payload)

    def unlink_instruction_overlay(self, overlay_id: str) -> ClientResponse:
        return self._request_json("POST", f"/instruction-overlays/{overlay_id}/detach", payload={})

    def set_goal_instruction_selection(self, goal_id: str, payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", f"/goals/{goal_id}/instruction-selection", payload=payload)

    def set_task_instruction_selection(self, task_id: str, payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", f"/tasks/{task_id}/instruction-selection", payload=payload)

    def get_autopilot_status(self) -> ClientResponse:
        return self._request_json("GET", "/tasks/autopilot/status")

    def get_auto_planner_status(self) -> ClientResponse:
        return self._request_json("GET", "/tasks/auto-planner/status")

    def get_triggers_status(self) -> ClientResponse:
        return self._request_json("GET", "/triggers/status")

    def start_autopilot(self, payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", "/tasks/autopilot/start", payload=payload)

    def stop_autopilot(self) -> ClientResponse:
        return self._request_json("POST", "/tasks/autopilot/stop", payload={})

    def tick_autopilot(self) -> ClientResponse:
        return self._request_json("POST", "/tasks/autopilot/tick", payload={})

    def configure_auto_planner(self, payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", "/tasks/auto-planner/configure", payload=payload)

    def configure_triggers(self, payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", "/triggers/configure", payload=payload)

    def analyze_audit_logs(self, *, limit: int = 50) -> ClientResponse:
        return self._request_json("POST", f"/api/system/audit/analyze?limit={max(1, limit)}", payload={})

    def submit_goal(self, goal_text: str, context_payload: dict[str, Any]) -> ClientResponse:
        payload = {"goal_text": goal_text, "context": context_payload}
        return self._request_json("POST", "/goals", payload=payload)

    def analyze_context(self, context_payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", "/tasks/analyze", payload={"context": context_payload})

    def review_context(self, context_payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", "/tasks/review", payload={"context": context_payload})

    def patch_plan(self, context_payload: dict[str, Any]) -> ClientResponse:
        return self._request_json("POST", "/tasks/patch-plan", payload={"context": context_payload})

    def create_project_new(self, goal_text: str, context_payload: dict[str, Any]) -> ClientResponse:
        payload = {"goal_text": goal_text, "context": context_payload}
        return self._request_json("POST", "/projects/new", payload=payload)

    def create_project_evolve(self, goal_text: str, context_payload: dict[str, Any]) -> ClientResponse:
        payload = {"goal_text": goal_text, "context": context_payload}
        return self._request_json("POST", "/projects/evolve", payload=payload)
