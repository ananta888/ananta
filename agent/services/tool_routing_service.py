from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.runtime_policy import normalize_task_kind
from agent.services.integration_registry_service import get_integration_registry_service

_BACKEND_CAPABILITY_CLASSES: dict[str, list[str]] = {
    "sgpt": ["planning", "review"],
    "codex": ["patching", "review"],
    "opencode": ["patching", "shell_execution", "planning"],
    "aider": ["patching", "shell_execution"],
    "mistral_code": ["patching", "review"],
    "deerflow": ["research", "planning"],
    "ananta_research": ["research", "planning"],
}

_TOOL_CLASS_CAPABILITY_CLASSES: dict[str, list[str]] = {
    "read": ["review"],
    "write": ["patching"],
    "admin": ["admin_repair", "shell_execution"],
    "unknown": ["planning"],
}

_TASK_KIND_CAPABILITY_HINTS: dict[str, list[str]] = {
    "coding": ["patching"],
    "analysis": ["review"],
    "research": ["research"],
    "doc": ["planning"],
    "ops": ["shell_execution"],
}

_RISK_CLASS_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class ToolRouterDecision:
    selected_target: str | None
    selected_reason: str
    alternatives: list[dict[str, Any]]
    policy_checks: list[dict[str, Any]]
    required_capabilities: list[str]
    governance_mode: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected_target": self.selected_target,
            "selected_reason": self.selected_reason,
            "alternatives": list(self.alternatives),
            "policy_checks": list(self.policy_checks),
            "required_capabilities": list(self.required_capabilities),
            "governance_mode": self.governance_mode,
            "policy_version": "tool-router-v1",
        }


