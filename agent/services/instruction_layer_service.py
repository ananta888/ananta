from __future__ import annotations

import copy
import re
import time
from typing import Any

from agent.common.audit import log_audit
from agent.db_models import GoalDB, InstructionOverlayDB, TaskDB, UserInstructionProfileDB
from agent.services.repository_registry import get_repository_registry

_LAYER_MODEL_VERSION = "instruction-layer-model-v1"
_STACK_VERSION = "instruction-stack-v1"
_PRECEDENCE = ["governance", "blueprint_template", "user_profile", "task_overlay", "task_input"]
_ALLOWED_USER_INFLUENCE = {"style", "language", "detail_level", "working_mode", "formatting"}
_FORBIDDEN_METADATA_KEYS = {
    "approval",
    "approval_required",
    "approval_policy",
    "governance_mode",
    "security_policy",
    "execution_risk_policy",
    "allowed_tools",
    "write_access",
    "runtime_execution",
}
_OVERLAY_ATTACHMENT_KINDS = {"goal", "task", "session", "usage"}
_FORBIDDEN_DIRECTIVE_PATTERNS = [
    re.compile(
        r"(ignore|bypass|override|disable|skip)\s+(all\s+)?(approval|governance|policy|security|guardrail)",
        re.IGNORECASE,
    ),
    re.compile(r"(disable|remove|ignore)\s+(safety|guardrails?|restrictions?)", re.IGNORECASE),
    re.compile(r"(grant|allow)\s+(unrestricted|full)\s+(shell|command|filesystem)\s+access", re.IGNORECASE),
]
_PROFILE_EXAMPLES = [
    {
        "id": "concise-coding",
        "name": "Concise Coding",
        "description": "High-signal implementation answers with short rationale.",
        "prompt_content": (
            "Prioritize direct implementation steps, keep explanations concise, "
            "and include only essential rationale."
        ),
        "profile_metadata": {"preferences": {"style": "concise", "detail_level": "high"}},
        "safety_notes": [
            "Does not alter governance, approval or security policy.",
            "Focuses only on style and level of detail.",
        ],
    },
    {
        "id": "research-helper",
        "name": "Research Helper",
        "description": "Structured exploration with explicit assumptions and sources.",
        "prompt_content": (
            "Use a research-first mode: state assumptions explicitly, compare options briefly, "
            "and summarize trade-offs clearly."
        ),
        "profile_metadata": {"preferences": {"working_mode": "research", "detail_level": "high"}},
        "safety_notes": [
            "No permission escalation directives.",
            "Keeps policy boundaries unchanged.",
        ],
    },
    {
        "id": "review-first",
        "name": "Review First",
        "description": "Risk-focused review style before proposing edits.",
        "prompt_content": (
            "Start with a focused review of risks and edge cases, then propose the minimal safe change set."
        ),
        "profile_metadata": {"preferences": {"working_mode": "review", "style": "concise"}},
        "safety_notes": [
            "No bypass of approval workflows.",
            "Keeps security and governance constraints dominant.",
        ],
    },
]


