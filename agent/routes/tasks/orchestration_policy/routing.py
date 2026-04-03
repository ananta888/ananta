from __future__ import annotations

from random import random

from .models import WorkerSelection

ROLE_CAPABILITY_MAP = {
    "planner": {"planning", "task_graph", "analysis"},
    "researcher": {"research", "analysis"},
    "coder": {"coding", "implementation"},
    "reviewer": {"review", "analysis"},
    "tester": {"testing", "verification"},
}

TASK_KIND_ROLE_PREFERENCES = {
    "planning": ["planner"],
    "research": ["researcher"],
    "coding": ["coder"],
    "review": ["reviewer"],
    "testing": ["tester"],
    "verification": ["tester", "reviewer"],
}

SECURITY_LEVEL_RANK = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}

RESEARCH_SPECIALIZATIONS = ("deep_research", "repo_research", "document_research")


def normalize_capabilities(capabilities: list[str] | None) -> list[str]:
    return _normalize_capabilities(capabilities)


def normalize_worker_roles(worker_roles: list[str] | None) -> list[str]:
    allowed = {"planner", "researcher", "coder", "reviewer", "tester"}
    normalized: list[str] = []
    for role in worker_roles or []:
        value = str(role or "").strip().lower()
        if value in allowed and value not in normalized:
            normalized.append(value)
    return normalized