class ToolRoutingService:
    """Reusable hub-side tool/backend routing and capability normalization."""

    def build_capability_catalog(self, *, agent_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        cfg = dict(agent_cfg or {})
        backends = get_integration_registry_service().list_execution_backends(include_preflight=True)
        backend_caps = dict(backends.get("capabilities") or {})
        tool_classes = self._normalize_tool_class_map(((cfg.get("llm_tool_guardrails") or {}).get("tool_classes")))
        stateful_backends = {str(item or "").strip().lower() for item in list(((cfg.get("cli_session_mode") or {}).get("stateful_backends") or []))}

        items: list[dict[str, Any]] = []
        for backend, payload in sorted(backend_caps.items(), key=lambda item: str(item[0])):
            backend_name = str(backend or "").strip().lower()
            capability_classes = sorted(set(_BACKEND_CAPABILITY_CLASSES.get(backend_name, ["planning"])))
            risk_class = self._risk_class_for_capability_classes(capability_classes)
            items.append(
                {
                    "id": backend_name,
                    "kind": "backend",
                    "capability_classes": capability_classes,
                    "risk_class": risk_class,
                    "supports_stateful_session": backend_name in stateful_backends,
                    "requires_approval": risk_class in {"medium", "high"},
                    "availability": "ready" if bool((payload or {}).get("available")) else "unavailable",
                }
            )

        for tool_name, tool_class in sorted(tool_classes.items(), key=lambda item: item[0]):
            capability_classes = sorted(set(_TOOL_CLASS_CAPABILITY_CLASSES.get(tool_class, ["planning"])))
            risk_class = "high" if tool_class == "admin" else ("medium" if tool_class == "write" else "low")
            items.append(
                {
                    "id": tool_name,
                    "kind": "tool",
                    "capability_classes": capability_classes,
                    "risk_class": risk_class,
                    "supports_stateful_session": False,
                    "requires_approval": tool_class in {"write", "admin"},
                    "availability": "ready",
                    "tool_class": tool_class,
                }
            )

        summary = {
            "total_items": len(items),
            "backend_count": len([item for item in items if item.get("kind") == "backend"]),
            "tool_count": len([item for item in items if item.get("kind") == "tool"]),
            "capability_classes": sorted({cap for item in items for cap in (item.get("capability_classes") or [])}),
        }
        return {
            "catalog_version": "tool-router-v1",
            "items": items,
            "summary": summary,
        }

    def route_execution_backend(
        self,
        *,
        task_kind: str | None,
        requested_backend: str | None,
        required_capabilities: list[str] | None,
        governance_mode: str | None,
        agent_cfg: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        catalog = self.build_capability_catalog(agent_cfg=agent_cfg)
        normalized_kind = normalize_task_kind(task_kind, "")
        governance = str(governance_mode or "balanced").strip().lower()
        if governance not in {"safe", "balanced", "strict"}:
            governance = "balanced"
        required = self._required_capabilities(normalized_kind, required_capabilities)
        backend_items = [item for item in list(catalog.get("items") or []) if item.get("kind") == "backend"]

        requested = str(requested_backend or "").strip().lower() or None
        alternatives: list[dict[str, Any]] = []
        best_candidate: tuple[dict, int, int] | None = None
        for item in backend_items:
            candidate_id = str(item.get("id") or "").strip()
            candidate_caps = {str(cap or "").strip().lower() for cap in list(item.get("capability_classes") or [])}
            missing = [cap for cap in required if cap not in candidate_caps]
            available = str(item.get("availability") or "unavailable") == "ready"
            governance_block = self._is_governance_blocked(governance_mode=governance, risk_class=str(item.get("risk_class") or "low"))
            selected = False
            reason = "candidate_available"
            if not available:
                reason = "backend_unavailable"
            elif governance_block:
                reason = "governance_risk_blocked"
            elif missing:
                reason = f"missing_capabilities:{','.join(missing)}"
            elif requested and requested != candidate_id:
                reason = "requested_backend_preference"
            else:
                selected = True
                reason = "best_matching_candidate"

            alternatives.append(
                {
                    "target": candidate_id,
                    "selected": selected,
                    "reason": reason,
                    "missing_capabilities": missing,
                    "risk_class": item.get("risk_class"),
                    "availability": item.get("availability"),
                }
            )
            if not selected:
                continue
            score = len([cap for cap in required if cap in candidate_caps])
            risk_rank = _RISK_CLASS_RANK.get(str(item.get("risk_class") or "low"), 3)
            if best_candidate is None or score > best_candidate[1] or (score == best_candidate[1] and risk_rank < best_candidate[2]):
                best_candidate = (item, score, risk_rank)

        selected_target = str(best_candidate[0].get("id")) if best_candidate else None
        selected_reason = "no_eligible_backend"
        if selected_target:
            selected_reason = "requested_backend_selected" if requested and requested == selected_target else "capability_match_selected"

        policy_checks = [
            {"rule_id": "required_capabilities_present", "result": bool(required), "reason": ",".join(required) if required else "no_explicit_required_capabilities"},
            {"rule_id": "governance_mode", "result": True, "reason": governance},
            {"rule_id": "requested_backend", "result": bool(requested), "reason": requested or "not_requested"},
        ]
        decision = ToolRouterDecision(
            selected_target=selected_target,
            selected_reason=selected_reason,
            alternatives=alternatives,
            policy_checks=policy_checks,
            required_capabilities=required,
            governance_mode=governance,
        )
        return {
            "catalog_version": str(catalog.get("catalog_version") or "tool-router-v1"),
            "catalog_summary": dict(catalog.get("summary") or {}),
            "decision": decision.as_dict(),
        }

    @staticmethod
    def _normalize_tool_class_map(raw: Any) -> dict[str, str]:
        payload = raw if isinstance(raw, dict) else {}
        normalized: dict[str, str] = {}
        for key, value in payload.items():
            name = str(key or "").strip()
            if not name:
                continue
            tool_class = str(value or "unknown").strip().lower()
            if tool_class not in {"read", "write", "admin", "unknown"}:
                tool_class = "unknown"
            normalized[name] = tool_class
        return normalized

    @staticmethod
    def _risk_class_for_capability_classes(capability_classes: list[str]) -> str:
        classes = set(str(item or "").strip().lower() for item in capability_classes)
        if "admin_repair" in classes or "shell_execution" in classes:
            return "high"
        if "patching" in classes:
            return "medium"
        return "low"

    @staticmethod
    def _required_capabilities(task_kind: str, required_capabilities: list[str] | None) -> list[str]:
        requested = [str(item or "").strip().lower() for item in list(required_capabilities or []) if str(item or "").strip()]
        derived = list(_TASK_KIND_CAPABILITY_HINTS.get(task_kind, []))
        merged: list[str] = []
        for item in [*derived, *requested]:
            if item not in merged:
                merged.append(item)
        return merged

    @staticmethod
    def _is_governance_blocked(*, governance_mode: str, risk_class: str) -> bool:
        risk = str(risk_class or "low").strip().lower()
        if governance_mode == "balanced":
            return False
        if governance_mode == "safe":
            return risk == "high"
        if governance_mode == "strict":
            return risk in {"medium", "high"}
        return False


tool_routing_service = ToolRoutingService()


def get_tool_routing_service() -> ToolRoutingService:
    return tool_routing_service
