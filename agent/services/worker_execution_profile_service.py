from __future__ import annotations

VALID_WORKER_EXECUTION_PROFILES: tuple[str, ...] = ("safe", "balanced", "fast")
DEFAULT_WORKER_EXECUTION_PROFILE = "balanced"


def normalize_worker_execution_profile(value: str | None, *, default: str = DEFAULT_WORKER_EXECUTION_PROFILE) -> str:
    normalized_default = str(default or DEFAULT_WORKER_EXECUTION_PROFILE).strip().lower() or DEFAULT_WORKER_EXECUTION_PROFILE
    if normalized_default not in VALID_WORKER_EXECUTION_PROFILES:
        normalized_default = DEFAULT_WORKER_EXECUTION_PROFILE
    normalized = str(value or "").strip().lower()
    if normalized in VALID_WORKER_EXECUTION_PROFILES:
        return normalized
    return normalized_default


def resolve_worker_execution_profile(
    *,
    worker_execution_context: dict | None,
    agent_cfg: dict | None,
) -> tuple[str, str]:
    context = dict(worker_execution_context or {})
    profile_from_context = (
        str(context.get("worker_profile") or "").strip()
        or str(context.get("execution_profile") or "").strip()
    )
    if profile_from_context:
        source = str(context.get("profile_source") or "task_context").strip().lower() or "task_context"
        return normalize_worker_execution_profile(profile_from_context), source
    runtime_cfg = (agent_cfg or {}).get("worker_runtime") if isinstance((agent_cfg or {}).get("worker_runtime"), dict) else {}
    profile_from_runtime = str((runtime_cfg or {}).get("default_execution_profile") or "").strip()
    if profile_from_runtime:
        return normalize_worker_execution_profile(profile_from_runtime), "agent_default"
    return DEFAULT_WORKER_EXECUTION_PROFILE, "agent_default"