def _normalize_capabilities(capabilities: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for cap in capabilities or []:
        value = str(cap or "").strip().lower()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def derive_research_specialization(
    task: dict | None,
    task_kind: str | None = None,
    required_capabilities: list[str] | None = None,
) -> str | None:
    kind = str(task_kind or (task or {}).get("task_kind") or "").strip().lower()
    normalized_required = _normalize_capabilities(required_capabilities) or derive_required_capabilities(task, kind)
    if kind != "research" and "research" not in normalized_required:
        return None
    for specialization in RESEARCH_SPECIALIZATIONS:
        if specialization in normalized_required:
            return specialization
    return "research" if "research" in normalized_required else None


def derive_required_capabilities(task: dict | None, task_kind: str | None = None) -> list[str]:
    explicit = _normalize_capabilities((task or {}).get("required_capabilities"))
    if explicit:
        return explicit
    kind = str(task_kind or (task or {}).get("task_kind") or "").strip().lower()
    if kind in {"planning", "research", "coding", "review", "testing", "verification"}:
        if kind == "research":
            text = " ".join(
                [
                    str((task or {}).get("title") or ""),
                    str((task or {}).get("description") or ""),
                ]
            ).lower()
            derived = ["research"]
            if any(
                token in text
                for token in ("deep research", "deep-dive", "deep dive", "comprehensive analysis", "comprehensive report")
            ):
                derived.append("deep_research")
            if any(token in text for token in ("repository", "repo", "codebase", "source tree", "git history")):
                derived.append("repo_research")
            if any(token in text for token in ("document", "pdf", "spec", "readme", "docs", "knowledge base")):
                derived.append("document_research")
            return derived
        return [kind]
    text = " ".join(
        [
            str((task or {}).get("title") or ""),
            str((task or {}).get("description") or ""),
        ]
    ).lower()
    if "test" in text or "verify" in text:
        return ["testing"]
    if "review" in text:
        return ["review"]
    if "plan" in text:
        return ["planning"]
    if "research" in text or "analy" in text:
        derived = ["research"]
        if any(token in text for token in ("repository", "repo", "codebase", "source tree", "git history")):
            derived.append("repo_research")
        if any(token in text for token in ("document", "pdf", "spec", "readme", "docs", "knowledge base")):
            derived.append("document_research")
        return derived
    return ["coding"]


def choose_worker_for_task(
    task: dict | None,
    workers: list[dict],
    task_kind: str | None = None,
    required_capabilities: list[str] | None = None,
) -> WorkerSelection:
    normalized_required = _normalize_capabilities(required_capabilities) or derive_required_capabilities(task, task_kind)
    kind = str(task_kind or (task or {}).get("task_kind") or "").strip().lower()
    preferred_roles = TASK_KIND_ROLE_PREFERENCES.get(kind, [])
    required_security = _security_level(task or {})

    ranked: list[tuple[float, dict, list[str], list[str], float, float, str]] = []
    for worker in workers:
        liveness = dict(worker.get("liveness") or {})
        worker_status = str(worker.get("status") or liveness.get("status") or "").lower()
        if worker.get("available_for_routing") is False or liveness.get("available_for_routing") is False:
            continue
        if worker_status != "online":
            continue
        if worker.get("registration_validated") is False:
            continue

        execution_limits = dict(worker.get("execution_limits") or {})
        max_parallel = int(execution_limits.get("max_parallel_tasks") or 0)
        current_load = int(worker.get("current_load") or 0)
        if max_parallel > 0 and current_load >= max_parallel:
            continue

        worker_security = _security_level(worker)
        if worker_security < required_security:
            continue

        worker_roles = normalize_worker_roles(worker.get("worker_roles"))
        worker_caps = _normalize_capabilities(worker.get("capabilities"))
        expanded_caps = set(worker_caps)
        for role in worker_roles:
            expanded_caps.update(ROLE_CAPABILITY_MAP.get(role, set()))

        matched_caps = [cap for cap in normalized_required if cap in expanded_caps]
        matched_roles = [role for role in worker_roles if role in preferred_roles]
        if normalized_required and not matched_caps:
            continue

        load_penalty = _load_ratio(current_load, max_parallel)
        success_signal, quality_signal = _quality_signals(worker)

        score = (
            len(matched_caps) * 50.0
            + len(matched_roles) * 20.0
            + (success_signal * 20.0)
            + (quality_signal * 10.0)
            + (worker_security * 2.0)
            - (load_penalty * 30.0)
        )
        ranked.append((score, worker, matched_caps, matched_roles, load_penalty, success_signal, _security_label(worker_security)))

    if ranked:
        ranked.sort(key=lambda item: (-item[0], item[4], -(item[5]), item[1].get("url") or ""))
        _, selected, matched_caps, matched_roles, load_penalty, success_signal, security_label = ranked[0]
        return WorkerSelection(
            worker_url=str(selected.get("url") or ""),
            reasons=[
                f"matched_capabilities:{','.join(matched_caps)}" if matched_caps else "matched_capabilities:none",
                f"matched_roles:{','.join(matched_roles)}" if matched_roles else "matched_roles:none",
                f"load_ratio:{load_penalty:.2f}",
                f"success_signal:{success_signal:.2f}",
                f"security_level:{security_label}",
            ],
            matched_capabilities=matched_caps,
            matched_roles=matched_roles,
            strategy="capability_quality_load_match",
        )

    fallback = _pick_fallback_worker(workers, min_security=required_security)
    if fallback:
        fallback_load_ratio = _load_ratio(
            int(fallback.get("current_load") or 0),
            int((fallback.get("execution_limits") or {}).get("max_parallel_tasks") or 0),
        )
        return WorkerSelection(
            worker_url=str(fallback.get("url") or ""),
            reasons=[
                "fallback:least_loaded_online_worker",
                f"required_capabilities:{','.join(normalized_required)}",
                f"load_ratio:{fallback_load_ratio:.2f}",
            ],
            matched_capabilities=[],
            matched_roles=[],
            strategy="fallback",
        )
    return WorkerSelection(
        worker_url=None,
        reasons=["no_online_worker_available"],
        matched_capabilities=[],
        matched_roles=[],
        strategy="none",
    )


def compute_retry_delay_seconds(
    attempt: int,
    base_backoff_seconds: float,
    *,
    max_backoff_seconds: float = 30.0,
    jitter_factor: float = 0.2,
) -> float:
    base = max(0.0, float(base_backoff_seconds))
    if base == 0:
        return 0.0
    bounded = min(base * (2 ** max(0, attempt - 1)), max(0.0, float(max_backoff_seconds)))
    jitter = bounded * max(0.0, float(jitter_factor)) * random()
    return bounded + jitter


def build_dispatch_queue(tasks: list[dict]) -> list[dict]:
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    dispatchable = []
    for task in tasks:
        status = str(task.get("status") or "").lower()
        if status not in {"todo", "assigned", "blocked", "created"}:
            continue
        dispatchable.append(task)
    dispatchable.sort(
        key=lambda task: (
            priority_rank.get(str(task.get("priority") or "medium").lower(), 1),
            float(task.get("created_at") or 0.0),
            str(task.get("id") or ""),
        )
    )
    queue = []
    for index, task in enumerate(dispatchable, start=1):
        queue.append(
            {
                "task_id": task.get("id"),
                "priority": task.get("priority"),
                "status": task.get("status"),
                "assigned_agent_url": task.get("assigned_agent_url"),
                "queue_position": index,
            }
        )
    return queue


def _pick_fallback_worker(workers: list[dict], min_security: int) -> dict | None:
    candidates: list[tuple[float, str, dict]] = []
    for worker in workers:
        liveness = dict(worker.get("liveness") or {})
        worker_status = str(worker.get("status") or liveness.get("status") or "").lower()
        if worker.get("available_for_routing") is False or liveness.get("available_for_routing") is False:
            continue
        if worker_status != "online":
            continue
        if worker.get("registration_validated") is False:
            continue
        if _security_level(worker) < min_security:
            continue
        max_parallel = int((worker.get("execution_limits") or {}).get("max_parallel_tasks") or 0)
        current_load = int(worker.get("current_load") or 0)
        if max_parallel > 0 and current_load >= max_parallel:
            continue
        load_ratio = _load_ratio(current_load, max_parallel)
        candidates.append((load_ratio, str(worker.get("url") or ""), worker))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _quality_signals(worker: dict) -> tuple[float, float]:
    routing = dict(worker.get("routing_signals") or {})
    metrics = dict(worker.get("metrics") or {})
    success_rate = _clamp_01(routing.get("success_rate", worker.get("success_rate", metrics.get("success_rate", 0.5))))
    quality_rate = _clamp_01(routing.get("quality_rate", worker.get("quality_rate", metrics.get("quality_rate", success_rate))))
    return success_rate, quality_rate


def _load_ratio(current_load: int, max_parallel: int) -> float:
    if max_parallel > 0:
        return min(max(current_load, 0) / float(max_parallel), 1.0)
    return min(max(current_load, 0) / 4.0, 1.0)


def _security_level(subject: dict) -> int:
    value = str(subject.get("security_level") or subject.get("security_tier") or "medium").strip().lower()
    return SECURITY_LEVEL_RANK.get(value, SECURITY_LEVEL_RANK["medium"])


def _security_label(level: int) -> str:
    for key, value in SECURITY_LEVEL_RANK.items():
        if value == level:
            return key
    return "medium"


def _clamp_01(value: object) -> float:
    try:
        parsed = float(value)
    except Exception:
        return 0.5
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return parsed
