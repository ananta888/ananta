from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any


_ALLOWED_TASK_KINDS = {"coding", "testing", "review", "research", "planning", "ops", "analysis", "doc"}
_ALLOWED_RISK_LEVELS = {"low", "medium", "high", "critical"}
_SOFTWARE_HINTS = ("software", "project", "java", "angular", "frontend", "backend", "api", "service", "app")
_ALLOWED_OUTPUT_FORMATS = {"json", "markdown", "yaml"}


def normalize_planning_policy_config(value: dict | None) -> dict[str, Any]:
    payload = dict(value or {})

    def _bounded_int(raw: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    roles: list[str] = []
    for item in list(payload.get("allowed_planner_roles") or []):
        role = str(item or "").strip().lower()
        if role and role not in roles:
            roles.append(role)
    if not roles:
        roles = ["planning-agent", "planner"]

    raw_runtime_profiles = payload.get("runtime_profiles") if isinstance(payload.get("runtime_profiles"), dict) else {}
    runtime_profiles: dict[str, dict[str, Any]] = {}
    for profile_id, raw_profile in raw_runtime_profiles.items():
        if not isinstance(raw_profile, dict):
            continue
        runtime_profiles[str(profile_id)] = {
            "timeout_seconds": _bounded_int(raw_profile.get("timeout_seconds"), default=120, minimum=5, maximum=900),
            "max_output_tokens": _bounded_int(raw_profile.get("max_output_tokens"), default=512, minimum=128, maximum=2048),
            "retry_attempts": _bounded_int(raw_profile.get("retry_attempts"), default=2, minimum=1, maximum=5),
            "retry_backoff_seconds": float(raw_profile.get("retry_backoff_seconds") or 1.0),
            "segmented_planning_enabled": bool(raw_profile.get("segmented_planning_enabled", False)),
            "segment_context_chars": _bounded_int(raw_profile.get("segment_context_chars"), default=2400, minimum=600, maximum=12000),
            "max_segments": _bounded_int(raw_profile.get("max_segments"), default=3, minimum=1, maximum=8),
            "preferred_output_format": (
                str(raw_profile.get("preferred_output_format") or "json").strip().lower()
                if str(raw_profile.get("preferred_output_format") or "json").strip().lower() in _ALLOWED_OUTPUT_FORMATS
                else "json"
            ),
        }

    return {
        "delegated_planning_enabled": bool(payload.get("delegated_planning_enabled", False)),
        "allowed_planner_roles": roles,
        "require_review": bool(payload.get("require_review", True)),
        "allow_remote_planners": bool(payload.get("allow_remote_planners", False)),
        "max_nodes": _bounded_int(payload.get("max_nodes"), default=8, minimum=1, maximum=50),
        "max_depth": _bounded_int(payload.get("max_depth"), default=8, minimum=1, maximum=50),
        "timeout_seconds": _bounded_int(payload.get("timeout_seconds"), default=45, minimum=5, maximum=900),
        "max_output_tokens": _bounded_int(payload.get("max_output_tokens"), default=512, minimum=128, maximum=2048),
        "segmented_planning_enabled": bool(payload.get("segmented_planning_enabled", False)),
        "segment_context_chars": _bounded_int(payload.get("segment_context_chars"), default=2400, minimum=600, maximum=12000),
        "max_segments": _bounded_int(payload.get("max_segments"), default=3, minimum=1, maximum=8),
        "preferred_output_format": (
            str(payload.get("preferred_output_format") or "json").strip().lower()
            if str(payload.get("preferred_output_format") or "json").strip().lower() in _ALLOWED_OUTPUT_FORMATS
            else "json"
        ),
        "selective_repair_rounds": _bounded_int(payload.get("selective_repair_rounds"), default=2, minimum=0, maximum=4),
        "validation_profiles": dict(payload.get("validation_profiles") or {}) if isinstance(payload.get("validation_profiles"), dict) else {},
        "team_overrides": dict(payload.get("team_overrides") or {}) if isinstance(payload.get("team_overrides"), dict) else {},
        "planner_prompt_evolution": dict(payload.get("planner_prompt_evolution") or {}) if isinstance(payload.get("planner_prompt_evolution"), dict) else {},
        "default_runtime_profile": str(payload.get("default_runtime_profile") or "").strip() or None,
        "runtime_profiles": runtime_profiles,
    }


def select_planning_agent_candidate(*, agents: list[dict], planning_policy: dict[str, Any]) -> dict[str, Any] | None:
    allowed_roles = set(str(item or "").strip().lower() for item in list(planning_policy.get("allowed_planner_roles") or []))
    allow_remote = bool(planning_policy.get("allow_remote_planners", False))
    ranked: list[dict[str, Any]] = []
    for agent in agents:
        if str(agent.get("status") or "").lower() != "online":
            continue
        worker_roles = {str(item or "").strip().lower() for item in list(agent.get("worker_roles") or [])}
        capabilities = {str(item or "").strip().lower() for item in list(agent.get("capabilities") or [])}
        if not (worker_roles & allowed_roles):
            continue
        if "plan.propose" not in capabilities and "planning" not in capabilities:
            continue
        url = str(agent.get("url") or "")
        if not allow_remote and url and not (
            "localhost" in url or "127.0.0.1" in url or ".local" in url
        ):
            continue
        ranked.append(
            {
                "name": str(agent.get("name") or ""),
                "url": url,
                "worker_roles": sorted(worker_roles),
                "capabilities": sorted(capabilities),
                "routing_reason": "planning_role_and_capability_match",
            }
        )
    ranked.sort(key=lambda item: (item["url"], item["name"]))
    return ranked[0] if ranked else None


def build_plan_proposal(
    *,
    goal_id: str,
    trace_id: str,
    summary: str,
    subtasks: list[dict[str, Any]],
    required_capabilities: list[str] | None = None,
) -> dict[str, Any]:
    summary_text = str(summary or "").strip()
    is_software_project = any(token in summary_text.lower() for token in _SOFTWARE_HINTS)

    def _default_artifacts(*, index: int, task_kind: str, title: str) -> list[dict[str, Any]]:
        title_lower = title.lower()
        if is_software_project and task_kind == "coding":
            if "backend" in title_lower or "java" in title_lower:
                return [{"kind": "directory", "required": True, "relative_path": "backend", "description": "Java backend project"}]
            if "frontend" in title_lower or "angular" in title_lower:
                return [{"kind": "directory", "required": True, "relative_path": "frontend", "description": "Angular frontend project"}]
            return [{"kind": "workspace_change", "required": True, "description": f"software-step-{index}"}]
        if task_kind in {"coding", "testing", "ops"}:
            return [{"kind": "workspace_change", "required": True, "description": f"task-{index}-output"}]
        return []

    nodes: list[dict[str, Any]] = []
    node_keys: list[str] = []
    for index, subtask in enumerate(subtasks, start=1):
        node_key = f"N{index}"
        node_keys.append(node_key)
        risk_level = str(subtask.get("risk_level") or "medium").strip().lower() or "medium"
        if risk_level not in _ALLOWED_RISK_LEVELS:
            risk_level = "medium"
        task_kind = str(subtask.get("task_kind") or "coding").strip().lower() or "coding"
        if task_kind not in _ALLOWED_TASK_KINDS:
            task_kind = "coding"
        depends_on = [str(item).strip() for item in list(subtask.get("depends_on") or []) if str(item).strip()]
        # Accept legacy numeric depends_on and map to deterministic node keys.
        mapped_depends_on: list[str] = []
        for dep in depends_on:
            if dep.startswith("N"):
                mapped_depends_on.append(dep)
            elif dep.isdigit():
                dep_index = int(dep)
                if dep_index >= 1:
                    mapped_depends_on.append(f"N{dep_index}")
        title = str(subtask.get("title") or f"Step {index}")[:200]
        expected_artifacts = [dict(item) for item in list(subtask.get("expected_artifacts") or []) if isinstance(item, dict)]
        if not expected_artifacts:
            expected_artifacts = _default_artifacts(index=index, task_kind=task_kind, title=title)
        verification_spec = dict(subtask.get("verification_spec") or {})
        if expected_artifacts and not verification_spec.get("expected_artifacts"):
            verification_spec["expected_artifacts"] = expected_artifacts
        nodes.append(
            {
                "node_key": node_key,
                "title": title,
                "description": str(subtask.get("description") or subtask.get("title") or "")[:2000],
                "task_kind": task_kind,
                "depends_on": mapped_depends_on,
                "required_capabilities": [str(item).strip().lower() for item in list(subtask.get("required_capabilities") or []) if str(item).strip()],
                "risk_level": risk_level,
                "verification_spec": verification_spec,
                "expected_artifacts": expected_artifacts,
                "artifact_trace_id": f"A{index}",
                "suggested_worker_profile": str(subtask.get("suggested_worker_profile") or "planner").strip().lower() or "planner",
            }
        )
    return {
        "plan_proposal_contract_version": "v1",
        "goal_id": str(goal_id or "").strip(),
        "trace_id": str(trace_id or "").strip(),
        "summary": str(summary or "").strip(),
        "goal_contract_requirements": {
            "requires_artifact_expectations": bool(is_software_project),
            "goal_type": "software_project" if is_software_project else "generic",
            "software_project_blueprint": "new_software_project" if is_software_project else None,
        },
        "assumptions": [],
        "clarifying_questions": [],
        "nodes": nodes,
        "dependencies": [{"from": dep, "to": node["node_key"]} for node in nodes for dep in list(node.get("depends_on") or [])],
        "risks": [],
        "required_capabilities": sorted(
            {
                str(item).strip().lower()
                for item in (required_capabilities or [])
                if str(item).strip()
            }
        ),
        "acceptance_criteria": [],
        "estimated_complexity": "medium",
        "confidence": 0.7,
    }


@dataclass
class ProposalValidationResult:
    ok: bool
    errors: list[str]
    normalized_payload: dict[str, Any]


def validate_plan_proposal_payload(payload: dict[str, Any], *, known_capabilities: set[str] | None = None) -> ProposalValidationResult:
    known_caps = {str(item).strip().lower() for item in (known_capabilities or set()) if str(item).strip()}
    errors: list[str] = []
    normalized = dict(payload or {})
    contract_version = str(normalized.get("plan_proposal_contract_version") or "").strip()
    if contract_version != "v1":
        errors.append("invalid_contract_version")
    nodes = list(normalized.get("nodes") or [])
    if not nodes:
        errors.append("nodes_required")
        return ProposalValidationResult(ok=False, errors=errors, normalized_payload=normalized)

    seen: set[str] = set()
    dep_edges: list[tuple[str, str]] = []
    normalized_nodes: list[dict[str, Any]] = []
    requires_artifact_expectations = bool(((normalized.get("goal_contract_requirements") or {}).get("requires_artifact_expectations")))
    missing_artifact_nodes: list[str] = []
    for node in nodes:
        node_key = str((node or {}).get("node_key") or "").strip()
        if not node_key:
            errors.append("node_key_required")
            continue
        if node_key in seen:
            errors.append(f"duplicate_node_key:{node_key}")
        seen.add(node_key)
        task_kind = str((node or {}).get("task_kind") or "").strip().lower() or "coding"
        if task_kind not in _ALLOWED_TASK_KINDS:
            errors.append(f"invalid_task_kind:{node_key}")
            task_kind = "coding"
        risk_level = str((node or {}).get("risk_level") or "").strip().lower() or "medium"
        if risk_level not in _ALLOWED_RISK_LEVELS:
            errors.append(f"invalid_risk_level:{node_key}")
            risk_level = "medium"
        depends_on = [str(dep).strip() for dep in list((node or {}).get("depends_on") or []) if str(dep).strip()]
        for dep in depends_on:
            dep_edges.append((dep, node_key))
        required = [str(cap).strip().lower() for cap in list((node or {}).get("required_capabilities") or []) if str(cap).strip()]
        expected_artifacts = [dict(item) for item in list((node or {}).get("expected_artifacts") or []) if isinstance(item, dict)]
        verification_spec = dict((node or {}).get("verification_spec") or {})
        if expected_artifacts and not verification_spec.get("expected_artifacts"):
            verification_spec["expected_artifacts"] = expected_artifacts
        if requires_artifact_expectations and task_kind in {"coding", "testing", "ops"} and not expected_artifacts:
            errors.append(f"missing_expected_artifacts:{node_key}")
            missing_artifact_nodes.append(node_key)
        if known_caps:
            unknown = sorted({cap for cap in required if cap not in known_caps})
            for cap in unknown:
                errors.append(f"unknown_capability:{node_key}:{cap}")
        normalized_nodes.append(
            {
                **dict(node or {}),
                "node_key": node_key,
                "task_kind": task_kind,
                "risk_level": risk_level,
                "depends_on": depends_on,
                "required_capabilities": required,
                "expected_artifacts": expected_artifacts,
                "verification_spec": verification_spec,
            }
        )

    node_keys = {node["node_key"] for node in normalized_nodes}
    for dep, target in dep_edges:
        if dep not in node_keys:
            errors.append(f"unknown_dependency:{target}:{dep}")

    outgoing = {key: [] for key in node_keys}
    incoming_count = Counter({key: 0 for key in node_keys})
    for dep, target in dep_edges:
        if dep in node_keys and target in node_keys:
            outgoing[dep].append(target)
            incoming_count[target] += 1
    queue = [key for key in node_keys if incoming_count[key] == 0]
    visited = 0
    while queue:
        current = queue.pop()
        visited += 1
        for nxt in outgoing[current]:
            incoming_count[nxt] -= 1
            if incoming_count[nxt] == 0:
                queue.append(nxt)
    if visited != len(node_keys):
        errors.append("dependency_cycle_detected")

    normalized["nodes"] = normalized_nodes
    normalized["dependencies"] = [{"from": dep, "to": target} for dep, target in dep_edges if dep in node_keys and target in node_keys]
    if missing_artifact_nodes:
        normalized["repair_hints"] = {
            "reason": "missing_artifact_expectations",
            "node_keys": missing_artifact_nodes,
            "action": "add_expected_artifacts_and_verification_spec",
        }
    return ProposalValidationResult(ok=not errors, errors=errors, normalized_payload=normalized)
