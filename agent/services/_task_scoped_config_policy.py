"""Config-policy cluster for the task-scoped execution service.

Extracted from ``agent.services.task_scoped_execution_service`` as the
config_policy cluster of SPLIT-001. The module owns one concern:
translating the agent configuration dict into resolved, bounded,
deduplicated policy structures that the runtime and domain-action
clusters can consume directly.

Three layers live here:

1. **Pure leaf utilities** (SPLIT-001b): parsing + clamping numeric
   configuration values to documented bounds, with a safe fallback if
   the input is unparseable. No policy reading, no Flask app-context
   coupling, no cluster-specific business rules.

2. **Intermediate resolvers** (SPLIT-001c): the slightly higher-level
   functions that read the agent config and emit a fully-resolved
   policy struct (semantic output correction, interactive context
   profile, research context compaction, interactive timeouts). They
   call the leaf utilities in the same module; no ``cls`` access
   back into the service.

3. **CLI / opencode / native-worker policy resolvers** (SPLIT-001d):
   the six pure-resolver methods that translate the agent config
   into the cli-session policy struct, the opencode execution /
   interactive-launch mode, the native-worker enablement flag,
   the native-worker-routing decision, and the native-opencode-runtime
   detector. These are pure (or, for one method, ``_should_use_native_worker_runtime``,
   a thin composition over the other pure functions in this same module).

Backwards compatibility is preserved at the service boundary via thin
delegating wrappers in :class:`TaskScopedExecutionService` (12-month
deprecation window). See SPLIT-001 in
``todos/todo.refactor-large-files-split.json`` for the master plan.
"""

from __future__ import annotations

from typing import Optional, Union


# ======================================================================
# 1. Pure leaf utilities (SPLIT-001b)
# ======================================================================


def normalize_temperature(value: Union[float, int, str, None]) -> Optional[float]:
    """Clamp a temperature value to the documented [0.0, 2.0] range.

    Returns ``None`` for unparseable input or explicit ``None``.
    """
    if value is None:
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    if normalized != normalized:  # NaN guard
        return None
    if normalized < 0.0:
        return 0.0
    if normalized > 2.0:
        return 2.0
    return normalized