class InstructionLayerService:
    """Resolves, validates and assembles user profile + overlay instruction layers."""

    def layer_model(self) -> dict[str, Any]:
        return {
            "version": _LAYER_MODEL_VERSION,
            "layers": [
                {"id": "governance", "source": "hub_policy", "overridable": False},
                {"id": "blueprint_template", "source": "team_role_template", "overridable": False},
                {"id": "user_profile", "source": "persistent_profile", "overridable": True},
                {"id": "task_overlay", "source": "task_goal_session_overlay", "overridable": True},
                {"id": "task_input", "source": "current_user_request", "overridable": True},
            ],
            "precedence": list(_PRECEDENCE),
            "merge_strategy": {
                "kind": "structured_preference_then_section_concat",
                "preferences_conflict_resolution": "higher_precedence_wins",
                "overlay_vs_profile": "task_overlay_overrides_user_profile",
                "rendering": "ordered_sections",
            },
            "allowed_user_influence_scope": sorted(_ALLOWED_USER_INFLUENCE),
            "forbidden_user_influence_scope": sorted(_FORBIDDEN_METADATA_KEYS),
            "supported_overlay_attachment_kinds": sorted(_OVERLAY_ATTACHMENT_KINDS),
            "first_release_attachment_subset": ["task", "goal", "session"],
            "safe_defaults": {
                "when_no_profile": "only_governance_and_template_and_task_input",
                "when_no_overlay": "profile_or_template_without_overlay",
            },
            "terminology": {
                "persistent_layer": "user_profile",
                "scoped_layer": "task_overlay",
                "combined_output": "instruction_stack",
            },
        }

    def profile_examples(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(item) for item in _PROFILE_EXAMPLES]

    def validate_user_layer_payload(self, *, prompt_content: str, metadata: dict | None = None) -> dict[str, Any]:
        text = str(prompt_content or "").strip()
        meta = dict(metadata or {})
        blocked_patterns = [
            pattern.pattern for pattern in _FORBIDDEN_DIRECTIVE_PATTERNS if text and pattern.search(text)
        ]
        blocked_keys = self._find_forbidden_metadata_keys(meta)
        ok = not blocked_patterns and not blocked_keys
        return {
            "ok": ok,
            "blocked_reason": None if ok else "forbidden_instruction_scope",
            "forbidden_directives": blocked_patterns,
            "forbidden_metadata_keys": blocked_keys,
            "allowed_user_influence_scope": sorted(_ALLOWED_USER_INFLUENCE),
            "forbidden_user_influence_scope": sorted(_FORBIDDEN_METADATA_KEYS),
        }

    def _find_forbidden_metadata_keys(self, metadata: dict) -> list[str]:
        blocked: set[str] = set()

        def _walk(prefix: str, value: Any) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    key_text = str(key or "").strip()
                    path = f"{prefix}.{key_text}" if prefix else key_text
                    normalized = key_text.lower()
                    if normalized in _FORBIDDEN_METADATA_KEYS:
                        blocked.add(path)
                    _walk(path, nested)

        _walk("", metadata)
        return sorted(blocked)

    def normalize_profile_metadata(self, metadata: dict | None) -> dict[str, Any]:
        meta = dict(metadata or {})
        preferences = meta.get("preferences") if isinstance(meta.get("preferences"), dict) else {}
        normalized_preferences = {
            str(key).strip(): value
            for key, value in dict(preferences).items()
            if str(key).strip() and str(key).strip().lower() in _ALLOWED_USER_INFLUENCE
        }
        if normalized_preferences:
            meta["preferences"] = normalized_preferences
        else:
            meta.pop("preferences", None)
        return meta

    def normalize_overlay_metadata(self, metadata: dict | None) -> dict[str, Any]:
        return self.normalize_profile_metadata(metadata)

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

    def task_selection_summary(self, task_payload: dict | None) -> dict[str, Any]:
        task = dict(task_payload or {})
        context = dict((task.get("worker_execution_context") or {}).get("instruction_context") or {})
        return {
            "owner_username": str(context.get("owner_username") or "").strip() or None,
            "profile_id": str(context.get("profile_id") or "").strip() or None,
            "overlay_id": str(context.get("overlay_id") or "").strip() or None,
            "attachment_kind": "task" if str(context.get("overlay_id") or "").strip() else None,
            "attachment_id": str(task.get("id") or "").strip() or None,
        }

    def goal_selection_summary(self, goal_payload: GoalDB | dict | None) -> dict[str, Any]:
        if isinstance(goal_payload, GoalDB):
            data = goal_payload.model_dump()
        else:
            data = dict(goal_payload or {})
        execution_preferences = dict(data.get("execution_preferences") or {})
        context = dict(execution_preferences.get("instruction_context") or {})
        return {
            "owner_username": str(context.get("owner_username") or data.get("requested_by") or "").strip() or None,
            "profile_id": str(context.get("profile_id") or "").strip() or None,
            "overlay_id": str(context.get("overlay_id") or "").strip() or None,
            "attachment_kind": "goal" if str(context.get("overlay_id") or "").strip() else None,
            "attachment_id": str(data.get("id") or "").strip() or None,
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

    def assemble_for_task(
        self,
        *,
        task: dict | TaskDB | None,
        base_prompt: str,
        system_prompt: str | None,
        session_id: str | None = None,
        usage_key: str | None = None,
        emit_audit: bool = False,
    ) -> dict[str, Any]:
        task_payload = task.model_dump() if isinstance(task, TaskDB) else dict(task or {})
        goal_payload = self._load_goal_for_task(task_payload)
        owner_username = self._resolve_owner_username(task_payload, goal_payload)
        task_context = dict((task_payload.get("worker_execution_context") or {}).get("instruction_context") or {})
        goal_context = dict((goal_payload or {}).get("execution_preferences", {}).get("instruction_context", {}) or {})
        explicit_profile_id = str(
            task_context.get("profile_id")
            or goal_context.get("profile_id")
            or ""
        ).strip() or None
        explicit_overlay_id = str(
            task_context.get("overlay_id")
            or goal_context.get("overlay_id")
            or ""
        ).strip() or None
        profile = (
            self.resolve_profile_for_owner(owner_username=owner_username, explicit_profile_id=explicit_profile_id)
            if owner_username
            else None
        )
        overlay = (
            self.resolve_overlay_for_context(
                owner_username=owner_username,
                explicit_overlay_id=explicit_overlay_id,
                task_id=str(task_payload.get("id") or "").strip() or None,
                goal_id=str(task_payload.get("goal_id") or "").strip() or None,
                session_id=session_id,
                usage_key=usage_key,
            )
            if owner_username
            else None
        )
        profile_validation = (
            self.validate_user_layer_payload(
                prompt_content=str(profile.prompt_content or ""),
                metadata=dict(profile.profile_metadata or {}),
            )
            if profile
            else {"ok": True, "forbidden_directives": [], "forbidden_metadata_keys": []}
        )
        overlay_validation = (
            self.validate_user_layer_payload(
                prompt_content=str(overlay.prompt_content or ""),
                metadata=dict(overlay.overlay_metadata or {}),
            )
            if overlay
            else {"ok": True, "forbidden_directives": [], "forbidden_metadata_keys": []}
        )

        applied_layers: list[dict[str, Any]] = []
        suppressed_layers: list[dict[str, Any]] = []
        effective_preferences: dict[str, Any] = {}

        if system_prompt:
            applied_layers.append({"layer": "blueprint_template", "source": "task_role_template"})
        if profile:
            if profile_validation.get("ok"):
                applied_layers.append(
                    {
                        "layer": "user_profile",
                        "source": "persistent_profile",
                        "profile_id": profile.id,
                        "name": profile.name,
                    }
                )
                effective_preferences.update(dict((profile.profile_metadata or {}).get("preferences") or {}))
            else:
                suppressed_layers.append(
                    {
                        "layer": "user_profile",
                        "profile_id": profile.id,
                        "reason": "forbidden_instruction_scope",
                        "forbidden_directives": profile_validation.get("forbidden_directives") or [],
                        "forbidden_metadata_keys": profile_validation.get("forbidden_metadata_keys") or [],
                    }
                )
        if overlay:
            if overlay_validation.get("ok"):
                applied_layers.append(
                    {
                        "layer": "task_overlay",
                        "source": "overlay",
                        "overlay_id": overlay.id,
                        "name": overlay.name,
                        "attachment_kind": overlay.attachment_kind,
                        "attachment_id": overlay.attachment_id,
                    }
                )
                effective_preferences.update(dict((overlay.overlay_metadata or {}).get("preferences") or {}))
            else:
                suppressed_layers.append(
                    {
                        "layer": "task_overlay",
                        "overlay_id": overlay.id,
                        "reason": "forbidden_instruction_scope",
                        "forbidden_directives": overlay_validation.get("forbidden_directives") or [],
                        "forbidden_metadata_keys": overlay_validation.get("forbidden_metadata_keys") or [],
                    }
                )

        rendered_sections: list[str] = []
        if system_prompt:
            rendered_sections.append(system_prompt)
        if profile and profile_validation.get("ok"):
            rendered_sections.append(f"[USER PROFILE]\n{str(profile.prompt_content or '').strip()}")
        if overlay and overlay_validation.get("ok"):
            rendered_sections.append(f"[TASK OVERLAY]\n{str(overlay.prompt_content or '').strip()}")
        rendered_system_prompt = "\n\n".join(section for section in rendered_sections if section).strip() or None

        diagnostics = {
            "version": _STACK_VERSION,
            "precedence": list(_PRECEDENCE),
            "applied_layers": applied_layers,
            "suppressed_layers": suppressed_layers,
            "effective_preferences": effective_preferences,
            "selected_profile": self._profile_summary(profile) if profile else None,
            "selected_overlay": self._overlay_summary(overlay) if overlay else None,
            "owner_username": owner_username or None,
            "task_id": str(task_payload.get("id") or "").strip() or None,
            "goal_id": str(task_payload.get("goal_id") or "").strip() or None,
        }
        if emit_audit and (profile or overlay):
            log_audit(
                "instruction_layers_applied",
                {
                    "task_id": diagnostics["task_id"],
                    "goal_id": diagnostics["goal_id"],
                    "owner_username": diagnostics["owner_username"],
                    "profile_id": (diagnostics.get("selected_profile") or {}).get("id"),
                    "overlay_id": (diagnostics.get("selected_overlay") or {}).get("id"),
                },
            )

        return {
            "rendered_system_prompt": rendered_system_prompt,
            "diagnostics": diagnostics,
            "selection": {
                "owner_username": owner_username or None,
                "profile_id": (diagnostics.get("selected_profile") or {}).get("id"),
                "overlay_id": (diagnostics.get("selected_overlay") or {}).get("id"),
            },
        }

    def render_diagnostics_brief(self, diagnostics: dict | None) -> str:
        payload = dict(diagnostics or {})
        profile = dict(payload.get("selected_profile") or {})
        overlay = dict(payload.get("selected_overlay") or {})
        suppressed = list(payload.get("suppressed_layers") or [])
        lines = [
            "Instruction-Stack (high -> low): governance > blueprint_template > user_profile > task_overlay > task_input",
            f"Aktives Profil: {profile.get('name') or '-'}",
            f"Aktives Overlay: {overlay.get('name') or '-'}",
        ]
        if suppressed:
            lines.append("Unterdrueckte Layer: " + ", ".join(str(item.get("layer") or "unknown") for item in suppressed))
        return "\n".join(lines)

    def _resolve_owner_username(self, task_payload: dict, goal_payload: dict | None) -> str:
        task_context = dict((task_payload.get("worker_execution_context") or {}).get("instruction_context") or {})
        goal_context = dict((goal_payload or {}).get("execution_preferences", {}).get("instruction_context", {}) or {})
        return (
            str(task_context.get("owner_username") or "").strip()
            or str(goal_context.get("owner_username") or "").strip()
            or str((goal_payload or {}).get("requested_by") or "").strip()
        )

    def _load_goal_for_task(self, task_payload: dict) -> dict | None:
        goal_id = str(task_payload.get("goal_id") or "").strip()
        if not goal_id:
            return None
        goal = get_repository_registry().goal_repo.get_by_id(goal_id)
        return goal.model_dump() if goal else None

    @staticmethod
    def _profile_summary(profile: UserInstructionProfileDB | None) -> dict[str, Any] | None:
        if profile is None:
            return None
        return {
            "id": profile.id,
            "name": profile.name,
            "owner_username": profile.owner_username,
            "is_default": bool(profile.is_default),
            "is_active": bool(profile.is_active),
        }

    @staticmethod
    def _overlay_summary(overlay: InstructionOverlayDB | None) -> dict[str, Any] | None:
        if overlay is None:
            return None
        return {
            "id": overlay.id,
            "name": overlay.name,
            "owner_username": overlay.owner_username,
            "scope": overlay.scope,
            "attachment_kind": overlay.attachment_kind,
            "attachment_id": overlay.attachment_id,
            "is_active": bool(overlay.is_active),
            "expires_at": overlay.expires_at,
        }


instruction_layer_service = InstructionLayerService()


def get_instruction_layer_service() -> InstructionLayerService:
    return instruction_layer_service
