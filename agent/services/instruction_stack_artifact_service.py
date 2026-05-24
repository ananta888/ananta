from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

_SCHEMA = "instruction_stack.v1"
_LAYER_ORDER = ["governance", "blueprint_template", "user_profile", "task_overlay", "task_input"]


@dataclass(frozen=True)
class InstructionStackArtifact:
    schema: str
    task_id: str | None
    goal_id: str | None
    role_template_context: dict[str, Any]
    applied_layers: list[dict[str, Any]]
    suppressed_layers: list[dict[str, Any]]
    rendered_system_prompt: str | None
    diagnostics: dict[str, Any]
    checksum: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "role_template_context": dict(self.role_template_context or {}),
            "applied_layers": [dict(item or {}) for item in list(self.applied_layers or [])],
            "suppressed_layers": [dict(item or {}) for item in list(self.suppressed_layers or [])],
            "rendered_system_prompt": self.rendered_system_prompt,
            "diagnostics": dict(self.diagnostics or {}),
            "checksum": self.checksum,
        }


class InstructionStackArtifactService:
    """Builds deterministic, checksum-based instruction stack artifacts."""

    def build_artifact(
        self,
        *,
        task_id: str | None,
        goal_id: str | None,
        role_template_context: dict[str, Any] | None,
        applied_layers: list[dict[str, Any]] | None,
        suppressed_layers: list[dict[str, Any]] | None,
        rendered_system_prompt: str | None,
        diagnostics: dict[str, Any] | None,
    ) -> InstructionStackArtifact:
        normalized_applied = self._normalize_layers(applied_layers)
        normalized_suppressed = self._normalize_layers(suppressed_layers)
        normalized_role_context = dict(role_template_context or {})
        normalized_prompt = str(rendered_system_prompt or "").strip() or None
        normalized_diagnostics = dict(diagnostics or {})
        checksum = self._checksum(
            {
                "schema": _SCHEMA,
                "task_id": str(task_id or "").strip() or None,
                "goal_id": str(goal_id or "").strip() or None,
                "role_template_context": normalized_role_context,
                "applied_layers": normalized_applied,
                "suppressed_layers": normalized_suppressed,
                "rendered_system_prompt": normalized_prompt,
                "diagnostics": normalized_diagnostics,
            }
        )
        return InstructionStackArtifact(
            schema=_SCHEMA,
            task_id=str(task_id or "").strip() or None,
            goal_id=str(goal_id or "").strip() or None,
            role_template_context=normalized_role_context,
            applied_layers=normalized_applied,
            suppressed_layers=normalized_suppressed,
            rendered_system_prompt=normalized_prompt,
            diagnostics=normalized_diagnostics,
            checksum=checksum,
        )

    @staticmethod
    def _checksum(payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_layers(value: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in list(value or []):
            layer = dict(item or {})
            layer_name = str(layer.get("layer") or "").strip().lower()
            if layer_name:
                layer["layer"] = layer_name
            normalized.append(layer)
        rank = {name: idx for idx, name in enumerate(_LAYER_ORDER)}
        return sorted(normalized, key=lambda item: rank.get(str(item.get("layer") or ""), len(rank) + 1))


_instruction_stack_artifact_service = InstructionStackArtifactService()


def get_instruction_stack_artifact_service() -> InstructionStackArtifactService:
    return _instruction_stack_artifact_service