def bounded_int(
    value: Union[int, float, str, None],
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Parse ``value`` as int and clamp to ``[minimum, maximum]``.

    Returns ``default`` for ``None`` / unparseable / non-integer floats.
    """
    if value is None:
        return int(default)
    if isinstance(value, bool):
        # bool is an int subclass; reject True/False explicitly to avoid
        # accidental coercion of truthiness flags.
        return int(default)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(default)
    if parsed < minimum:
        return int(minimum)
    if parsed > maximum:
        return int(maximum)
    return int(parsed)


def bounded_float(
    value: Union[int, float, str, None],
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    """Parse ``value`` as float and clamp to ``[minimum, maximum]``.

    Returns ``default`` for ``None`` / unparseable / NaN.
    """
    if value is None:
        return float(default)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed:  # NaN guard
        return float(default)
    if parsed < minimum:
        return float(minimum)
    if parsed > maximum:
        return float(maximum)
    return float(parsed)


# ======================================================================
# 2. Intermediate resolvers (SPLIT-001c)
# ======================================================================


def resolve_worker_semantic_output_correction_policy(agent_cfg: dict | None) -> dict:
    cfg = dict(agent_cfg or {})
    runtime_cfg = cfg.get("worker_runtime") if isinstance(cfg.get("worker_runtime"), dict) else {}
    raw_policy = runtime_cfg.get("semantic_output_correction")
    raw_policy = dict(raw_policy) if isinstance(raw_policy, dict) else {}
    if not raw_policy:
        return {}
    provider_cfg = raw_policy.get("embedding_provider")
    provider_cfg = dict(provider_cfg) if isinstance(provider_cfg, dict) else {}
    fields_cfg = raw_policy.get("fields")
    fields_cfg = dict(fields_cfg) if isinstance(fields_cfg, dict) else {}
    risk_cfg = fields_cfg.get("risk_classification")
    risk_cfg = dict(risk_cfg) if isinstance(risk_cfg, dict) else {}
    risk_candidates = [
        str(item).strip().lower()
        for item in list(risk_cfg.get("candidates") or ["low", "medium", "high", "critical"])
        if str(item).strip()
    ]
    deduped_risk_candidates: list[str] = []
    seen_candidates: set[str] = set()
    for item in risk_candidates:
        if item not in seen_candidates:
            seen_candidates.add(item)
            deduped_risk_candidates.append(item)
    provider = str(provider_cfg.get("provider") or "local").strip().lower() or "local"
    policy = {
        "enabled": bool(raw_policy.get("enabled", False)),
        "similarity_threshold": bounded_float(
            raw_policy.get("similarity_threshold"),
            default=0.9,
            minimum=0.5,
            maximum=1.0,
        ),
        "min_margin": bounded_float(raw_policy.get("min_margin"), default=0.03, minimum=0.0, maximum=1.0),
        "lexical_weight": bounded_float(raw_policy.get("lexical_weight"), default=0.35, minimum=0.0, maximum=1.0),
        "embedding_provider": {
            "provider": provider,
            "dimensions": bounded_int(provider_cfg.get("dimensions"), default=12, minimum=4, maximum=4096),
            "model_version": str(provider_cfg.get("model_version") or "").strip() or None,
            "base_url": str(provider_cfg.get("base_url") or "").strip() or None,
            "api_key": str(provider_cfg.get("api_key") or "").strip() or None,
            "model": str(provider_cfg.get("model") or "").strip() or None,
            "timeout_seconds": bounded_int(provider_cfg.get("timeout_seconds"), default=20, minimum=1, maximum=120),
        },
        "fields": {
            "risk_classification": {
                "enabled": bool(risk_cfg.get("enabled", True)),
                "candidates": deduped_risk_candidates or ["low", "medium", "high", "critical"],
            }
        },
    }
    return policy


# Backward-compat alias for the pre-split private name.
_resolve_worker_semantic_output_correction_policy = resolve_worker_semantic_output_correction_policy


def resolve_interactive_context_profile(agent_cfg: dict | None, *, retry: bool = False) -> dict:
    cfg = dict(agent_cfg or {})
    runtime_cfg = cfg.get("opencode_runtime") if isinstance(cfg.get("opencode_runtime"), dict) else {}
    profile_cfg = (
        runtime_cfg.get("interactive_context_profile")
        if isinstance(runtime_cfg.get("interactive_context_profile"), dict)
        else {}
    )

    task_brief_chars = bounded_int(
        profile_cfg.get("task_brief_chars_retry" if retry else "task_brief_chars"),
        default=520 if retry else 900,
        minimum=180,
        maximum=4000,
    )
    hub_context_chars = bounded_int(
        profile_cfg.get("hub_context_chars_retry" if retry else "hub_context_chars"),
        default=1200 if retry else 2600,
        minimum=256,
        maximum=12000,
    )
    research_prompt_chars = bounded_int(
        profile_cfg.get("research_prompt_chars_retry" if retry else "research_prompt_chars"),
        default=700 if retry else 1800,
        minimum=200,
        maximum=8000,
    )
    artifact_ids_limit = bounded_int(
        profile_cfg.get("artifact_ids_limit_retry" if retry else "artifact_ids_limit"),
        default=3 if retry else 6,
        minimum=1,
        maximum=20,
    )
    knowledge_ids_limit = bounded_int(
        profile_cfg.get("knowledge_ids_limit_retry" if retry else "knowledge_ids_limit"),
        default=2 if retry else 4,
        minimum=1,
        maximum=20,
    )
    repo_refs_limit = bounded_int(
        profile_cfg.get("repo_refs_limit_retry" if retry else "repo_refs_limit"),
        default=3 if retry else 6,
        minimum=1,
        maximum=30,
    )
    return {
        "compact": True,
        "retry": bool(retry),
        "task_brief_char_limit": task_brief_chars,
        "hub_context_char_limit": hub_context_chars,
        "research_prompt_char_limit": research_prompt_chars,
        "artifact_ids_limit": artifact_ids_limit,
        "knowledge_collection_ids_limit": knowledge_ids_limit,
        "repo_scope_refs_limit": repo_refs_limit,
    }


_resolve_interactive_context_profile = resolve_interactive_context_profile


def compact_research_context(
    research_context: dict | None,
    *,
    profile: dict | None,
) -> dict | None:
    if not isinstance(research_context, dict):
        return research_context
    cfg = dict(profile or {})
    artifact_limit = bounded_int(cfg.get("artifact_ids_limit"), default=6, minimum=1, maximum=20)
    knowledge_limit = bounded_int(cfg.get("knowledge_collection_ids_limit"), default=4, minimum=1, maximum=20)
    repo_ref_limit = bounded_int(cfg.get("repo_scope_refs_limit"), default=6, minimum=1, maximum=30)
    prompt_limit = bounded_int(cfg.get("research_prompt_char_limit"), default=1800, minimum=200, maximum=12000)
    prompt_section = str((research_context or {}).get("prompt_section") or "").strip()
    if len(prompt_section) > prompt_limit:
        prompt_section = prompt_section[: max(1, prompt_limit - 14)].rstrip() + "\n\n[gekürzt]"
    return {
        **dict(research_context or {}),
        "artifact_ids": list((research_context or {}).get("artifact_ids") or [])[:artifact_limit],
        "knowledge_collection_ids": list((research_context or {}).get("knowledge_collection_ids") or [])[:knowledge_limit],
        "repo_scope_refs": list((research_context or {}).get("repo_scope_refs") or [])[:repo_ref_limit],
        "prompt_section": prompt_section or None,
        "context_char_count": min(
            int((research_context or {}).get("context_char_count") or len(prompt_section)),
            len(prompt_section) if prompt_section else int((research_context or {}).get("context_char_count") or 0),
        ),
    }


_compact_research_context = compact_research_context


def resolve_interactive_propose_timeout(agent_cfg: dict | None, *, fallback: int) -> int:
    cfg = dict(agent_cfg or {})
    runtime_cfg = cfg.get("opencode_runtime") if isinstance(cfg.get("opencode_runtime"), dict) else {}
    configured = bounded_int(
        runtime_cfg.get("interactive_propose_timeout_seconds"),
        default=420,
        minimum=120,
        maximum=1800,
    )
    return max(int(fallback or 60), configured)


_resolve_interactive_propose_timeout = resolve_interactive_propose_timeout


def resolve_interactive_retry_timeout(agent_cfg: dict | None, *, fallback: int) -> int:
    cfg = dict(agent_cfg or {})
    runtime_cfg = cfg.get("opencode_runtime") if isinstance(cfg.get("opencode_runtime"), dict) else {}
    configured = bounded_int(
        runtime_cfg.get("interactive_retry_timeout_seconds"),
        default=max(int(fallback or 60), 480),
        minimum=120,
        maximum=1800,
    )
    return max(int(fallback or 60), configured)


_resolve_interactive_retry_timeout = resolve_interactive_retry_timeout


# ======================================================================
# 3. CLI / opencode / native-worker policy resolvers (SPLIT-001d)
# ======================================================================


def resolve_cli_session_policy(agent_cfg: dict | None) -> dict:
    cfg = agent_cfg or {}
    mode = cfg.get("cli_session_mode") if isinstance(cfg.get("cli_session_mode"), dict) else {}
    backends = [
        str(item or "").strip().lower()
        for item in list(mode.get("stateful_backends") or ["opencode", "codex"])
        if str(item or "").strip()
    ]
    return {
        "enabled": bool(mode.get("enabled", False)),
        "stateful_backends": backends,
        "max_turns_per_session": max(1, min(int(mode.get("max_turns_per_session") or 40), 200)),
        "max_sessions": max(1, min(int(mode.get("max_sessions") or 200), 2000)),
        "allow_task_scoped_auto_session": bool(mode.get("allow_task_scoped_auto_session", True)),
        "reuse_scope": str(mode.get("reuse_scope") or "task").strip().lower() or "task",
        "native_opencode_sessions": bool(mode.get("native_opencode_sessions", False)),
    }


# Backward-compat alias for the pre-split private name.
_cli_session_policy = resolve_cli_session_policy


def resolve_opencode_execution_mode(agent_cfg: dict | None) -> str:
    cfg = agent_cfg or {}
    runtime_cfg = cfg.get("opencode_runtime") if isinstance(cfg.get("opencode_runtime"), dict) else {}
    mode = str(runtime_cfg.get("execution_mode") or "live_terminal").strip().lower()
    return mode if mode in {"backend", "live_terminal", "interactive_terminal"} else "live_terminal"


_resolve_opencode_execution_mode = resolve_opencode_execution_mode


def resolve_opencode_interactive_launch_mode(agent_cfg: dict | None) -> str:
    cfg = agent_cfg or {}
    runtime_cfg = cfg.get("opencode_runtime") if isinstance(cfg.get("opencode_runtime"), dict) else {}
    mode = str(runtime_cfg.get("interactive_launch_mode") or "run").strip().lower()
    return mode if mode in {"run", "tui"} else "run"


_resolve_opencode_interactive_launch_mode = resolve_opencode_interactive_launch_mode


def native_worker_runtime_enabled(agent_cfg: dict | None) -> bool:
    runtime_cfg = (agent_cfg or {}).get("worker_runtime")
    runtime_cfg = runtime_cfg if isinstance(runtime_cfg, dict) else {}
    native_cfg = runtime_cfg.get("native_worker_runtime")
    native_cfg = native_cfg if isinstance(native_cfg, dict) else {}
    return bool(native_cfg.get("enabled", False))


_native_worker_runtime_enabled = native_worker_runtime_enabled


def should_use_native_worker_runtime(
    *,
    proposal_meta: dict | None,
    agent_cfg: dict | None,
    command: str | None,
) -> bool:
    if not str(command or "").strip():
        return False
    if not native_worker_runtime_enabled(agent_cfg):
        return False
    proposal = dict(proposal_meta or {})
    backend = str(proposal.get("backend") or "").strip().lower()
    routing = dict(proposal.get("routing") or {})
    runtime_path = str(routing.get("worker_runtime_path") or "").strip().lower()
    return backend == "ananta-worker" and runtime_path == "native_worker_pipeline"


_should_use_native_worker_runtime = should_use_native_worker_runtime


def has_native_opencode_runtime(session_payload: dict | None) -> bool:
    metadata = (session_payload or {}).get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    runtime_meta = metadata.get("opencode_runtime")
    runtime_meta = runtime_meta if isinstance(runtime_meta, dict) else {}
    return str(runtime_meta.get("kind") or "").strip().lower() == "native_server"


_has_native_opencode_runtime = has_native_opencode_runtime
