"""Bounded repair procedure catalog with explicit command templates.

DRR-T008: Each catalog entry declares procedure_id, problem_class,
supported_platforms, preconditions, steps, safety_class, required_approval,
verification, and rollback_hints. Command templates use strict parameter
validation — no unbound user-controlled string interpolation.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ── Command template parameter validation ────────────────────────────────────

# Only these parameter names are allowed in command template strings
_ALLOWED_PARAMS: frozenset[str] = frozenset({
    "port", "service_name", "package_name", "config_path",
    "timeout_seconds", "log_lines", "target_dir", "runtime_name",
})

_SAFE_PARAM_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def validate_command_template(template: str) -> str:
    """Validate a command template string.

    Raises ValueError if the template contains unbound interpolation or
    disallowed parameter names.
    """
    placeholders = re.findall(r"\{(\w+)\}", template)
    for param in placeholders:
        if param not in _ALLOWED_PARAMS:
            raise ValueError(
                f"disallowed template parameter {param!r} in {template!r}; "
                f"allowed: {sorted(_ALLOWED_PARAMS)}"
            )
    return template


# ── Catalog models ────────────────────────────────────────────────────────────

class CatalogStep(BaseModel):
    step_id: str
    title: str
    action_class: str = "inspect_state"
    command_template: str = ""
    mutation_candidate: bool = False
    action_safety_class: str = "inspect_only"
    requires_approval: bool = False
    verification_after_step: bool = False
    rollback_hint: str = "no_mutation"
    timeout_seconds: int = 30

    @field_validator("command_template")
    @classmethod
    def _validate_command_template(cls, v: str) -> str:
        if v:
            validate_command_template(v)
        return v

    @field_validator("action_safety_class")
    @classmethod
    def _validate_safety_class(cls, v: str) -> str:
        known = {"inspect_only", "bounded_low_risk", "confirm_required", "high_risk"}
        if v not in known:
            raise ValueError(f"unknown action_safety_class: {v!r}")
        return v


class CatalogEntry(BaseModel):
    procedure_id: str
    problem_class: str
    supported_platforms: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    steps: list[CatalogStep] = Field(default_factory=list)
    safety_class: str = "safe"
    required_approval: str = "none"
    verification: list[str] = Field(default_factory=list)
    rollback_hints: list[str] = Field(default_factory=list)

    @field_validator("safety_class")
    @classmethod
    def _validate_safety_class(cls, v: str) -> str:
        known = {"safe", "review_first", "confirm_required", "high_risk"}
        if v not in known:
            raise ValueError(f"unknown safety_class: {v!r}")
        return v

    @field_validator("problem_class")
    @classmethod
    def _validate_problem_class(cls, v: str) -> str:
        known = {
            "package_install_failure", "service_start_failure", "port_conflict",
            "path_issue", "permission_issue", "compose_failure", "runtime_health_failure",
        }
        if v not in known:
            raise ValueError(f"unknown problem_class: {v!r}")
        return v


# ── Initial catalog entries ───────────────────────────────────────────────────

INITIAL_CATALOG_ENTRIES: list[CatalogEntry] = [
    CatalogEntry(
        procedure_id="proc-port-conflict-inspect-v1",
        problem_class="port_conflict",
        supported_platforms=["ubuntu", "windows11"],
        preconditions=["deterministic_diagnosis_completed", "non_destructive_checks_executed"],
        safety_class="safe",
        required_approval="none",
        steps=[
            CatalogStep(
                step_id="inspect-port",
                title="Inspect which process holds the port",
                action_class="inspect_state",
                command_template="lsof -i :{port} 2>/dev/null || ss -tlnp 'sport = :{port}' 2>/dev/null || netstat -ano | findstr :{port}",
                mutation_candidate=False,
                action_safety_class="inspect_only",
                timeout_seconds=15,
            ),
        ],
        verification=["port_free"],
        rollback_hints=["no_mutation"],
    ),
    CatalogEntry(
        procedure_id="proc-port-conflict-fix-v1",
        problem_class="port_conflict",
        supported_platforms=["ubuntu", "windows11"],
        preconditions=["deterministic_diagnosis_completed", "port_identified"],
        safety_class="review_first",
        required_approval="confirm_required",
        steps=[
            CatalogStep(
                step_id="inspect-port-detail",
                title="Inspect port binding details",
                action_class="inspect_state",
                command_template="lsof -i :{port} 2>/dev/null || ss -tlnp 'sport = :{port}'",
                mutation_candidate=False,
                action_safety_class="inspect_only",
                timeout_seconds=15,
            ),
            CatalogStep(
                step_id="release-port",
                title="Kill process holding the port",
                action_class="port_conflict_resolution",
                command_template="kill -TERM $(lsof -t -i:{port}) 2>/dev/null; sleep 1",
                mutation_candidate=True,
                action_safety_class="confirm_required",
                requires_approval=True,
                verification_after_step=True,
                rollback_hint="process_auto_restart_or_manual_restart",
                timeout_seconds=10,
            ),
            CatalogStep(
                step_id="verify-port",
                title="Verify port is free",
                action_class="verification_check",
                command_template="lsof -i :{port} 2>/dev/null || ss -tlnp 'sport = :{port}' 2>/dev/null || echo port_free",
                mutation_candidate=False,
                action_safety_class="inspect_only",
                timeout_seconds=10,
            ),
        ],
        verification=["port_free", "service_healthy"],
        rollback_hints=["restart_original_process", "manual_service_restart"],
    ),
    CatalogEntry(
        procedure_id="proc-service-start-inspect-v1",
        problem_class="service_start_failure",
        supported_platforms=["ubuntu", "windows11"],
        preconditions=["deterministic_diagnosis_completed"],
        safety_class="safe",
        required_approval="none",
        steps=[
            CatalogStep(
                step_id="check-service-status",
                title="Check service status and logs",
                action_class="inspect_state",
                command_template="systemctl status {service_name} 2>/dev/null || sc query {service_name} 2>/dev/null",
                mutation_candidate=False,
                action_safety_class="inspect_only",
                timeout_seconds=15,
            ),
            CatalogStep(
                step_id="check-service-logs",
                title="Check recent service logs",
                action_class="inspect_state",
                command_template="journalctl -u {service_name} -n {log_lines} --no-pager 2>/dev/null || echo 'logs_unavailable'",
                mutation_candidate=False,
                action_safety_class="inspect_only",
                timeout_seconds=15,
            ),
        ],
        verification=["service_status_known"],
        rollback_hints=["no_mutation"],
    ),
    CatalogEntry(
        procedure_id="proc-service-start-restart-v1",
        problem_class="service_start_failure",
        supported_platforms=["ubuntu", "windows11"],
        preconditions=["deterministic_diagnosis_completed", "service_identified"],
        safety_class="review_first",
        required_approval="confirm_required",
        steps=[
            CatalogStep(
                step_id="pre-restart-check",
                title="Pre-restart service status check",
                action_class="inspect_state",
                command_template="systemctl status {service_name} 2>/dev/null || sc query {service_name}",
                mutation_candidate=False,
                action_safety_class="inspect_only",
                timeout_seconds=10,
            ),
            CatalogStep(
                step_id="restart-service",
                title="Restart the service",
                action_class="restart_service",
                command_template="systemctl restart {service_name} 2>/dev/null && sleep 2 || sc stop {service_name} && sc start {service_name} && sleep 2",
                mutation_candidate=True,
                action_safety_class="confirm_required",
                requires_approval=True,
                verification_after_step=True,
                rollback_hint="restart_previous_service_state_if_known",
                timeout_seconds=30,
            ),
            CatalogStep(
                step_id="verify-restart",
                title="Verify service is running after restart",
                action_class="verification_check",
                command_template="systemctl is-active {service_name} 2>/dev/null || sc query {service_name} | findstr RUNNING",
                mutation_candidate=False,
                action_safety_class="inspect_only",
                timeout_seconds=10,
            ),
        ],
        verification=["service_running", "service_healthy"],
        rollback_hints=["restart_previous_service_state_if_known"],
    ),
    CatalogEntry(
        procedure_id="proc-package-install-inspect-v1",
        problem_class="package_install_failure",
        supported_platforms=["ubuntu"],
        preconditions=["deterministic_diagnosis_completed"],
        safety_class="safe",
        required_approval="none",
        steps=[
            CatalogStep(
                step_id="check-package-state",
                title="Check package manager state",
                action_class="inspect_state",
                command_template="dpkg --audit 2>/dev/null; apt-cache policy {package_name} 2>/dev/null",
                mutation_candidate=False,
                action_safety_class="inspect_only",
                timeout_seconds=15,
            ),
            CatalogStep(
                step_id="check-dpkg-lock",
                title="Check for dpkg lock contention",
                action_class="inspect_state",
                command_template="lsof /var/lib/dpkg/lock 2>/dev/null; lsof /var/lib/apt/lists/lock 2>/dev/null; echo lock_check_done",
                mutation_candidate=False,
                action_safety_class="inspect_only",
                timeout_seconds=10,
            ),
        ],
        verification=["package_state_known"],
        rollback_hints=["no_mutation"],
    ),
    CatalogEntry(
        procedure_id="proc-compose-failure-inspect-v1",
        problem_class="compose_failure",
        supported_platforms=["ubuntu"],
        preconditions=["deterministic_diagnosis_completed"],
        safety_class="safe",
        required_approval="none",
        steps=[
            CatalogStep(
                step_id="check-compose-status",
                title="Check compose stack status",
                action_class="inspect_state",
                command_template="docker compose ps 2>/dev/null || docker-compose ps 2>/dev/null",
                mutation_candidate=False,
                action_safety_class="inspect_only",
                timeout_seconds=15,
            ),
            CatalogStep(
                step_id="check-compose-logs",
                title="Check compose service logs",
                action_class="inspect_state",
                command_template="docker compose logs --tail={log_lines} 2>/dev/null || docker-compose logs --tail={log_lines} 2>/dev/null",
                mutation_candidate=False,
                action_safety_class="inspect_only",
                timeout_seconds=20,
            ),
        ],
        verification=["compose_state_known"],
        rollback_hints=["no_mutation"],
    ),
    CatalogEntry(
        procedure_id="proc-path-issue-inspect-v1",
        problem_class="path_issue",
        supported_platforms=["ubuntu", "windows11"],
        preconditions=["deterministic_diagnosis_completed"],
        safety_class="safe",
        required_approval="none",
        steps=[
            CatalogStep(
                step_id="check-path",
                title="Check PATH for required binary",
                action_class="inspect_state",
                command_template='echo "$PATH" | tr ":" "\\n" | while read p; do ls "$p/{runtime_name}" 2>/dev/null && found=1; done; which {runtime_name} 2>/dev/null || where {runtime_name} 2>/dev/null || echo not_found',
                mutation_candidate=False,
                action_safety_class="inspect_only",
                timeout_seconds=10,
            ),
        ],
        verification=["binary_resolvable"],
        rollback_hints=["no_mutation"],
    ),
]


# ── Catalog access ────────────────────────────────────────────────────────────

def get_catalog() -> list[CatalogEntry]:
    return list(INITIAL_CATALOG_ENTRIES)


def lookup_catalog(
    *,
    problem_class: str,
    environment_facts: dict[str, Any] | None = None,
    include_mutation: bool = False,
) -> list[dict[str, Any]]:
    """Look up catalog entries by problem_class with ranking reasons.

    Returns entries filtered by problem_class and optionally platform,
    ranked by safety_class (safe first) and specificity.
    """
    entries = get_catalog()
    platform = ((environment_facts or {}).get("platform_target") or "").lower()

    matched: list[dict[str, Any]] = []
    for entry in entries:
        if entry.problem_class != problem_class:
            continue
        if platform and entry.supported_platforms:
            supported_lower = [p.lower() for p in entry.supported_platforms]
            if platform not in supported_lower and "cross_platform" not in supported_lower:
                continue
        if not include_mutation and entry.safety_class != "safe":
            continue
        ranking_reason = "exact_problem_class_match"
        if platform and platform in [p.lower() for p in entry.supported_platforms]:
            ranking_reason = "exact_problem_class_and_platform_match"
        matched.append({
            "entry": entry.model_dump(),
            "procedure_id": entry.procedure_id,
            "safety_class": entry.safety_class,
            "mutation_count": sum(1 for s in entry.steps if s.mutation_candidate),
            "ranking_reason": ranking_reason,
        })

    safety_order = {"safe": 0, "review_first": 1, "confirm_required": 2, "high_risk": 3}
    matched.sort(key=lambda item: safety_order.get(item["safety_class"], 99))
    return matched
