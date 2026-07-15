"""Hub-owned delegation boundary for bounded generative transcript correction."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from agent.config import settings
from agent.services.generative_corrector_worker_port import HttpGenerativeCorrectorWorkerPort
from ananta_contracts.voice_corrector_worker import (
    VoiceCorrectorWorkerPort,
    VoiceCorrectorWorkerRequest,
)

_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,191}$")
_PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_PROTECTED_TOKEN_RE = re.compile(r"https?://\S+|\b[\w.:-]*\d[\w.:-]*\b", re.UNICODE)


@dataclass(frozen=True)
class VoiceGenerativeCorrectorOutcome:
    result: dict[str, Any]
    applied: bool
    reason_code: str


class VoiceGenerativeCorrectorTaskTrackerPort(Protocol):
    def start(
        self,
        *,
        tenant_id: str,
        parent_task_id: str,
        request_id: str,
        content_digest: str,
        policy_digest: str,
        model_id: str,
    ) -> str: ...

    def finish(self, task_id: str, *, status: str, reason_code: str) -> None: ...


class VoiceGenerativeCorrectorTaskTracker:
    """Persist opaque correlation only; transcript content remains in result storage."""

    def start(
        self,
        *,
        tenant_id: str,
        parent_task_id: str,
        request_id: str,
        content_digest: str,
        policy_digest: str,
        model_id: str,
    ) -> str:
        from agent.services.task_queue_service import get_task_queue_service
        from agent.services.voice_task_scope import inherited_voice_task_scope

        correlation = f"{parent_task_id}\0{request_id}\0{content_digest}\0{model_id}".encode()
        task_id = f"voice-generative-corrector-{hashlib.sha256(correlation).hexdigest()[:32]}"
        inherited_scope = inherited_voice_task_scope(parent_task_id, tenant_id=tenant_id)
        get_task_queue_service().ingest_task(
            task_id=task_id,
            status="in_progress",
            title="Hub-delegated Voice transcript corrector",
            description="Execute one bounded rewrite in the isolated generative-corrector worker.",
            priority="low",
            created_by="hub",
            source="voice_api",
            tags=["voice_transcription", "generative_corrector", "worker_delegation"],
            event_type="voice_generative_corrector_delegated",
            event_details={"request_id": request_id, "model_id": model_id},
            extra_fields={
                "task_kind": "voice_generative_corrector",
                "parent_task_id": parent_task_id,
                "required_capabilities": ["voice_generative_corrector_worker"],
                "worker_execution_context": {
                    "voice_generative_corrector": {
                        "request_id": request_id,
                        "tenant_scope_hash": hashlib.sha256(tenant_id.encode()).hexdigest(),
                        **inherited_scope,
                        "content_digest": content_digest,
                        "policy_digest": policy_digest,
                        "model_id": model_id,
                        "persistence_owner": "hub",
                    }
                },
            },
        )
        return task_id

    @staticmethod
    def finish(task_id: str, *, status: str, reason_code: str) -> None:
        from agent.services.voice_task_terminal_service import get_voice_task_terminal_service

        succeeded = status in {"corrected", "unchanged"}
        get_voice_task_terminal_service().update_existing(
            task_id,
            "completed" if succeeded else "failed",
            status_reason_code=None if succeeded else reason_code,
            status_reason_details={} if succeeded else {"fallback": True},
            verification_status={
                "voice_generative_corrector": {
                    "status": "verified" if succeeded else "fallback",
                    "reason_code": reason_code,
                }
            },
            event_type=("voice_generative_corrector_completed" if succeeded else "voice_generative_corrector_failed"),
            event_actor="hub",
            event_details={"status": status, "reason_code": reason_code},
        )


class VoiceGenerativeCorrectorService:
    """Delegate a rewrite and fail open to the byte-exact ASR transcript."""

    def __init__(
        self,
        worker_port: VoiceCorrectorWorkerPort | None = None,
        task_tracker: VoiceGenerativeCorrectorTaskTrackerPort | None = None,
    ) -> None:
        self._worker_port = worker_port
        self._task_tracker = task_tracker if task_tracker is not None else VoiceGenerativeCorrectorTaskTracker()

    def apply(
        self,
        result: Mapping[str, Any],
        *,
        effective_configuration: Mapping[str, Any],
        tenant_id: str | None = None,
        parent_task_id: str | None = None,
        request_id: str | None = None,
        language: str | None = None,
        deadline_epoch_ms: int | None = None,
    ) -> VoiceGenerativeCorrectorOutcome:
        baseline = str(result.get("text") or "")
        flags = effective_configuration.get("feature_flags")
        enabled = (
            effective_configuration.get("correction_policy") == "generative_rewrite"
            and isinstance(flags, Mapping)
            and flags.get("generative_corrector") is True
        )
        if not enabled or not baseline:
            return self._fallback(result, "generative_corrector_disabled", baseline=baseline)
        if len(baseline) > 8_000 or "\x00" in baseline:
            return self._fallback(result, "generative_corrector_invalid_baseline", baseline=baseline)
        provider_id = str(effective_configuration.get("generative_corrector_provider") or "embedded").strip().lower()
        model_id = str(effective_configuration.get("generative_corrector_model") or "").strip()
        worker_model_id = corrector_worker_model_id(provider_id=provider_id, model_id=model_id)
        if worker_model_id is None:
            reason = (
                "generative_corrector_provider_not_allowlisted"
                if provider_id not in configured_corrector_providers()
                else "generative_corrector_model_not_allowlisted"
            )
            return self._fallback(result, reason, baseline=baseline)
        try:
            max_edit_ratio = float(effective_configuration.get("generative_corrector_max_edit_ratio", 0.35))
        except (TypeError, ValueError):
            return self._fallback(result, "generative_corrector_invalid_policy", baseline=baseline)
        if not math.isfinite(max_edit_ratio) or not 0.01 <= max_edit_ratio <= 1.0:
            return self._fallback(result, "generative_corrector_invalid_policy", baseline=baseline)
        worker_port = self._worker_port if self._worker_port is not None else _configured_worker_port()
        if worker_port is None:
            return self._fallback(result, "generative_corrector_unavailable", baseline=baseline)
        if not tenant_id or not parent_task_id or not request_id:
            return self._fallback(result, "generative_corrector_correlation_missing", baseline=baseline)

        timeout_ms = max(1, min(int(settings.voice_generative_corrector_timeout_ms), 120_000))
        now_ms = time.time_ns() // 1_000_000
        local_deadline_ms = now_ms + timeout_ms
        if deadline_epoch_ms is not None:
            if isinstance(deadline_epoch_ms, bool) or not isinstance(deadline_epoch_ms, int):
                return self._fallback(result, "generative_corrector_deadline_expired", baseline=baseline)
            local_deadline_ms = min(local_deadline_ms, deadline_epoch_ms)
        if local_deadline_ms <= now_ms:
            return self._fallback(result, "generative_corrector_deadline_expired", baseline=baseline)

        try:
            policy_payload = json.dumps(
                dict(effective_configuration),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        except (TypeError, ValueError):
            return self._fallback(result, "generative_corrector_invalid_policy", baseline=baseline)
        task_id: str | None = None
        try:
            task_id = self._task_tracker.start(
                tenant_id=tenant_id,
                parent_task_id=parent_task_id,
                request_id=request_id,
                content_digest=hashlib.sha256(baseline.encode()).hexdigest(),
                policy_digest=hashlib.sha256(policy_payload).hexdigest(),
                model_id=worker_model_id,
            )
            worker_request = VoiceCorrectorWorkerRequest(
                request_id=request_id,
                task_id=task_id,
                region_id="full-transcript",
                original_text=baseline,
                model_id=worker_model_id,
                language=str(language).strip() if language else None,
                max_edit_ratio=max_edit_ratio,
                deadline_epoch_ms=local_deadline_ms,
            )
            worker_response = worker_port.execute(worker_request)
            worker_response.validate_for(worker_request)
        except Exception:
            if task_id is not None:
                self._finish_safely(task_id, status="failed", reason_code="generative_corrector_failed")
            return self._fallback(
                result,
                "generative_corrector_failed",
                baseline=baseline,
                task_id=task_id,
            )

        reason_code = worker_response.reason_code or f"generative_corrector_{worker_response.status}"
        if worker_response.status == "failed" or worker_response.corrected_text is None:
            try:
                self._task_tracker.finish(task_id, status="failed", reason_code=reason_code)
            except Exception:
                return self._fallback(
                    result,
                    "generative_corrector_tracking_failed",
                    baseline=baseline,
                    task_id=task_id,
                )
            return self._fallback(result, reason_code, baseline=baseline, task_id=task_id)
        corrected = worker_response.corrected_text
        if _protected_tokens(baseline) != _protected_tokens(corrected):
            protected_reason = "generative_corrector_protected_token_changed"
            self._finish_safely(task_id, status="failed", reason_code=protected_reason)
            return self._fallback(
                result,
                protected_reason,
                baseline=baseline,
                task_id=task_id,
            )
        try:
            self._task_tracker.finish(task_id, status=worker_response.status, reason_code=reason_code)
        except Exception:
            return self._fallback(
                result,
                "generative_corrector_tracking_failed",
                baseline=baseline,
                task_id=task_id,
            )

        updated = dict(result)
        updated["original_text"] = baseline
        updated["text"] = corrected
        updated["generative_corrector"] = {
            "schema_version": "ananta.voice-generative-correction.v1",
            "status": worker_response.status,
            "applied": corrected != baseline,
            "changed": corrected != baseline,
            "review_required": True,
            "original_text": baseline,
            "corrected_text": corrected,
            "edits": [
                {
                    **edit.to_dict(),
                    "start": edit.original_start,
                    "end": edit.original_end,
                    "before": edit.original_text,
                    "after": edit.corrected_text,
                }
                for edit in worker_response.edits
            ],
            "edit_ratio": sum(max(len(edit.original_text), len(edit.corrected_text)) for edit in worker_response.edits)
            / max(1, len(baseline)),
            "model_id": worker_response.model_id,
            "provider_id": provider_id,
            "model_revision": worker_response.model_revision,
            "engine_id": worker_response.engine_id,
            "prompt_version": worker_response.prompt_version,
            "worker_task_id": task_id,
            "execution_owner": "worker",
        }
        updated["decision_trace"] = {
            **dict(result.get("decision_trace") or {}),
            "generative_corrector": {
                "execution_owner": "worker",
                "execution_path": "generative_corrector_worker",
                "status": worker_response.status,
                "reason_code": reason_code,
                "model_id": worker_response.model_id,
                "provider_id": provider_id,
                "worker_task_id": task_id,
            },
        }
        return VoiceGenerativeCorrectorOutcome(
            result=updated,
            applied=corrected != baseline,
            reason_code=reason_code,
        )

    def _finish_safely(self, task_id: str, *, status: str, reason_code: str) -> None:
        try:
            self._task_tracker.finish(task_id, status=status, reason_code=reason_code)
        except Exception:
            return

    @staticmethod
    def _fallback(
        result: Mapping[str, Any],
        reason_code: str,
        *,
        baseline: str,
        task_id: str | None = None,
    ) -> VoiceGenerativeCorrectorOutcome:
        updated = dict(result)
        updated["decision_trace"] = {
            **dict(result.get("decision_trace") or {}),
            "generative_corrector": {
                "execution_owner": "worker",
                "execution_path": "generative_corrector_worker",
                "status": "fallback",
                "reason_code": reason_code,
                **({"worker_task_id": task_id} if task_id else {}),
            },
        }
        if baseline and reason_code != "generative_corrector_disabled":
            updated["original_text"] = baseline
            updated["generative_corrector"] = {
                "schema_version": "ananta.voice-generative-correction.v1",
                "status": "fallback",
                "applied": False,
                "changed": False,
                "review_required": True,
                "reason_code": reason_code,
                "original_text": baseline,
                "corrected_text": baseline,
                "edits": [],
                "execution_owner": "worker",
                **({"worker_task_id": task_id} if task_id else {}),
            }
        return VoiceGenerativeCorrectorOutcome(result=updated, applied=False, reason_code=reason_code)


def _protected_tokens(value: str) -> Counter[str]:
    return Counter(match.group(0) for match in _PROTECTED_TOKEN_RE.finditer(value))


def configured_corrector_models() -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            item.strip()
            for item in str(settings.voice_generative_corrector_models or "").split(",")
            if _MODEL_ID_RE.fullmatch(item.strip())
        )
    )


def configured_corrector_providers() -> tuple[str, ...]:
    values = tuple(
        dict.fromkeys(
            item.strip().lower()
            for item in str(settings.voice_generative_corrector_providers or "").split(",")
            if _PROVIDER_ID_RE.fullmatch(item.strip().lower())
        )
    )
    return values or ("embedded",)


def corrector_worker_model_id(*, provider_id: str, model_id: str) -> str | None:
    provider = str(provider_id or "embedded").strip().lower()
    model = str(model_id or "").strip()
    if provider not in configured_corrector_providers() or not _MODEL_ID_RE.fullmatch(model):
        return None
    if provider == "embedded":
        return model if model in configured_corrector_models() else None
    qualified = f"{provider}:{model}"
    return qualified if _MODEL_ID_RE.fullmatch(qualified) else None


def resolve_inherited_corrector_configuration(
    effective_configuration: Mapping[str, Any],
    agent_configuration: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Resolve ``inherit`` at the Hub boundary while keeping stored deltas sparse."""

    resolved = dict(effective_configuration)
    provider = str(resolved.get("generative_corrector_provider") or "embedded").strip().lower()
    if provider != "inherit":
        return resolved
    agent_cfg = agent_configuration if isinstance(agent_configuration, Mapping) else {}
    llm_value = agent_cfg.get("llm_config")
    llm_cfg = llm_value if isinstance(llm_value, Mapping) else {}
    master_provider = ""
    master_model = ""
    master_source = ""
    try:
        from agent.services.model_master_default_service import get_global_master_default_service

        master = get_global_master_default_service().read_model()
        master_provider = str(master.get("provider") or "").strip().lower()
        master_model = str(master.get("model") or "").strip()
        master_source = str(master.get("source") or "").strip()
    except Exception:
        # Inheritance remains usable in minimal/test Hubs where the model
        # routing subsystem is intentionally absent.
        pass
    inherited_provider = (
        str(
            llm_cfg.get("provider")
            or agent_cfg.get("default_provider")
            or master_provider
            or settings.default_provider
            or ""
        )
        .strip()
        .lower()
    )
    inherited_model = str(
        llm_cfg.get("model") or agent_cfg.get("default_model") or master_model or settings.default_model or ""
    ).strip()
    if llm_cfg.get("provider") or llm_cfg.get("model"):
        inherited_source = "agent_config.llm_config"
    elif agent_cfg.get("default_provider") or agent_cfg.get("default_model"):
        inherited_source = "agent_config.default"
    elif master_provider or master_model:
        inherited_source = master_source or "model_master_default"
    else:
        inherited_source = "settings.default"
    resolved["generative_corrector_provider"] = inherited_provider
    resolved["generative_corrector_model"] = inherited_model
    resolved["generative_corrector_inherited"] = True
    resolved["generative_corrector_inherited_source"] = inherited_source
    return resolved


