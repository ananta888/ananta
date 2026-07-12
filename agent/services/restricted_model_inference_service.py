"""RTIPM-003: RestrictedModelInferenceService.

Single gateway for all restricted (non-generative) model inference operations.
Dispatches to registered adapters based on requested operation and declared
capabilities. Adapters are optional; missing ML dependencies produce a
``degraded`` status, not a crash.

Hard separation contract
────────────────────────
- ``embed()``            → list[list[float]]
- ``classify()``         → ClassificationResult  (fixed label set)
- ``rerank()``           → list[RerankResult]    (scores only)
- ``score_choices()``    → list[ChoiceScore]      (fixed choices)
- ``extract_features()`` → FeatureVector
- ``risk_score()``       → RiskScoreResult

None of these operations return free text. ``model.generate()`` is never
invoked by this service. If a caller somehow passes a free text answer
through an adapter result, ``validate_no_generation()`` will reject it.

Audit events are emitted for: started, finished, blocked, degraded.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from agent.services.model_inference_adapters import (
    CAP_CHOICE_SCORING,
    CAP_CLASSIFICATION,
    CAP_EMBEDDINGS,
    CAP_FEATURE_EXTRACTION,
    CAP_RERANK,
    AdapterStatus,
    BaseInferenceAdapter,
    ChoiceScore,
    ClassificationResult,
    FeatureVector,
    RerankResult,
    RiskScoreResult,
)
from agent.services.path_ai_mode_policy_service import (
    AI_MODE_RESTRICTED_TRANSFORMER,
    PathAiModePolicyService,
    get_path_ai_mode_policy_service,
)
from agent.services.restricted_inference_config_service import (
    TASK_CANDIDATE_RERANK,
    TASK_CHOICE_SCORE,
    TASK_CLASSIFY,
    TASK_PATH_DOMAIN_CLASSIFY,
    TASK_RISK_SCORE,
    RestrictedInferenceConfig,
    RestrictedInferenceConfigService,
)
from agent.services.restricted_inference_contract import (
    RestrictedInferenceOperation,
    RestrictedInferenceRequest,
    RestrictedInferenceResponse,
    RestrictedInferenceStatus,
)
from agent.services.restricted_inference_port import (
    ContractRestrictedInferencePort,
    HttpRestrictedInferenceTransport,
    HubTaskQueueRestrictedInferencePort,
    RestrictedInferencePort,
)
from agent.services.restricted_inference_result_guard import (
    RestrictedInferenceResultError,
    validate_choice_scores,
    validate_restricted_result,
)

log = logging.getLogger(__name__)

# Supported operation names
OP_EMBED = "embed"
OP_CLASSIFY = "classify"
OP_RERANK = "rerank"
OP_SCORE_CHOICES = "score_choices"
OP_EXTRACT_FEATURES = "extract_features"
OP_RISK_SCORE = "risk_score"

ALL_OPS = frozenset(
    {
        OP_EMBED,
        OP_CLASSIFY,
        OP_RERANK,
        OP_SCORE_CHOICES,
        OP_EXTRACT_FEATURES,
        OP_RISK_SCORE,
    }
)

_OP_TO_CAP: dict[str, str] = {
    OP_EMBED: CAP_EMBEDDINGS,
    OP_CLASSIFY: CAP_CLASSIFICATION,
    OP_RERANK: CAP_RERANK,
    OP_SCORE_CHOICES: CAP_CHOICE_SCORING,
    OP_EXTRACT_FEATURES: CAP_FEATURE_EXTRACTION,
    OP_RISK_SCORE: CAP_CLASSIFICATION,
}

_OP_TO_TASK: dict[str, str] = {
    OP_EMBED: TASK_CANDIDATE_RERANK,
    OP_CLASSIFY: TASK_CLASSIFY,
    OP_RERANK: TASK_CANDIDATE_RERANK,
    OP_SCORE_CHOICES: TASK_CHOICE_SCORE,
    OP_EXTRACT_FEATURES: TASK_PATH_DOMAIN_CLASSIFY,
    OP_RISK_SCORE: TASK_RISK_SCORE,
}


# ── Audit event ───────────────────────────────────────────────────────────────


@dataclass
class InferenceAuditEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    event: str = ""
    operation: str = ""
    task_id: str = ""
    adapter_engine: str = ""
    model_id: str = ""
    manifest_digest: str = ""
    path: str = ""
    latency_ms: float = 0.0
    reason_code: str = ""
    fallback_used: bool = False
    matched_rule: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event": self.event,
            "operation": self.operation,
            "task_id": self.task_id,
            "engine": self.adapter_engine,
            "adapter_engine": self.adapter_engine,
            "model_id": self.model_id,
            "manifest_digest": self.manifest_digest,
            "path": self.path,
            "latency_ms": round(self.latency_ms, 2),
            "reason_code": self.reason_code,
            "fallback_used": self.fallback_used,
            "matched_rule": self.matched_rule,
            "ts": self.ts,
        }


@dataclass(frozen=True)
class RestrictedInferenceInvocationContext:
    """Hub-owned correlation, deadline and immutable execution policy."""

    request_id: str
    task_id: str
    run_id: str
    tenant_id: str
    policy_hash: str
    deadline_epoch_ms: int
    idempotency_key: str = ""
    paths: tuple[str, ...] = ()
    execution_policy: Mapping[str, Any] = field(default_factory=dict)
    owned_task: bool = False


class RestrictedInferenceRemoteError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


# ── Mock adapter (deterministic scores, no ML deps required) ──────────────────


class MockInferenceAdapter(BaseInferenceAdapter):
    """Deterministic mock adapter for tests and degraded-state fallback.

    All scores are derived from text length / position — reproducible without
    any ML library. No generation.
    """

    ENGINE = "mock"
    CAPABILITIES = frozenset(
        {
            CAP_EMBEDDINGS,
            CAP_CLASSIFICATION,
            CAP_RERANK,
            CAP_CHOICE_SCORING,
            CAP_FEATURE_EXTRACTION,
        }
    )
    MODEL_ID = "mock-deterministic-v1"

    def __init__(self, dims: int = 8) -> None:
        self._dims = max(1, dims)

    def status(self) -> AdapterStatus:
        return AdapterStatus(
            name="mock",
            engine=self.ENGINE,
            status="ready",
            capabilities=self.CAPABILITIES,
            model_id=self.MODEL_ID,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = []
        for text in texts:
            seed = sum(ord(c) for c in text)
            vec = [float((seed + i) % 100) / 100.0 for i in range(self._dims)]
            result.append(vec)
        return result

    def classify(self, text: str, labels: list[str]) -> ClassificationResult:
        if not labels:
            labels = ["positive", "negative"]
        seed = sum(ord(c) for c in text)
        idx = seed % len(labels)
        scores = {label: 1.0 / len(labels) for label in labels}
        scores[labels[idx]] = 0.6
        total = sum(scores.values())
        scores = {k: round(v / total, 4) for k, v in scores.items()}
        return ClassificationResult(
            label=labels[idx],
            confidence=scores[labels[idx]],
            all_scores=scores,
            model_id=self.MODEL_ID,
            engine=self.ENGINE,
        )

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[RerankResult]:
        q_seed = sum(ord(c) for c in query)
        results = []
        for i, c in enumerate(candidates):
            excerpt = str(c.get("excerpt") or c.get("path") or "")
            common = len(set(query.lower().split()) & set(excerpt.lower().split()))
            score = round(min(1.0, common / max(len(query.split()), 1) + (q_seed % 10) / 100), 4)
            results.append(
                RerankResult(
                    path=str(c.get("path") or ""),
                    record_id=str(c.get("record_id") or str(i)),
                    score=score,
                    reason_code="mock_word_overlap",
                    model_id=self.MODEL_ID,
                    engine=self.ENGINE,
                )
            )
        results.sort(key=lambda r: (-r.score, r.path))
        return results

    def score_choices(self, prompt: str, choices: list[str]) -> list[ChoiceScore]:
        seed = sum(ord(c) for c in prompt)
        results = []
        total_w = sum(len(c) + (seed % 7) for c in choices) or 1
        for choice in choices:
            w = (len(choice) + seed % 7) / total_w
            results.append(ChoiceScore(choice=choice, score=round(w, 4), model_id=self.MODEL_ID, engine=self.ENGINE))
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def extract_features(self, text: str) -> FeatureVector:
        vec = self.embed([text])[0]
        return FeatureVector(vector=vec, dimensions=len(vec), model_id=self.MODEL_ID, engine=self.ENGINE)

    def risk_score(self, input_dict: dict[str, Any]) -> RiskScoreResult:
        text = " ".join(str(v) for v in input_dict.values() if v)
        seed = sum(ord(c) for c in text)
        score = (seed % 100) / 100.0
        cat = "high" if score >= 0.5 else "low"
        return RiskScoreResult(
            risk_score=round(score, 4), risk_category=cat, model_id=self.MODEL_ID, engine=self.ENGINE
        )


# ── Main service ──────────────────────────────────────────────────────────────


class RestrictedModelInferenceService:
    """Dispatch restricted (non-generative) inference operations to adapters.

    All operations are gated by PathAiModePolicy: if ``restricted_transformer_inference``
    is blocked for the relevant path, a ``InferenceBlockedError`` is raised and an
    audit event is emitted.
    """

    class InferenceBlockedError(RuntimeError):
        pass

    class NoDegradedFallbackError(RuntimeError):
        pass

    def __init__(
        self,
        *,
        adapters: list[BaseInferenceAdapter] | None = None,
        policy_service: PathAiModePolicyService | None = None,
        use_mock_fallback: bool = True,
        config: RestrictedInferenceConfig | None = None,
        config_service: RestrictedInferenceConfigService | None = None,
        adapter_registry: Any | None = None,
        inference_port: RestrictedInferencePort | None = None,
        manifest_resolver: Callable[[str], str] | None = None,
        manifest_engine_resolver: Callable[[str], str] | None = None,
        invocation_context_provider: Callable[[str], RestrictedInferenceInvocationContext] | None = None,
        legacy_local_enabled: bool | None = None,
    ) -> None:
        self._config = config or (config_service.resolve() if config_service else None)
        self._registry = adapter_registry
        configured_worker_url = (
            self._config.worker_url if self._config and self._config.execution_mode == "worker" else ""
        )
        configured_worker_allowlist = (
            tuple(self._config.worker_allowed_endpoints)
            if self._config and self._config.execution_mode == "worker"
            else ()
        )
        self._port = inference_port or _port_from_environment(
            configured_worker_url,
            configured_worker_allowlist,
        )
        if legacy_local_enabled is None:
            if self._config is not None:
                legacy_local_enabled = self._config.legacy_local_enabled
            else:
                configured = os.getenv("ANANTA_RESTRICTED_INFERENCE_LEGACY_LOCAL")
                legacy_local_enabled = (
                    bool(adapters)
                    if configured is None and self._port is not None
                    else (True if configured is None else configured.strip().lower() in {"1", "true", "yes", "on"})
                )
        if self._config and self._config.production_profile:
            if self._config.execution_mode != "worker" or legacy_local_enabled or self._config.allow_mock_fallback:
                raise ValueError("invalid restricted-inference production configuration")
        self._legacy_local_enabled = bool(legacy_local_enabled)
        self._manifest_resolver = manifest_resolver or self._default_manifest_id
        self._manifest_engine_resolver = manifest_engine_resolver or self._default_manifest_engine
        self._invocation_context_provider = invocation_context_provider or self._default_invocation_context
        configured_adapters: list[BaseInferenceAdapter] = []
        if self._legacy_local_enabled and self._config and self._registry:
            configured_adapters = self._registry.build_many(self._config.models)
        self._adapters: list[BaseInferenceAdapter] = (
            list(adapters or configured_adapters) if self._legacy_local_enabled else []
        )
        self._policy = policy_service or get_path_ai_mode_policy_service()
        allow_fallback = self._config.allow_mock_fallback if self._config else use_mock_fallback
        self._enabled = self._config.enabled if self._config else True
        self._mock = MockInferenceAdapter() if allow_fallback and self._legacy_local_enabled else None
        self._audit_log: list[InferenceAuditEvent] = []

    def add_adapter(self, adapter: BaseInferenceAdapter) -> None:
        if not self._legacy_local_enabled:
            raise RuntimeError("local adapters are disabled; use RestrictedInferencePort")
        self._adapters.append(adapter)

    def get_adapter_statuses(self) -> list[AdapterStatus]:
        if self._port is not None:
            return [
                AdapterStatus(
                    name="restricted-inference-worker",
                    engine="worker-port",
                    status="declared",
                    capabilities=frozenset(_OP_TO_CAP.values()),
                )
            ]
        statuses = [a.status() for a in self._adapters]
        if self._mock:
            statuses.append(self._mock.status())
        return statuses

    def audit_log(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._audit_log]

    # ── Gated operations ──────────────────────────────────────────────────────

    def embed(
        self,
        texts: list[str],
        *,
        path: str = "",
        context: RestrictedInferenceInvocationContext | None = None,
    ) -> list[list[float]]:
        self._check_enabled(OP_EMBED, path)
        self._check_policy(path, OP_EMBED)
        if self._port is not None:
            result = self._remote(OP_EMBED, {"texts": texts}, path=path, context=context)
            vectors = [list(map(float, row)) for row in result["vectors"]]
            self._guard_result(OP_EMBED, vectors, path=path, expected_count=len(texts))
            return vectors
        adapter = self._pick(OP_EMBED, path)
        t0 = time.time()
        result = adapter.embed(texts)
        self._guard_result(OP_EMBED, result, path=path, expected_count=len(texts))
        self._audit(OP_EMBED, adapter, path, (time.time() - t0) * 1000, "ok")
        return result

    def classify(
        self,
        text: str,
        labels: list[str],
        *,
        path: str = "",
        context: RestrictedInferenceInvocationContext | None = None,
    ) -> ClassificationResult:
        if not labels or any(not isinstance(label, str) or not label for label in labels):
            raise ValueError("classify requires non-empty string labels")
        if len(set(labels)) != len(labels):
            raise ValueError("classify requires unique labels")
        self._check_enabled(OP_CLASSIFY, path)
        self._check_policy(path, OP_CLASSIFY)
        if self._port is not None:
            raw = self._remote(OP_CLASSIFY, {"text": text, "labels": labels}, path=path, context=context)
            result = ClassificationResult(
                label=str(raw["label"]),
                confidence=float(raw["confidence"]),
                all_scores={str(key): float(value) for key, value in raw["all_scores"].items()},
                model_id=str(raw["model_id"]),
                engine=str(raw["engine"]),
                latency_ms=float(raw["latency_ms"]),
            )
            self._guard_result(OP_CLASSIFY, result, path=path, allowed_labels=labels)
            return result
        adapter = self._pick(OP_CLASSIFY, path)
        t0 = time.time()
        result = adapter.classify(text, labels)
        self._guard_result(OP_CLASSIFY, result, path=path, allowed_labels=labels)
        self._audit(OP_CLASSIFY, adapter, path, (time.time() - t0) * 1000, "ok")
        return result

    def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        path: str = "",
        context: RestrictedInferenceInvocationContext | None = None,
    ) -> list[RerankResult]:
        self._check_enabled(OP_RERANK, path)
        self._check_policy(path, OP_RERANK)
        if self._port is not None:
            raw = self._remote(OP_RERANK, {"query": query, "candidates": candidates}, path=path, context=context)
            result = [
                RerankResult(
                    path=str(item["path"]),
                    record_id=str(item["record_id"]),
                    score=float(item["score"]),
                    confidence=float(item["confidence"]),
                    reason_code=str(item["reason_code"]),
                    model_id=str(raw["model_id"]),
                    engine=str(raw["engine"]),
                    manifest_digest=str(raw["manifest_digest"]),
                )
                for item in raw["items"]
            ]
            self._guard_result(OP_RERANK, result, path=path, candidates=candidates)
            return result
        adapter = self._pick(OP_RERANK, path)
        t0 = time.time()
        result = adapter.rerank(query, candidates)
        self._guard_result(OP_RERANK, result, path=path, candidates=candidates)
        self._audit(OP_RERANK, adapter, path, (time.time() - t0) * 1000, "ok")
        return result

    def score_choices(
        self,
        prompt: str,
        choices: list[str],
        *,
        path: str = "",
        context: RestrictedInferenceInvocationContext | None = None,
    ) -> list[ChoiceScore]:
        if not choices:
            raise ValueError("score_choices requires at least one choice")
        if any(not isinstance(choice, str) or not choice for choice in choices):
            raise ValueError("score_choices requires non-empty string choices")
        if len(set(choices)) != len(choices):
            raise ValueError("score_choices requires unique choices")
        self._check_enabled(OP_SCORE_CHOICES, path)
        self._check_policy(path, OP_SCORE_CHOICES)
        if self._port is not None:
            raw = self._remote(
                OP_SCORE_CHOICES,
                {"prompt": prompt, "choices": choices},
                path=path,
                context=context,
            )
            result = [
                ChoiceScore(
                    choice=str(item["choice"]),
                    score=float(item["score"]),
                    model_id=str(raw["model_id"]),
                    engine=str(raw["engine"]),
                )
                for item in raw["items"]
            ]
            self._guard_result(OP_SCORE_CHOICES, result, path=path, allowed_choices=choices)
            return result
        adapter = self._pick(OP_SCORE_CHOICES, path)
        t0 = time.time()
        result = adapter.score_choices(prompt, choices)
        self._guard_result(OP_SCORE_CHOICES, result, path=path, allowed_choices=choices)
        self._audit(OP_SCORE_CHOICES, adapter, path, (time.time() - t0) * 1000, "ok")
        return result

    def extract_features(
        self,
        text: str,
        *,
        path: str = "",
        context: RestrictedInferenceInvocationContext | None = None,
    ) -> FeatureVector:
        self._check_enabled(OP_EXTRACT_FEATURES, path)
        self._check_policy(path, OP_EXTRACT_FEATURES)
        if self._port is not None:
            raw = self._remote(OP_EXTRACT_FEATURES, {"text": text}, path=path, context=context)
            result = FeatureVector(
                vector=[float(value) for value in raw["vector"]],
                dimensions=int(raw["dimensions"]),
                model_id=str(raw["model_id"]),
                engine=str(raw["engine"]),
            )
            self._guard_result(OP_EXTRACT_FEATURES, result, path=path)
            return result
        adapter = self._pick(OP_EXTRACT_FEATURES, path)
        t0 = time.time()
        result = adapter.extract_features(text)
        self._guard_result(OP_EXTRACT_FEATURES, result, path=path)
        self._audit(OP_EXTRACT_FEATURES, adapter, path, (time.time() - t0) * 1000, "ok")
        return result

    def risk_score(
        self,
        input_dict: dict[str, Any],
        *,
        path: str = "",
        context: RestrictedInferenceInvocationContext | None = None,
    ) -> RiskScoreResult:
        self._check_enabled(OP_RISK_SCORE, path)
        self._check_policy(path, OP_RISK_SCORE)
        if self._port is not None:
            raw = self._remote(OP_RISK_SCORE, {"input": input_dict}, path=path, context=context)
            result = RiskScoreResult(
                risk_score=float(raw["risk_score"]),
                risk_category=str(raw["risk_category"]),
                confidence=float(raw["confidence"]),
                model_id=str(raw["model_id"]),
                engine=str(raw["engine"]),
            )
            self._guard_result(OP_RISK_SCORE, result, path=path)
            return result
        adapter = self._pick(OP_RISK_SCORE, path)
        t0 = time.time()
        result = adapter.risk_score(input_dict)
        self._guard_result(OP_RISK_SCORE, result, path=path)
        self._audit(OP_RISK_SCORE, adapter, path, (time.time() - t0) * 1000, "ok")
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _remote(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        path: str,
        context: RestrictedInferenceInvocationContext | None,
    ) -> Mapping[str, Any]:
        if self._port is None:
            raise self.NoDegradedFallbackError("restricted inference worker port is not configured")
        provided_paths = context.paths if context is not None else ()
        request_paths = self._request_paths(
            operation,
            payload,
            explicit_path=path,
            context_paths=provided_paths,
        )
        if not request_paths:
            self._audit_blocked(operation, path, "path_scope_required")
            raise self.InferenceBlockedError(
                "restricted inference worker dispatch requires an explicit path scope"
            )
        invocation = context or self._invocation_context_provider(operation)
        try:
            manifest_id = self._manifest_resolver(operation)
            manifest_engine = self._manifest_engine_resolver(operation)
            for request_path in request_paths:
                self._check_policy(request_path, operation)
                policy = self._policy.resolve(request_path)
                if policy.allowed_model_engines and manifest_engine not in set(policy.allowed_model_engines):
                    self._audit_blocked(operation, request_path, "model_engine_not_allowed")
                    raise self.InferenceBlockedError(
                        f"restricted inference engine is not allowed for path={request_path!r}"
                    )
            execution_policy = self._effective_execution_policy(
                operation,
                request_paths,
                invocation.execution_policy,
            )
            policy_hash = hashlib.sha256(
                json.dumps(
                    {
                        "base_policy_hash": invocation.policy_hash,
                        "execution_policy": execution_policy,
                        "paths": request_paths,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            request = RestrictedInferenceRequest(
                request_id=invocation.request_id,
                task_id=invocation.task_id,
                run_id=invocation.run_id,
                tenant_id=invocation.tenant_id,
                operation=RestrictedInferenceOperation(operation),
                payload=payload,
                model_manifest_id=manifest_id,
                policy_hash=policy_hash,
                deadline_epoch_ms=invocation.deadline_epoch_ms,
                paths=request_paths,
                idempotency_key=invocation.idempotency_key,
                execution_policy=execution_policy,
            )
            started = time.monotonic_ns()
            response: RestrictedInferenceResponse = self._port.execute(request)
        except Exception as exc:
            self._finish_owned_invocation(invocation, succeeded=False, error=exc)
            raise
        if response.status is RestrictedInferenceStatus.FAILED:
            assert response.error is not None
            self._finish_owned_invocation(
                invocation,
                succeeded=False,
                reason_code=response.error.code,
            )
            self._audit_blocked(operation, path, response.error.code)
            raise RestrictedInferenceRemoteError(
                response.error.code,
                response.error.message,
                retryable=response.error.retryable,
            )
        assert response.result is not None
        result = response.result
        self._finish_owned_invocation(invocation, succeeded=True)
        self._audit_remote(
            operation,
            path,
            elapsed_ms=(time.monotonic_ns() - started) / 1_000_000,
            engine=str(result["engine"]),
            model_id=str(result["model_id"]),
            manifest_digest=str(result["manifest_digest"]),
            task_id=invocation.task_id,
        )
        return result

    @staticmethod
    def _request_paths(
        operation: str,
        payload: Mapping[str, Any],
        *,
        explicit_path: str,
        context_paths: tuple[str, ...],
    ) -> tuple[str, ...]:
        values: list[str] = [str(item).strip() for item in context_paths if str(item).strip()]
        if explicit_path.strip():
            values.append(explicit_path.strip())
        if operation == OP_RERANK:
            candidates = payload.get("candidates")
            if isinstance(candidates, list):
                values.extend(
                    str(item.get("path") or "").strip()
                    for item in candidates
                    if isinstance(item, Mapping) and str(item.get("path") or "").strip()
                )
        return tuple(dict.fromkeys(values))

    def _effective_execution_policy(
        self,
        operation: str,
        request_paths: tuple[str, ...],
        requested: Mapping[str, Any],
    ) -> dict[str, Any]:
        policy: dict[str, Any] = {
            "allow_attention": bool(requested.get("allow_attention", False)),
            "allow_cpu_fallback": bool(requested.get("allow_cpu_fallback", False)),
            "allow_hidden_states": bool(requested.get("allow_hidden_states", False)),
            "device": str(requested.get("device") or (self._config.device if self._config else "cpu")),
            "max_batch_size": int(requested.get("max_batch_size") or 64),
            "max_candidates": int(requested.get("max_candidates") or 64),
            "max_input_chars": int(requested.get("max_input_chars") or 1_000_000),
            "max_output_dimensions": int(requested.get("max_output_dimensions") or 65_536),
        }
        task_id = _OP_TO_TASK.get(operation)
        if self._config is not None and task_id:
            task = self._config.tasks.get(task_id)
            if task is not None:
                policy["max_candidates"] = min(policy["max_candidates"], task.max_candidates)
                policy["max_batch_size"] = min(policy["max_batch_size"], task.max_candidates)
            model = self._config.model_for_task(task_id)
            if model is not None:
                policy["device"] = model.device
        for request_path in request_paths:
            path_policy = self._policy.resolve(request_path)
            policy["allow_attention"] = bool(policy["allow_attention"] and path_policy.allow_attention)
            policy["allow_hidden_states"] = bool(
                policy["allow_hidden_states"] and path_policy.allow_hidden_states
            )
            if path_policy.max_batch_size > 0:
                policy["max_batch_size"] = min(
                    policy["max_batch_size"],
                    path_policy.max_batch_size,
                )
                policy["max_candidates"] = min(
                    policy["max_candidates"],
                    path_policy.max_batch_size,
                )
            if path_policy.max_input_chars > 0:
                policy["max_input_chars"] = min(
                    policy["max_input_chars"],
                    path_policy.max_input_chars,
                )
            if operation in {OP_CLASSIFY, OP_RERANK, OP_SCORE_CHOICES, OP_RISK_SCORE} and not path_policy.allow_logits:
                self._audit_blocked(operation, request_path, "logits_not_allowed")
                raise self.InferenceBlockedError(
                    f"restricted inference logits are not allowed for path={request_path!r}"
                )
            if operation == OP_EXTRACT_FEATURES and not path_policy.allow_hidden_states:
                self._audit_blocked(operation, request_path, "hidden_states_not_allowed")
                raise self.InferenceBlockedError(
                    f"restricted inference hidden states are not allowed for path={request_path!r}"
                )
        return policy

    def _default_manifest_id(self, operation: str) -> str:
        configured = str(os.getenv(f"ANANTA_RESTRICTED_INFERENCE_MANIFEST_{operation.upper()}", "")).strip()
        if configured:
            return configured
        default = str(os.getenv("ANANTA_RESTRICTED_INFERENCE_MANIFEST_ID", "")).strip()
        if default:
            return default
        task_id = _OP_TO_TASK.get(operation, "")
        if self._config and task_id:
            model = self._config.model_for_task(task_id)
            if model is not None:
                return model.id
        raise self.NoDegradedFallbackError(f"No model manifest configured for operation={operation!r}")

    def _default_manifest_engine(self, operation: str) -> str:
        configured = str(os.getenv(f"ANANTA_RESTRICTED_INFERENCE_ENGINE_{operation.upper()}", "")).strip()
        if configured:
            return configured
        default = str(os.getenv("ANANTA_RESTRICTED_INFERENCE_ENGINE", "")).strip()
        if default:
            return default
        task_id = _OP_TO_TASK.get(operation, "")
        if self._config and task_id:
            model = self._config.model_for_task(task_id)
            if model is not None:
                return model.engine
        return ""

    @staticmethod
    def _default_invocation_context(operation: str) -> RestrictedInferenceInvocationContext:
        from agent.services.task_queue_service import get_task_queue_service

        identifier = uuid.uuid4().hex
        tenant_id = _current_restricted_tenant_id()
        task_id = f"restricted-request-{identifier}"
        get_task_queue_service().ingest_task(
            task_id=task_id,
            status="in_progress",
            title=f"Restricted inference request: {operation}",
            description="Hub-owned parent for one bounded non-generative worker delegation.",
            priority="medium",
            created_by="hub",
            source="restricted_inference",
            tags=["restricted_inference", "no_generation"],
            event_type="restricted_inference_requested",
            event_details={"operation": operation},
            extra_fields={
                "task_kind": "restricted_inference_request",
                "required_capabilities": ["restricted_inference", operation],
                "worker_execution_context": {
                    "restricted_inference_request": {
                        "operation": operation,
                        "tenant_scope_hash": hashlib.sha256(tenant_id.encode()).hexdigest(),
                        "no_generation": True,
                        "persistence_owner": "hub",
                    }
                },
            },
        )
        return RestrictedInferenceInvocationContext(
            request_id=f"request-{identifier}",
            task_id=task_id,
            run_id=f"run-{identifier}",
            tenant_id=tenant_id,
            policy_hash="hub-policy",
            deadline_epoch_ms=time.time_ns() // 1_000_000 + 30_000,
            owned_task=True,
        )

    @staticmethod
    def _finish_owned_invocation(
        invocation: RestrictedInferenceInvocationContext,
        *,
        succeeded: bool,
        reason_code: str = "restricted_inference_failed",
        error: Exception | None = None,
    ) -> None:
        if not invocation.owned_task:
            return
        from agent.services.task_runtime_service import update_local_task_status

        update_local_task_status(
            invocation.task_id,
            "completed" if succeeded else "failed",
            status_reason_code=None if succeeded else reason_code,
            status_reason_details=(
                {} if succeeded or error is None else {"error_type": type(error).__name__}
            ),
            verification_status={
                "restricted_inference_request": {
                    "status": "verified" if succeeded else "failed",
                    "no_generation": True,
                }
            },
            event_type=(
                "restricted_inference_request_completed"
                if succeeded
                else "restricted_inference_request_failed"
            ),
            event_actor="hub",
            event_details={"status": "completed" if succeeded else "failed"},
        )

    def _check_policy(self, path: str, op: str) -> None:
        if not path:
            return
        policy = self._policy.resolve(path)
        if not policy.is_mode_allowed(AI_MODE_RESTRICTED_TRANSFORMER):
            self._audit_blocked(
                op,
                path,
                "policy_blocked_restricted_transformer",
                policy.matched_rule.path_glob if policy.matched_rule else "",
            )
            raise self.InferenceBlockedError(
                f"restricted_transformer_inference blocked for path={path!r} by policy rule={policy.matched_rule}"
            )

    def _check_enabled(self, op: str, path: str) -> None:
        if not self._enabled:
            self._audit_blocked(op, path, "restricted_inference_disabled")
            raise self.InferenceBlockedError("restricted_inference is disabled")
        task_id = _OP_TO_TASK.get(op)
        if self._config and task_id:
            task = self._config.tasks.get(task_id)
            if task and not task.enabled:
                self._audit_blocked(op, path, "task_disabled")
                raise self.InferenceBlockedError(f"restricted inference task disabled: {task_id}")

    def _pick(self, op: str, path: str = "") -> BaseInferenceAdapter:
        cap = _OP_TO_CAP.get(op, "")
        allowed_engines: set[str] | None = None
        if path:
            policy = self._policy.resolve(path)
            if policy.allowed_model_engines:
                allowed_engines = set(policy.allowed_model_engines)
        for adapter in self._adapters:
            st = adapter.status()
            if allowed_engines and st.engine not in allowed_engines:
                continue
            if st.status == "ready" and st.has_capability(cap):
                return adapter
        if self._mock:
            log.debug("RestrictedModelInferenceService: using mock fallback for op=%s", op)
            self._audit_degraded(op, path, "mock_fallback_used")
            return self._mock
        raise self.NoDegradedFallbackError(f"No adapter available for operation={op!r} and mock fallback is disabled")

    def _guard_result(self, op: str, result: Any, *, path: str, **context: Any) -> None:
        try:
            validate_restricted_result(op, result, **context)
        except RestrictedInferenceResultError as exc:
            self._audit_blocked(op, path, exc.reason_code)
            raise

    def _audit(self, op: str, adapter: BaseInferenceAdapter, path: str, ms: float, reason: str) -> None:
        st = adapter.status()
        ev = InferenceAuditEvent(
            event="model_inference_finished",
            operation=op,
            task_id=_OP_TO_TASK.get(op, ""),
            adapter_engine=st.engine,
            model_id=st.model_id,
            path=path,
            latency_ms=ms,
            reason_code=reason,
        )
        self._audit_log.append(ev)

    def _audit_remote(
        self,
        op: str,
        path: str,
        *,
        elapsed_ms: float,
        engine: str,
        model_id: str,
        manifest_digest: str,
        task_id: str,
    ) -> None:
        self._audit_log.append(
            InferenceAuditEvent(
                event="model_inference_finished",
                operation=op,
                task_id=task_id,
                adapter_engine=engine,
                model_id=model_id,
                manifest_digest=manifest_digest,
                path=path,
                latency_ms=elapsed_ms,
                reason_code="ok",
            )
        )

    def _audit_blocked(self, op: str, path: str, reason: str, matched_rule: str = "") -> None:
        ev = InferenceAuditEvent(
            event="model_inference_blocked",
            operation=op,
            task_id=_OP_TO_TASK.get(op, ""),
            path=path,
            reason_code=reason,
            matched_rule=matched_rule,
        )
        self._audit_log.append(ev)
        log.warning(
            "RestrictedModelInferenceService: inference blocked op=%s path=%r reason=%s",
            op,
            path,
            reason,
        )

    def _audit_degraded(self, op: str, path: str, reason: str) -> None:
        ev = InferenceAuditEvent(
            event="model_inference_degraded",
            operation=op,
            task_id=_OP_TO_TASK.get(op, ""),
            adapter_engine="mock",
            model_id=MockInferenceAdapter.MODEL_ID,
            path=path,
            reason_code=reason,
            fallback_used=True,
        )
        self._audit_log.append(ev)


# ── Validation helper ─────────────────────────────────────────────────────────


def validate_no_generation(results: list[ChoiceScore]) -> None:
    """Backward-compatible wrapper for the hardened choice-result guard."""
    validate_choice_scores(results)


def _port_from_environment(
    endpoint_override: str = "",
    allowed_endpoints_override: tuple[str, ...] = (),
) -> RestrictedInferencePort | None:
    endpoint = str(endpoint_override or os.getenv("ANANTA_RESTRICTED_INFERENCE_URL", "")).strip()
    allowed_endpoints = allowed_endpoints_override or tuple(
        item.strip()
        for item in str(os.getenv("ANANTA_RESTRICTED_INFERENCE_ALLOWED_ENDPOINTS", "")).split(",")
        if item.strip()
    )
    token = str(os.getenv("ANANTA_RESTRICTED_INFERENCE_TOKEN", "")).strip()
    if not endpoint:
        return None
    if not token:
        raise RuntimeError("restricted inference URL is configured without an internal bearer token")
    if not allowed_endpoints:
        raise RuntimeError("restricted inference URL is configured without an exact endpoint allowlist")
    transport = HttpRestrictedInferenceTransport(
        endpoint=endpoint,
        allowed_endpoints=allowed_endpoints,
        bearer_token=token,
        connect_timeout_seconds=float(os.getenv("ANANTA_RESTRICTED_INFERENCE_CONNECT_TIMEOUT_SECONDS", "5")),
    )
    return HubTaskQueueRestrictedInferencePort(ContractRestrictedInferencePort(transport))


def _current_restricted_tenant_id() -> str:
    """Return a bounded, pseudonymous request tenant or the Hub system scope."""

    try:
        from flask import g, has_request_context

        if has_request_context():
            user = getattr(g, "user", None)
            agent = getattr(g, "auth_payload", None)
            identity = user if isinstance(user, Mapping) and user else agent
            if isinstance(identity, Mapping):
                raw = str(
                    identity.get("tenant_id")
                    or identity.get("sub")
                    or identity.get("username")
                    or identity.get("agent_id")
                    or ""
                ).strip()
                if raw:
                    return f"tenant-{hashlib.sha256(raw.encode()).hexdigest()[:32]}"
    except RuntimeError:
        pass
    return "hub-system"


# ── Module singleton ──────────────────────────────────────────────────────────

_service: RestrictedModelInferenceService | None = None


def get_restricted_model_inference_service() -> RestrictedModelInferenceService:
    global _service
    if _service is None:
        from agent.services.user_config_service import get_user_config_service

        config_service = RestrictedInferenceConfigService(
            global_config=get_user_config_service().config
        )
        _service = RestrictedModelInferenceService(
            config_service=config_service,
            legacy_local_enabled=(
                False
                if str(os.getenv("ANANTA_RESTRICTED_INFERENCE_URL", "")).strip()
                else None
            ),
        )
    return _service


def reset_restricted_model_inference_service(
    new: RestrictedModelInferenceService | None = None,
) -> None:
    global _service
    _service = new
