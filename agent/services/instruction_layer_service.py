from __future__ import annotations

import time
from typing import Any

from agent.common.audit import log_audit
from agent.db_models import GoalDB, InstructionOverlayDB, TaskDB, UserInstructionProfileDB
from agent.services.instruction_layer_compiler import (
    _LAYER_MODEL_VERSION,
    _OVERLAY_ATTACHMENT_KINDS,
    _OVERLAY_SCOPES,
    _STACK_VERSION,
    InstructionLayerService as InstructionLayerCompiler,
)
from agent.services.repository_registry import get_repository_registry


class InstructionLayerService(InstructionLayerCompiler):
    """Resolves policy-safe instruction layers and renders a deterministic stack artifact.

    Inherits layer compilation logic (validation, normalization, compatibility, assembly)
    from InstructionLayerCompiler. This class provides the selection/resolution API layer.
    """

    def resolve_profile_for_owner(
        self,
        *,
        owner_username: str,
        explicit_profile_id: str | None = None,
    ) -> UserInstructionProfileDB | None:
        owner = str(owner_username or "").strip()
        if not owner:
            return None
        repos = get_repository_registry()
        if explicit_profile_id:
            profile = repos.user_instruction_profile_repo.get_by_id(explicit_profile_id)
            if profile and str(profile.owner_username) == owner and bool(profile.is_active):
                return profile
            return None
        return repos.user_instruction_profile_repo.get_active_for_owner(owner)

    def resolve_overlay_for_context(
        self,
        *,
        owner_username: str,
        explicit_overlay_id: str | None = None,
        task_id: str | None = None,
        goal_id: str | None = None,
        session_id: str | None = None,
        usage_key: str | None = None,
        now_ts: float | None = None,
    ) -> InstructionOverlayDB | None:
        owner = str(owner_username or "").strip()
        if not owner:
            return None
        repos = get_repository_registry()
        now = float(now_ts or time.time())
        if explicit_overlay_id:
            explicit = repos.instruction_overlay_repo.get_by_id(explicit_overlay_id)
            if not explicit:
                return None
            if str(explicit.owner_username or "").strip() != owner:
                return None
            if not bool(explicit.is_active):
                return None
            if explicit.expires_at is not None and float(explicit.expires_at) <= now:
                return None
            return explicit

        overlays = repos.instruction_overlay_repo.list_by_owner(
            owner,
            include_inactive=False,
            include_expired=False,
            now_ts=now,
        )
        if not overlays:
            return None
        by_attachment: dict[tuple[str | None, str | None], list[InstructionOverlayDB]] = {}
        for overlay in overlays:
            key = (
                str(overlay.attachment_kind or "").strip() or None,
                str(overlay.attachment_id or "").strip() or None,
            )
            by_attachment.setdefault(key, []).append(overlay)

        candidate_keys = [
            ("task", str(task_id or "").strip() or None),
            ("goal", str(goal_id or "").strip() or None),
            ("session", str(session_id or "").strip() or None),
            ("usage", str(usage_key or "").strip() or None),
            (None, None),
        ]
        for key in candidate_keys:
            if key[1] is None and key[0] is not None:
                continue
            candidates = by_attachment.get(key) or []
            if candidates:
                return candidates[0]
        return None

    def evaluate_selection_compatibility(
        self,
        *,
        task: dict | TaskDB | None,
        owner_username: str | None,
        profile_id: str | None,
        overlay_id: str | None,
    ) -> dict[str, Any]:
        task_payload = task.model_dump() if isinstance(task, TaskDB) else dict(task or {})
        profile, overlay = self._resolve_selection_entities(
            owner_username=owner_username,
            profile_id=profile_id,
            overlay_id=overlay_id,
        )
        compatibility = self._evaluate_role_template_compatibility(
            task_payload=task_payload,
            profile=profile,
            overlay=overlay,
        )
        compatibility["selected_profile"] = self._profile_summary(profile)
        compatibility["selected_overlay"] = self._overlay_summary(overlay)
        return compatibility

    def task_selection_summary(self, task_payload: dict | None) -> dict[str, Any]:
        task = dict(task_payload or {})
        context = dict((task.get("worker_execution_context") or {}).get("instruction_context") or {})
        owner_username = str(context.get("owner_username") or "").strip() or None
        profile_id = str(context.get("profile_id") or "").strip() or None
        overlay_id = str(context.get("overlay_id") or "").strip() or None
        profile, overlay = self._resolve_selection_entities(
            owner_username=owner_username,
            profile_id=profile_id,
            overlay_id=overlay_id,
        )
        compatibility = self._evaluate_role_template_compatibility(
            task_payload=task,
            profile=profile,
            overlay=overlay,
        )
        return {
            "owner_username": owner_username,
            "profile_id": profile_id,
            "overlay_id": overlay_id,
            "attachment_kind": (
                str((overlay.attachment_kind if overlay else "") or "").strip() or ("task" if overlay_id else None)
            ),
            "attachment_id": (
                str((overlay.attachment_id if overlay else "") or "").strip() or str(task.get("id") or "").strip() or None
                if overlay_id
                else None
            ),
            "selected_profile": self._profile_summary(profile),
            "selected_overlay": self._overlay_summary(overlay),
            "template_compatibility": compatibility,
        }

    def goal_selection_summary(self, goal_payload: GoalDB | dict | None) -> dict[str, Any]:
        if isinstance(goal_payload, GoalDB):
            data = goal_payload.model_dump()
        else:
            data = dict(goal_payload or {})
        execution_preferences = dict(data.get("execution_preferences") or {})
        context = dict(execution_preferences.get("instruction_context") or {})
        owner_username = str(context.get("owner_username") or data.get("requested_by") or "").strip() or None
        profile_id = str(context.get("profile_id") or "").strip() or None
        overlay_id = str(context.get("overlay_id") or "").strip() or None
        profile, overlay = self._resolve_selection_entities(
            owner_username=owner_username,
            profile_id=profile_id,
            overlay_id=overlay_id,
        )
        compatibility = self._evaluate_role_template_compatibility(
            task_payload={},
            profile=profile,
            overlay=overlay,
        )
        return {
            "owner_username": owner_username,
            "profile_id": profile_id,
            "overlay_id": overlay_id,
            "attachment_kind": (
                str((overlay.attachment_kind if overlay else "") or "").strip() or ("goal" if overlay_id else None)
            ),
            "attachment_id": (
                str((overlay.attachment_id if overlay else "") or "").strip() or str(data.get("id") or "").strip() or None
                if overlay_id
                else None
            ),
            "selected_profile": self._profile_summary(profile),
            "selected_overlay": self._overlay_summary(overlay),
            "template_compatibility": compatibility,
        }

    def set_task_selection(
        self,
        *,
        task_id: str,
        owner_username: str,
        profile_id: str | None,
        overlay_id: str | None,
        actor: str,
    ) -> dict[str, Any]:
        repos = get_repository_registry()
        task = repos.task_repo.get_by_id(task_id)
        if task is None:
            raise ValueError("task_not_found")
        owner = str(owner_username or "").strip()
        if not owner:
            raise ValueError("owner_username_required")
        if profile_id:
            profile = repos.user_instruction_profile_repo.get_by_id(profile_id)
            if profile is None:
                raise ValueError("profile_not_found")
            if str(profile.owner_username or "").strip() != owner:
                raise ValueError("profile_owner_mismatch")
        if overlay_id:
            overlay = repos.instruction_overlay_repo.get_by_id(overlay_id)
            if overlay is None:
                raise ValueError("overlay_not_found")
            if str(overlay.owner_username or "").strip() != owner:
                raise ValueError("overlay_owner_mismatch")

        worker_execution_context = dict(task.worker_execution_context or {})
        instruction_context = dict(worker_execution_context.get("instruction_context") or {})
        instruction_context.update(
            {
                "owner_username": owner,
                "profile_id": str(profile_id or "").strip() or None,
                "overlay_id": str(overlay_id or "").strip() or None,
                "updated_at": time.time(),
            }
        )
        worker_execution_context["instruction_context"] = instruction_context
        task.worker_execution_context = worker_execution_context
        task.updated_at = time.time()
        repos.task_repo.save(task)
        log_audit(
            "task_instruction_selection_updated",
            {
                "task_id": task.id,
                "owner_username": owner,
                "profile_id": instruction_context.get("profile_id"),
                "overlay_id": instruction_context.get("overlay_id"),
                "actor": actor,
            },
        )
        return self.task_selection_summary(task.model_dump())

    def set_goal_selection(
        self,
        *,
        goal_id: str,
        owner_username: str,
        profile_id: str | None,
        overlay_id: str | None,
        actor: str,
    ) -> dict[str, Any]:
        repos = get_repository_registry()
        goal = repos.goal_repo.get_by_id(goal_id)
        if goal is None:
            raise ValueError("goal_not_found")
        owner = str(owner_username or "").strip()
        if not owner:
            raise ValueError("owner_username_required")
        if profile_id:
            profile = repos.user_instruction_profile_repo.get_by_id(profile_id)
            if profile is None:
                raise ValueError("profile_not_found")
            if str(profile.owner_username or "").strip() != owner:
                raise ValueError("profile_owner_mismatch")
        if overlay_id:
            overlay = repos.instruction_overlay_repo.get_by_id(overlay_id)
            if overlay is None:
                raise ValueError("overlay_not_found")
            if str(overlay.owner_username or "").strip() != owner:
                raise ValueError("overlay_owner_mismatch")

        execution_preferences = dict(goal.execution_preferences or {})
        instruction_context = dict(execution_preferences.get("instruction_context") or {})
        instruction_context.update(
            {
                "owner_username": owner,
                "profile_id": str(profile_id or "").strip() or None,
                "overlay_id": str(overlay_id or "").strip() or None,
                "updated_at": time.time(),
            }
        )
        execution_preferences["instruction_context"] = instruction_context
        goal.execution_preferences = execution_preferences
        goal.updated_at = time.time()
        repos.goal_repo.save(goal)
        log_audit(
            "goal_instruction_selection_updated",
            {
                "goal_id": goal.id,
                "owner_username": owner,
                "profile_id": instruction_context.get("profile_id"),
                "overlay_id": instruction_context.get("overlay_id"),
                "actor": actor,
            },
        )
        return self.goal_selection_summary(goal)


instruction_layer_service = InstructionLayerService()


def get_instruction_layer_service() -> InstructionLayerService:
    return instruction_layer_service
