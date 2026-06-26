"""Configuration model for restricted transformer inference.

This module only normalizes and diagnoses configuration. Adapter construction
and inference dispatch live in separate services, keeping config concerns
independent from ML dependencies.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.services.model_inference_adapters import (
    CAP_CLASSIFICATION,
    CAP_CHOICE_SCORING,
    CAP_EMBEDDINGS,
    CAP_FEATURE_EXTRACTION,
    CAP_RERANK,
)

ENGINE_MOCK = "mock"
ENGINE_SENTENCE_TRANSFORMERS = "sentence-transformers"
ENGINE_HUGGINGFACE = "huggingface-transformers"
ENGINE_ONNXRUNTIME = "onnxruntime"
ENGINE_PYTORCH = "pytorch"

KNOWN_ENGINES = frozenset({
    ENGINE_MOCK,
    ENGINE_SENTENCE_TRANSFORMERS,
    ENGINE_HUGGINGFACE,
    ENGINE_ONNXRUNTIME,
    ENGINE_PYTORCH,
})

TASK_CANDIDATE_RERANK = "candidate_rerank"
TASK_CLASSIFY = "task_classify"
TASK_PATH_DOMAIN_CLASSIFY = "path_domain_classify"
TASK_RISK_SCORE = "risk_score"
TASK_SEMANTIC_BOUNDARY_DETECTION = "semantic_boundary_detection"
TASK_CHOICE_SCORE = "choice_score"

KNOWN_TASKS = frozenset({
    TASK_CANDIDATE_RERANK,
    TASK_CLASSIFY,
    TASK_PATH_DOMAIN_CLASSIFY,
    TASK_RISK_SCORE,
    TASK_SEMANTIC_BOUNDARY_DETECTION,
    TASK_CHOICE_SCORE,
})

TASK_REQUIRED_CAPABILITY = {
    TASK_CANDIDATE_RERANK: CAP_RERANK,
    TASK_CLASSIFY: CAP_CLASSIFICATION,
    TASK_PATH_DOMAIN_CLASSIFY: CAP_CLASSIFICATION,
    TASK_RISK_SCORE: CAP_CLASSIFICATION,
    TASK_SEMANTIC_BOUNDARY_DETECTION: CAP_RERANK,
    TASK_CHOICE_SCORE: CAP_CHOICE_SCORING,
}

DEFAULT_TASKS: dict[str, dict[str, Any]] = {
    TASK_CANDIDATE_RERANK: {
        "enabled": True,
        "preferred_engine": ENGINE_MOCK,
        "fallback_to_deterministic": True,
        "max_candidates": 20,
        "weight": 1.0,
    },
    TASK_CLASSIFY: {
        "enabled": True,
        "preferred_engine": ENGINE_MOCK,
        "fallback_to_deterministic": True,
        "labels": ["implementation", "test", "security", "config", "other"],
        "weight": 1.0,
    },
    TASK_PATH_DOMAIN_CLASSIFY: {
        "enabled": True,
        "preferred_engine": ENGINE_MOCK,
        "fallback_to_deterministic": True,
        "labels": ["application", "test", "docs", "config", "security"],
        "weight": 1.0,
    },
    TASK_RISK_SCORE: {
        "enabled": True,
        "preferred_engine": ENGINE_MOCK,
        "fallback_to_deterministic": True,
        "labels": ["low", "medium", "high", "critical"],
        "weight": 1.0,
    },
    TASK_SEMANTIC_BOUNDARY_DETECTION: {
        "enabled": False,
        "preferred_engine": ENGINE_MOCK,
        "fallback_to_deterministic": True,
        "max_candidates": 20,
        "weight": 1.0,
    },
    TASK_CHOICE_SCORE: {
        "enabled": True,
        "preferred_engine": ENGINE_MOCK,
        "fallback_to_deterministic": True,
        "weight": 1.0,
    },
}


@dataclass(frozen=True)
class RestrictedInferenceTaskConfig:
    task_id: str
    enabled: bool = True
    preferred_engine: str = ENGINE_MOCK
    fallback_to_deterministic: bool = True
    max_candidates: int = 20
    labels: list[str] = field(default_factory=list)
    weight: float = 1.0

    @classmethod
    def from_raw(cls, task_id: str, raw: dict[str, Any] | None) -> "RestrictedInferenceTaskConfig":
        merged = dict(DEFAULT_TASKS.get(task_id) or {})
        merged.update(dict(raw or {}))
        try:
            max_candidates = max(1, int(merged.get("max_candidates") or 20))
        except (TypeError, ValueError):
            max_candidates = 20
        try:
            weight = float(merged.get("weight", 1.0))
        except (TypeError, ValueError):
            weight = 1.0
        return cls(
            task_id=task_id,
            enabled=bool(merged.get("enabled", True)),
            preferred_engine=str(merged.get("preferred_engine") or ENGINE_MOCK),
            fallback_to_deterministic=bool(merged.get("fallback_to_deterministic", True)),
            max_candidates=max_candidates,
            labels=[str(item) for item in (merged.get("labels") or [])],
            weight=weight,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "preferred_engine": self.preferred_engine,
            "fallback_to_deterministic": self.fallback_to_deterministic,
            "max_candidates": self.max_candidates,
            "labels": list(self.labels),
            "weight": self.weight,
        }


@dataclass(frozen=True)
class RestrictedInferenceModelConfig:
    id: str
    engine: str = ENGINE_MOCK
    model: str = "mock-deterministic-v1"
    revision: str = ""
    local_path: str = ""
    device: str = "cpu"
    enabled: bool = True
    tasks: list[str] = field(default_factory=lambda: sorted(KNOWN_TASKS))
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None) -> "RestrictedInferenceModelConfig":
        data = dict(raw or {})
        model_id = str(data.get("id") or data.get("model_id") or data.get("model") or "mock-default")
        return cls(
            id=model_id,
            engine=str(data.get("engine") or ENGINE_MOCK),
            model=str(data.get("model") or "mock-deterministic-v1"),
            revision=str(data.get("revision") or ""),
            local_path=str(data.get("local_path") or ""),
            device=str(data.get("device") or "cpu"),
            enabled=bool(data.get("enabled", True)),
            tasks=[str(item) for item in (data.get("tasks") or sorted(KNOWN_TASKS))],
            options={k: v for k, v in data.items() if k not in {
                "id", "model_id", "engine", "model", "revision", "local_path", "device", "enabled", "tasks",
            }},
        )

    def as_dict(self, *, redact_secrets: bool = True) -> dict[str, Any]:
        options = _redact(self.options) if redact_secrets else dict(self.options)
        return {
            "id": self.id,
            "engine": self.engine,
            "model": self.model,
            "revision": self.revision,
            "local_path": self.local_path,
            "device": self.device,
            "enabled": self.enabled,
            "tasks": list(self.tasks),
            **options,
        }


@dataclass(frozen=True)
class RestrictedInferenceConfig:
    enabled: bool = True
    default_engine: str = ENGINE_MOCK
    default_model_id: str = "mock-default"
    device: str = "cpu"
    allow_mock_fallback: bool = True
    allowed_engines: list[str] = field(default_factory=lambda: sorted(KNOWN_ENGINES))
    models: list[RestrictedInferenceModelConfig] = field(default_factory=list)
    tasks: dict[str, RestrictedInferenceTaskConfig] = field(default_factory=dict)

    def model_for_task(self, task_id: str, allowed_engines: set[str] | None = None) -> RestrictedInferenceModelConfig | None:
        task = self.tasks.get(task_id)
        preferred = task.preferred_engine if task else self.default_engine
        engines = set(allowed_engines or self.allowed_engines)
        if preferred:
            engines &= {preferred}
        for model in self.models:
            if model.enabled and model.engine in engines and task_id in model.tasks:
                return model
        for model in self.models:
            if model.enabled and model.engine in set(allowed_engines or self.allowed_engines) and task_id in model.tasks:
                return model
        return None

    def as_dict(self, *, redact_secrets: bool = True) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "default_engine": self.default_engine,
            "default_model_id": self.default_model_id,
            "device": self.device,
            "allow_mock_fallback": self.allow_mock_fallback,
            "allowed_engines": list(self.allowed_engines),
            "models": [model.as_dict(redact_secrets=redact_secrets) for model in self.models],
            "tasks": {task_id: task.as_dict() for task_id, task in self.tasks.items()},
        }

    def config_hash(self) -> str:
        payload = json.dumps(self.as_dict(redact_secrets=True), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class RestrictedInferenceDiagnostic:
    reason_code: str
    severity: str = "warning"
    message: str = ""
    model_id: str = ""
    engine: str = ""
    task_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason_code": self.reason_code,
            "severity": self.severity,
            "message": self.message,
            "model_id": self.model_id,
            "engine": self.engine,
            "task_id": self.task_id,
        }


class RestrictedInferenceConfigService:
    """Normalize and diagnose the top-level ``restricted_inference`` config."""

    def __init__(self, *, global_config: dict[str, Any] | None = None) -> None:
        self._global_config = dict(global_config or {})

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "RestrictedInferenceConfig":
        return cls(global_config=config).resolve()

    def resolve(self) -> RestrictedInferenceConfig:
        raw = dict(self._global_config.get("restricted_inference") or {})
        allowed_engines = [
            str(item) for item in (raw.get("allowed_engines") or sorted(KNOWN_ENGINES))
        ]
        top = self._global_config
        # Bridge top-level embedding_* keys into restricted_inference when not set explicitly.
        if not raw.get("default_model_id") and top.get("embedding_model_id"):
            raw["default_model_id"] = top["embedding_model_id"]
        for opt_key in ("lang_detect", "lang_model_de", "lang_model_en"):
            top_key = f"embedding_{opt_key}"
            if top_key in top and opt_key not in raw:
                raw[opt_key] = top[top_key]

        default_engine = str(raw.get("default_engine") or ENGINE_SENTENCE_TRANSFORMERS)
        default_model_id = str(raw.get("default_model_id") or "paraphrase-multilingual-MiniLM-L12-v2")
        device = str(raw.get("device") or "cpu")

        # Build lang_model_map from flat keys or nested dict.
        lang_detect = bool(raw.get("lang_detect", False))
        raw_lang_map = raw.get("lang_model_map")
        if isinstance(raw_lang_map, dict):
            lang_model_map: dict[str, str] = {str(k): str(v) for k, v in raw_lang_map.items()}
        else:
            lang_model_map = {
                "de": str(raw.get("lang_model_de") or "paraphrase-multilingual-MiniLM-L12-v2"),
                "en": str(raw.get("lang_model_en") or "all-MiniLM-L6-v2"),
                "*": default_model_id,
            }

        raw_models = raw.get("models")
        if isinstance(raw_models, list) and raw_models:
            models = [RestrictedInferenceModelConfig.from_raw(item) for item in raw_models if isinstance(item, dict)]
        else:
            if default_engine == ENGINE_MOCK:
                model_name = "mock-deterministic-v1"
            elif default_engine == ENGINE_SENTENCE_TRANSFORMERS:
                model_name = default_model_id
            else:
                model_name = default_model_id
            extra_options: dict[str, Any] = {"lang_detect": lang_detect, "lang_model_map": lang_model_map}
            models = [RestrictedInferenceModelConfig(
                id=default_model_id,
                engine=default_engine,
                model=model_name,
                device=device,
                tasks=sorted(KNOWN_TASKS),
                options=extra_options,
            )]

        raw_tasks = raw.get("tasks") if isinstance(raw.get("tasks"), dict) else {}
        tasks = {
            task_id: RestrictedInferenceTaskConfig.from_raw(task_id, raw_tasks.get(task_id))
            for task_id in sorted(KNOWN_TASKS)
        }
        return RestrictedInferenceConfig(
            enabled=bool(raw.get("enabled", True)),
            default_engine=default_engine,
            default_model_id=default_model_id,
            device=device,
            allow_mock_fallback=bool(raw.get("allow_mock_fallback", True)),
            allowed_engines=allowed_engines,
            models=models,
            tasks=tasks,
        )

    def diagnostics(self, *, dependency_status: dict[str, str] | None = None) -> list[RestrictedInferenceDiagnostic]:
        cfg = self.resolve()
        dep_status = dict(dependency_status or {})
        diagnostics: list[RestrictedInferenceDiagnostic] = []
        allowed = set(cfg.allowed_engines)
        for engine in allowed:
            if engine not in KNOWN_ENGINES:
                diagnostics.append(RestrictedInferenceDiagnostic(
                    "unknown_engine", "error", f"Unknown engine configured: {engine}", engine=engine
                ))
        for task_id, task in cfg.tasks.items():
            if task.preferred_engine not in KNOWN_ENGINES:
                diagnostics.append(RestrictedInferenceDiagnostic(
                    "unknown_engine", "error", f"Unknown preferred engine: {task.preferred_engine}",
                    engine=task.preferred_engine,
                    task_id=task_id,
                ))
            if not task.enabled:
                diagnostics.append(RestrictedInferenceDiagnostic(
                    "task_disabled", "info", f"Task is disabled: {task_id}", task_id=task_id
                ))
        for model in cfg.models:
            if model.engine not in KNOWN_ENGINES:
                diagnostics.append(RestrictedInferenceDiagnostic(
                    "unknown_engine", "error", f"Unknown engine configured: {model.engine}",
                    model_id=model.id,
                    engine=model.engine,
                ))
            if model.engine not in allowed:
                diagnostics.append(RestrictedInferenceDiagnostic(
                    "engine_not_allowed", "error", f"Model engine is not allowed: {model.engine}",
                    model_id=model.id,
                    engine=model.engine,
                ))
            if not model.enabled:
                diagnostics.append(RestrictedInferenceDiagnostic(
                    "disabled_model", "info", f"Model is disabled: {model.id}",
                    model_id=model.id,
                    engine=model.engine,
                ))
            if model.local_path and not Path(model.local_path).exists():
                diagnostics.append(RestrictedInferenceDiagnostic(
                    "invalid_local_path", "error", f"Local model path does not exist: {model.local_path}",
                    model_id=model.id,
                    engine=model.engine,
                ))
            status = dep_status.get(model.engine)
            if status in {"degraded", "unavailable"}:
                diagnostics.append(RestrictedInferenceDiagnostic(
                    "missing_dependency", "warning", f"Engine dependency is {status}: {model.engine}",
                    model_id=model.id,
                    engine=model.engine,
                ))
            if model.engine in {ENGINE_HUGGINGFACE, ENGINE_SENTENCE_TRANSFORMERS} and not model.local_path:
                diagnostics.append(RestrictedInferenceDiagnostic(
                    "unsafe_external_call", "warning",
                    "Remote model id may require network access; prefer local_path for offline operation.",
                    model_id=model.id,
                    engine=model.engine,
                ))
        return diagnostics

    def as_dict(self, *, redact_secrets: bool = True) -> dict[str, Any]:
        return self.resolve().as_dict(redact_secrets=redact_secrets)


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in payload.items():
        lowered = key.lower()
        if any(part in lowered for part in ("secret", "token", "api_key", "password")):
            result[key] = "<redacted>"
        elif isinstance(value, dict):
            result[key] = _redact(value)
        else:
            result[key] = value
    return result
