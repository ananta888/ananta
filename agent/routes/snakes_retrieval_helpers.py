"""Retrieval helper functions for snake chat — profile resolution, dry-run, domain scope."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agent.config import settings
from agent.services.rag_service import get_rag_service

_SNAKE_RETRIEVAL_CONFIG_KEYS = frozenset({
    "chat_retrieval_profile",
    "chat_retrieval_domain_hint",
    "chat_code_questions_repo_first",
    "chat_architecture_analysis_mode",
    "chat_codecompass_trigger_mode",
    "chat_use_codecompass",
    "chat_include_local_project",
    "chat_include_wikipedia",
    "chat_include_task_memory",
    "chat_source_pack_id",
})


def _snake_retrieval_config_overrides(body: dict[str, Any]) -> dict[str, Any]:
    raw = body.get("retrieval_config")
    if not isinstance(raw, dict):
        return {}
    overrides: dict[str, Any] = {}
    for key in _SNAKE_RETRIEVAL_CONFIG_KEYS:
        value = raw.get(key)
        if isinstance(value, bool):
            overrides[key] = value
        elif isinstance(value, str):
            overrides[key] = value.strip()
    return overrides


def _build_local_repo_fallback_context(prompt: str) -> str:
    text = str(prompt or "").lower()
    repo_root = Path(getattr(settings, "rag_repo_root", ".")).resolve()
    if "_build_grounded_snake_prompt" in text or "snakes.py" in text:
        snakes_file = repo_root / "agent" / "routes" / "snakes.py"
        if snakes_file.exists():
            try:
                lines = snakes_file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                return ""
            for idx, line in enumerate(lines):
                if "def _build_grounded_snake_prompt" in line:
                    start = max(0, idx - 4)
                    end = min(len(lines), idx + 24)
                    return "\n".join(lines[start:end]).strip()
    if "agent/routes" in text or "routes" in text:
        routes_dir = repo_root / "agent" / "routes"
        if routes_dir.exists() and routes_dir.is_dir():
            names = sorted(path.name for path in routes_dir.glob("*.py") if path.is_file())
            if names:
                return "Dateien in agent/routes:\n" + "\n".join(f"- {name}" for name in names[:24])
    return ""


def _domain_scope_response(domain_scope: object | None, bundle_domain_scope: dict | None) -> dict[str, Any]:
    """Build a serialisable domain_scope response fragment for snake ask."""
    if domain_scope is None:
        return {}
    from agent.codecompass.domain_scope import ResolvedDomainScope
    if isinstance(domain_scope, ResolvedDomainScope) and domain_scope.active:
        info = domain_scope.as_dict()
        if bundle_domain_scope and isinstance(bundle_domain_scope, dict):
            info.update({
                k: bundle_domain_scope[k]
                for k in ("active_domain_ids", "filter_stats", "guidance")
                if k in bundle_domain_scope
            })
        return {"domain_scope": info}
    return {}


def _resolve_domain_scope_for_chat(domain_hint: str | None) -> object | None:
    """CCRDS-014: resolve a ``domain:``-prefixed hint to a DomainScope or None.

    Returns None when the feature is disabled, the hint is unprefixed, or
    the hint is empty — the caller then proceeds without hard scoping.
    """
    if not domain_hint:
        return None
    if not str(getattr(settings, "codecompass_domain_scope_enabled", False)).strip().lower() in {"1", "true", "yes"}:
        return None
    try:
        from agent.codecompass.domain_scope_resolver import (
            DomainScopeResolver,
            scope_from_domain_hint,
        )
        scope = scope_from_domain_hint(
            domain_hint,
            enabled=True,
            strict=bool(getattr(settings, "codecompass_scope_strict_mode", True)),
        )
        if scope is None:
            return None
        resolver = DomainScopeResolver(
            repo_root=Path(__file__).resolve().parents[2],
            artifact_path=str(getattr(settings, "codecompass_domain_artifact_path", "") or "") or None,
            descriptor_root=str(getattr(settings, "codecompass_domain_descriptor_root", "") or "") or None,
        )
        return resolver.resolve(scope)
    except Exception:
        return None


def _resolve_snake_retrieval_profile_trace(
    user_text: str,
    *,
    retrieval_config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from agent.routes.ai_snake_config import _current_config
        from agent.services.retrieval_profile_service import resolve_profile

        cfg = _current_config()
        cfg.update(dict(retrieval_config_overrides or {}))
        feature_flag = str(cfg.get("chat_retrieval_profile") or "auto").strip().lower()
        if bool(cfg.get("chat_code_questions_repo_first")) and feature_flag == "auto":
            feature_flag = "repo_first"
        domain_hint = str(cfg.get("chat_retrieval_domain_hint") or "").strip() or None
        profile = resolve_profile(str(user_text or ""), cfg, domain_hint=domain_hint, feature_flag=feature_flag)

        # CCRDS-006/014: show the soft profile domain and the hard runtime
        # scope as separate trace fields. The scope is only resolved when
        # the feature flag is on and the hint uses the `domain:` prefix.
        runtime_domain_scope: dict[str, Any] = {"active": False}
        try:
            from agent.codecompass.domain_scope_resolver import (
                DomainScopeResolver,
                scope_from_domain_hint,
            )

            scope = scope_from_domain_hint(
                domain_hint,
                enabled=bool(getattr(settings, "codecompass_domain_scope_enabled", False)),
                strict=bool(getattr(settings, "codecompass_scope_strict_mode", True)),
            )
            if scope is not None:
                resolver = DomainScopeResolver(
                    repo_root=Path(__file__).resolve().parents[2],
                    artifact_path=str(getattr(settings, "codecompass_domain_artifact_path", "") or "") or None,
                    descriptor_root=str(getattr(settings, "codecompass_domain_descriptor_root", "") or "") or None,
                )
                runtime_domain_scope = resolver.resolve(scope).as_dict()
        except Exception as scope_exc:
            runtime_domain_scope = {"active": False, "error": str(scope_exc)[:120]}

        return {
            "profile_id": profile.profile_id,
            "retrieval_profile_domain": profile.domain,
            "runtime_domain_scope": runtime_domain_scope,
            "domain": profile.domain,
            "intent": profile.intent,
            "analysis_mode": profile.analysis_mode or "standard",
            "output_intent": profile.output_intent,
            "coverage_policy": profile.coverage_policy,
            "summary_policy": profile.summary_policy,
            "source_types": list(profile.source_types),
            "source_type_weights": dict(profile.source_type_weights),
            "feature_flag": profile.feature_flag,
            "trigger_mode": str(cfg.get("chat_codecompass_trigger_mode") or "auto").strip().lower(),
            "selected_by": profile.selected_by,
            "reasons": list(profile.reasons),
            "negative_source_patterns": list(profile.negative_source_patterns),
            "warnings": list(profile.warnings),
        }
    except Exception as exc:
        return {"error": str(exc)[:120]}


def _snake_retrieval_dry_run(
    question: str,
    *,
    retrieval_config_overrides: dict[str, Any] | None = None,
    top_k: int = 20,
) -> dict[str, Any]:
    """RWY-005: run retrieval for *question* without calling an LLM.

    Returns profile metadata, resolver scope flags, candidate counts per
    source type, top-5 candidate paths, and preset hints.
    """
    result: dict[str, Any] = {}
    try:
        from agent.routes.ai_snake_config import _current_config
        from agent.services.retrieval_profile_service import resolve_profile
        from worker.retrieval.codecompass_candidate_resolver import ResolverConfig

        cfg = _current_config()
        cfg.update(dict(retrieval_config_overrides or {}))
        feature_flag = str(cfg.get("chat_retrieval_profile") or "auto").strip().lower()
        if bool(cfg.get("chat_code_questions_repo_first")) and feature_flag == "auto":
            feature_flag = "repo_first"
        domain_hint = str(cfg.get("chat_retrieval_domain_hint") or "").strip() or None

        profile = resolve_profile(question, cfg, domain_hint=domain_hint, feature_flag=feature_flag)
        result["retrieval_profile"] = {
            "profile_id": profile.profile_id,
            "domain": profile.domain,
            "intent": profile.intent,
            "analysis_mode": profile.analysis_mode or "standard",
            "feature_flag": profile.feature_flag,
            "trigger_mode": str(cfg.get("chat_codecompass_trigger_mode") or "auto").strip().lower(),
            "selected_by": profile.selected_by,
            "reasons": list(profile.reasons),
            "source_types": list(profile.source_types),
            "source_type_weights": dict(profile.source_type_weights),
            "negative_source_patterns": list(profile.negative_source_patterns),
            "warnings": list(profile.warnings),
        }

        scope = ResolverConfig.from_env()
        result["resolver_scope"] = {
            "include_source": scope.include_source,
            "include_test_paths": scope.include_test_paths,
            "include_docs": scope.include_docs,
            "include_workflows": scope.include_workflows,
            "include_third_party": scope.include_third_party,
            "include_xml_nodes": scope.include_xml_nodes,
        }

        try:
            bundle, _ = get_rag_service().build_execution_context(
                question,
                task_kind="research",
                retrieval_intent=profile.retrieval_intent or "chat_codecompass_overview",
                source_types=profile.source_types or None,
                max_chunks=max(8, min(top_k if top_k is not None else 40, 40)),
                retrieval_profile=profile.as_dict(),
            )
            chunks = list(bundle.get("chunks") or [])
            src_counts: dict[str, int] = {}
            top_sources: list[dict[str, Any]] = []
            for ch in chunks:
                meta = dict((ch or {}).get("metadata") or {})
                st = str(meta.get("source_type") or (ch or {}).get("engine") or "unknown").lower()
                src_counts[st] = src_counts.get(st, 0) + 1
                if len(top_sources) < 5:
                    path = str(meta.get("file_path") or meta.get("path") or (ch or {}).get("path") or "").strip()
                    score = float((ch or {}).get("score") or meta.get("score") or 0.0)
                    if path:
                        top_sources.append({"path": path, "source_type": st, "score": round(score, 3)})
            result["candidate_counts"] = {
                "total": len(chunks),
                "by_source_type": src_counts,
            }
            result["top_sources"] = top_sources
            result["degraded_channels"] = []
        except Exception as exc:
            result["retrieval_error"] = str(exc)[:200]
            result["candidate_counts"] = {"total": 0, "by_source_type": {}}
            result["top_sources"] = []
            result["degraded_channels"] = [str(exc)[:120]]

        q_lower = question.lower()
        hints: list[str] = []
        if any(w in q_lower for w in ("readme", "docs", "doku", "architektur", "todo", "notiz")) and not scope.include_docs:
            hints.append("Tipp: docs/artifact deaktiviert → :config chat_retrieval_profile docs_first")
        if any(w in q_lower for w in ("test", "spec", "pytest", "unittest")) and not scope.include_test_paths:
            hints.append("Tipp: tests deaktiviert → ANANTA_CODECOMPASS_INCLUDE_TEST_PATHS=1 oder :config code_with_tests")
        if any(w in q_lower for w in ("workflow", "blueprint", "ops", "runbook")) and not scope.include_workflows:
            hints.append("Tipp: workflows deaktiviert → ANANTA_CODECOMPASS_INCLUDE_WORKFLOWS=1 oder :config ops")
        result["preset_hints"] = hints

    except Exception as exc:
        result["error"] = str(exc)[:200]
    return result
