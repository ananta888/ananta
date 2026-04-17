from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.evolution.models import EvolutionContext
from agent.services.repository_registry import get_repository_registry


@dataclass(frozen=True)
class EvolutionContextBuildOptions:
    audit_limit: int = 50
    verification_limit: int = 10
    artifact_limit: int = 20
    include_audit_details: bool = False


class EvolutionContextBuilder:
    """Builds provider-neutral evolution context from hub-owned repositories."""

    def __init__(self, *, repositories=None):
        self._repositories = repositories

    def build_for_task(
        self,
        task_id: str,
        *,
        objective: str | None = None,
        options: EvolutionContextBuildOptions | None = None,
    ) -> EvolutionContext:
        repos = self._repositories or get_repository_registry()
        task = repos.task_repo.get_by_id(task_id)
        if task is None:
            raise KeyError("task_not_found")

        opts = options or EvolutionContextBuildOptions()
        task_payload = self._dump(task)
        context_bundle = self._context_bundle(repos, task_payload)
        artifact_refs = self._artifact_refs(repos, task_payload, context_bundle, limit=opts.artifact_limit)
        verification = self._verification_signals(repos, task_payload, limit=opts.verification_limit)
        audit = self._audit_signals(
            repos,
            task_payload,
            limit=opts.audit_limit,
            include_details=opts.include_audit_details,
        )

        context_bundle_refs = []
        if context_bundle:
            context_bundle_refs.append({"kind": "context_bundle", "context_bundle_id": context_bundle.get("id")})
        last_proposal = task_payload.get("last_proposal") or {}
        return EvolutionContext(
            objective=self._objective(task_payload, objective),
            task_id=task_payload.get("id"),
            goal_id=task_payload.get("goal_id"),
            trace_id=task_payload.get("goal_trace_id"),
            plan_id=task_payload.get("plan_id"),
            source_refs=[
                {"kind": "task", "task_id": task_payload.get("id")},
                *context_bundle_refs,
                *artifact_refs,
            ],
            signals={
                "task": self._task_signal(task_payload),
                "verification": verification,
                "audit": audit,
                "context_bundle": self._context_bundle_signal(context_bundle),
                "artifacts": artifact_refs,
            },
            constraints={
                "required_capabilities": list(task_payload.get("required_capabilities") or []),
                "verification_spec": dict(task_payload.get("verification_spec") or {}),
                "review_required": bool((last_proposal.get("review") or {}).get("required")),
            },
        )

    def _objective(self, task: dict[str, Any], objective: str | None) -> str:
        requested = str(objective or "").strip()
        if requested:
            return requested
        title = str(task.get("title") or "").strip()
        description = str(task.get("description") or "").strip()
        return title or description or f"Improve task {task.get('id')}"

    def _context_bundle(self, repos, task: dict[str, Any]) -> dict[str, Any]:
        bundle_id = str(task.get("context_bundle_id") or "").strip()
        if not bundle_id:
            return {}
        bundle = repos.context_bundle_repo.get_by_id(bundle_id)
        return self._dump(bundle) if bundle is not None else {"id": bundle_id, "status": "missing"}

    def _task_signal(self, task: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": task.get("id"),
            "title": task.get("title"),
            "description": task.get("description"),
            "status": task.get("status"),
            "priority": task.get("priority"),
            "task_kind": task.get("task_kind"),
            "last_exit_code": task.get("last_exit_code"),
            "last_output_present": bool(task.get("last_output")),
            "updated_at": task.get("updated_at"),
        }

    def _context_bundle_signal(self, bundle: dict[str, Any]) -> dict[str, Any]:
        if not bundle:
            return {}
        return {
            "context_bundle_id": bundle.get("id"),
            "status": bundle.get("status", "available" if bundle.get("id") else "missing"),
            "bundle_type": bundle.get("bundle_type"),
            "chunk_count": len(bundle.get("chunks") or []),
            "token_estimate": int(bundle.get("token_estimate") or 0),
            "metadata": dict(bundle.get("bundle_metadata") or {}),
        }

    def _verification_signals(self, repos, task: dict[str, Any], *, limit: int) -> dict[str, Any]:
        records = list(repos.verification_record_repo.get_by_task_id(str(task.get("id") or "")))[: max(0, limit)]
        items = []
        for record in records:
            payload = self._dump(record)
            items.append(
                {
                    "verification_record_id": payload.get("id"),
                    "status": payload.get("status"),
                    "verification_type": payload.get("verification_type"),
                    "retry_count": payload.get("retry_count"),
                    "repair_attempts": payload.get("repair_attempts"),
                    "escalation_reason": payload.get("escalation_reason"),
                    "results": dict(payload.get("results") or {}),
                }
            )
        return {
            "latest_status": items[0].get("status") if items else None,
            "record_count": len(items),
            "records": items,
        }

    def _audit_signals(self, repos, task: dict[str, Any], *, limit: int, include_details: bool) -> dict[str, Any]:
        task_id = str(task.get("id") or "")
        goal_id = str(task.get("goal_id") or "")
        trace_id = str(task.get("goal_trace_id") or "")
        candidates = list(repos.audit_repo.get_all(limit=max(1, min(limit * 4, 500))))
        items: list[dict[str, Any]] = []
        for entry in candidates:
            payload = self._dump(entry)
            details = dict(payload.get("details") or {})
            if not self._matches_context(payload, details, task_id=task_id, goal_id=goal_id, trace_id=trace_id):
                continue
            row = {
                "audit_log_id": payload.get("id"),
                "action": payload.get("action"),
                "timestamp": payload.get("timestamp"),
                "task_id": payload.get("task_id") or details.get("task_id"),
                "goal_id": payload.get("goal_id") or details.get("goal_id"),
                "trace_id": payload.get("trace_id") or details.get("trace_id"),
            }
            if include_details:
                row["details"] = details
            items.append(row)
            if len(items) >= limit:
                break
        return {"event_count": len(items), "events": items}

    def _artifact_refs(
        self,
        repos,
        task: dict[str, Any],
        bundle: dict[str, Any],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        artifact_ids = self._extract_artifact_ids(task, bundle)
        refs: list[dict[str, Any]] = []
        for artifact_id in artifact_ids[: max(0, limit)]:
            artifact = repos.artifact_repo.get_by_id(artifact_id)
            if artifact is None:
                refs.append({"kind": "artifact", "artifact_id": artifact_id, "status": "missing"})
                continue
            payload = self._dump(artifact)
            refs.append(
                {
                    "kind": "artifact",
                    "artifact_id": payload.get("id"),
                    "status": payload.get("status"),
                    "media_type": payload.get("latest_media_type"),
                    "filename": payload.get("latest_filename"),
                    "size_bytes": payload.get("size_bytes"),
                    "created_by": payload.get("created_by"),
                }
            )
        return refs

    def _extract_artifact_ids(self, task: dict[str, Any], bundle: dict[str, Any]) -> list[str]:
        found: list[str] = []

        def add(value: Any) -> None:
            artifact_id = str(value or "").strip()
            if artifact_id and artifact_id not in found:
                found.append(artifact_id)

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                add(value.get("artifact_id"))
                for item in value.get("artifact_ids") or []:
                    add(item)
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(task.get("last_proposal") or {})
        walk(task.get("history") or [])
        walk((task.get("worker_execution_context") or {}).get("artifact_refs") or [])
        walk(bundle.get("chunks") or [])
        walk(bundle.get("bundle_metadata") or {})
        return found

    def _matches_context(
        self,
        payload: dict[str, Any],
        details: dict[str, Any],
        *,
        task_id: str,
        goal_id: str,
        trace_id: str,
    ) -> bool:
        values = {
            str(payload.get("task_id") or details.get("task_id") or ""),
            str(payload.get("goal_id") or details.get("goal_id") or ""),
            str(payload.get("trace_id") or details.get("trace_id") or ""),
        }
        return bool(
            (task_id and task_id in values)
            or (goal_id and goal_id in values)
            or (trace_id and trace_id in values)
        )

    def _dump(self, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return dict(value)
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if hasattr(value, "dict"):
            return value.dict()
        return dict(getattr(value, "__dict__", {}) or {})