def resolve_auto_corrector_configuration(
    effective_configuration: Mapping[str, Any],
    correction_models: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve a general ``auto`` target from the Hub-observed worker catalog."""

    resolved = dict(effective_configuration)
    requested_model = str(resolved.get("generative_corrector_model") or "").strip()
    if requested_model.casefold() != "auto":
        return resolved
    provider_id = str(resolved.get("generative_corrector_provider") or "").strip().lower()
    selected = next(
        (
            str(item.get("id") or "").strip()
            for item in correction_models
            if str(item.get("provider") or "").strip().lower() == provider_id
            and item.get("available") is True
            and _MODEL_ID_RE.fullmatch(str(item.get("id") or "").strip())
        ),
        "",
    )
    if not selected:
        return resolved
    resolved["generative_corrector_requested_model"] = requested_model
    resolved["generative_corrector_model"] = selected
    resolved["generative_corrector_auto_resolved"] = True
    return resolved


def generative_corrector_capabilities(
    worker_port: HttpGenerativeCorrectorWorkerPort | None = None,
    *,
    worker_health: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    port = worker_port if worker_port is not None else _configured_worker_port()
    health = dict(worker_health) if worker_health is not None else _corrector_worker_health(port)
    ready = health.get("status") == "ready"
    ready_provider_ids = {
        str(item)
        for item in health.get("ready_provider_ids", [])
        if isinstance(item, str) and _PROVIDER_ID_RE.fullmatch(item)
    }
    worker_models = tuple(
        dict.fromkeys(
            str(item) for item in health.get("model_ids", []) if isinstance(item, str) and _MODEL_ID_RE.fullmatch(item)
        )
    )
    capabilities = [
        {
            "id": model_id,
            "provider": "embedded",
            "display_name": model_id,
            "role": "generative_corrector",
            "purpose": "transcript_correction",
            "model_type": "causal_lm",
            "local": True,
            "available": bool(ready and model_id in worker_models),
            "status": ("ready" if ready and model_id in worker_models else "model_missing" if ready else "unavailable"),
            "reason_code": (
                None
                if ready and model_id in worker_models
                else "generative_corrector_model_missing"
                if ready
                else "generative_corrector_worker_unavailable"
            ),
            "capabilities": ["transcript_rewrite", "bounded_edits", "provenance"],
        }
        for model_id in configured_corrector_models()
    ]
    allowed_providers = set(configured_corrector_providers()) - {"embedded", "inherit"}
    for worker_model_id in worker_models:
        provider_id, separator, model_id = worker_model_id.partition(":")
        if not separator or provider_id not in allowed_providers or not model_id:
            continue
        capabilities.append(
            {
                "id": model_id,
                "provider": provider_id,
                "display_name": model_id,
                "worker_model_id": worker_model_id,
                "role": "generative_corrector",
                "purpose": "transcript_correction",
                "model_type": "causal_lm",
                "local": True,
                "available": bool(ready and provider_id in ready_provider_ids),
                "status": ("ready" if ready and provider_id in ready_provider_ids else "unavailable"),
                "reason_code": (
                    None if ready and provider_id in ready_provider_ids else "generative_corrector_provider_unavailable"
                ),
                "capabilities": ["transcript_rewrite", "bounded_edits", "provenance"],
            }
        )
    return capabilities


def generative_corrector_provider_capabilities(
    correction_models: list[dict[str, Any]] | None = None,
    worker_port: HttpGenerativeCorrectorWorkerPort | None = None,
    *,
    worker_health: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Project configured execution providers without exposing endpoints or secrets."""

    models = correction_models
    port = worker_port if worker_port is not None else _configured_worker_port()
    health = dict(worker_health) if worker_health is not None else _corrector_worker_health(port)
    if models is None:
        models = generative_corrector_capabilities(worker_port=port)
    ready = health.get("status") == "ready"
    worker_observed = health.get("status") in {"ready", "degraded"}
    worker_provider_ids = {
        str(item)
        for item in health.get("provider_ids", [])
        if isinstance(item, str) and _PROVIDER_ID_RE.fullmatch(item)
    }
    ready_provider_ids = {
        str(item)
        for item in health.get("ready_provider_ids", [])
        if isinstance(item, str) and _PROVIDER_ID_RE.fullmatch(item)
    }
    display_names = {
        "embedded": "Eingebettetes lokales Modell",
        "ollama": "Ollama",
        "lmstudio": "LM Studio",
    }
    capabilities: list[dict[str, Any]] = []
    for provider_id in configured_corrector_providers():
        if provider_id == "inherit":
            continue
        has_available_model = any(
            item.get("provider") == provider_id and item.get("available") is True for item in models
        )
        execution_ready = (
            has_available_model if provider_id == "embedded" else ready and provider_id in ready_provider_ids
        )
        manual_model_configurable = provider_id != "embedded" and provider_id in worker_provider_ids
        capabilities.append(
            {
                "id": provider_id,
                "display_name": display_names.get(provider_id, provider_id),
                "available": execution_ready,
                "supports_manual_model": manual_model_configurable,
                "reason_code": (
                    None
                    if execution_ready
                    else "generative_corrector_model_missing"
                    if provider_id == "embedded" and ready
                    else "generative_corrector_provider_not_configured"
                    if worker_observed and provider_id not in worker_provider_ids
                    else "generative_corrector_provider_unavailable"
                    if worker_observed
                    else "generative_corrector_worker_unavailable"
                ),
            }
        )
    return capabilities


def generative_corrector_capability_bundle(
    agent_configuration: Mapping[str, Any] | None,
    worker_port: HttpGenerativeCorrectorWorkerPort | None = None,
) -> dict[str, Any]:
    """Build one internally consistent public catalog from a single health read."""

    port = worker_port if worker_port is not None else _configured_worker_port()
    health = _corrector_worker_health(port)
    models = generative_corrector_capabilities(port, worker_health=health)
    providers = generative_corrector_provider_capabilities(
        models,
        port,
        worker_health=health,
    )
    return {
        "correction_models": models,
        "correction_providers": providers,
        "correction_default": generative_corrector_default_capability(
            agent_configuration,
            correction_models=models,
            correction_providers=providers,
        ),
    }


def generative_corrector_default_capability(
    agent_configuration: Mapping[str, Any] | None,
    *,
    correction_models: list[dict[str, Any]] | None = None,
    correction_providers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Describe the effective general LLM default used by ``inherit``."""

    inherited = resolve_inherited_corrector_configuration(
        {"generative_corrector_provider": "inherit", "generative_corrector_model": ""},
        agent_configuration,
    )
    provider_id = str(inherited.get("generative_corrector_provider") or "").strip().lower()
    configured_model_id = str(inherited.get("generative_corrector_model") or "").strip()
    models = correction_models if correction_models is not None else generative_corrector_capabilities()
    providers = (
        correction_providers if correction_providers is not None else generative_corrector_provider_capabilities(models)
    )
    resolved = resolve_auto_corrector_configuration(inherited, models)
    model_id = str(resolved.get("generative_corrector_model") or "").strip()
    provider_available = any(item.get("id") == provider_id and item.get("available") is True for item in providers)
    model_available = any(
        item.get("provider") == provider_id and item.get("id") == model_id and item.get("available") is True
        for item in models
    )
    supports_manual = any(
        item.get("id") == provider_id and item.get("supports_manual_model") is True for item in providers
    )
    dispatchable_model_id = corrector_worker_model_id(
        provider_id=provider_id,
        model_id=model_id,
    )
    source = str(inherited.get("generative_corrector_inherited_source") or "general_llm_default")
    result = {
        "provider": provider_id,
        "model": model_id,
        "source": source,
        "available": bool(
            provider_available
            and dispatchable_model_id is not None
            and (model_available or (supports_manual and model_id.casefold() != "auto"))
        ),
    }
    if configured_model_id.casefold() == "auto" and model_id != configured_model_id:
        result["configured_model"] = configured_model_id
    return result


def _corrector_worker_health(
    port: HttpGenerativeCorrectorWorkerPort | None,
) -> Mapping[str, Any]:
    if port is None:
        return {}
    try:
        return port.health(timeout_ms=500)
    except Exception:
        return {}


def _configured_worker_port() -> HttpGenerativeCorrectorWorkerPort | None:
    endpoint = str(settings.voice_generative_corrector_worker_url or "").strip()
    allowed = tuple(
        item.strip()
        for item in str(settings.voice_generative_corrector_worker_allowed_endpoints or "").split(",")
        if item.strip()
    )
    token = str(settings.voice_generative_corrector_worker_token or "").strip()
    origin = str(settings.voice_generative_corrector_hub_origin or "").strip()
    if not endpoint or not allowed or not token or not origin:
        return None
    try:
        return HttpGenerativeCorrectorWorkerPort(
            endpoint=endpoint,
            allowed_endpoints=allowed,
            bearer_token=token,
            hub_origin=origin,
            timeout_ms=max(1, min(int(settings.voice_generative_corrector_timeout_ms), 120_000)),
            max_response_bytes=max(
                1_024,
                min(int(settings.voice_generative_corrector_max_response_bytes), 2 * 1024 * 1024),
            ),
        )
    except (TypeError, ValueError):
        return None


voice_generative_corrector_service = VoiceGenerativeCorrectorService()


def get_voice_generative_corrector_service() -> VoiceGenerativeCorrectorService:
    return voice_generative_corrector_service
