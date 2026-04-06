from __future__ import annotations

import json
import logging
import os
import time
from copy import deepcopy
from json import JSONDecodeError
from typing import Any

import requests


OLLAMA_BENCH_TASK_KINDS = {"planning", "research", "coding", "review", "testing", "ops", "analysis", "doc"}


logger = logging.getLogger(__name__)


class OllamaBenchmarkDataError(RuntimeError):
    """Raised when persisted Ollama benchmark data cannot be parsed safely."""


def _deep_merge_dicts(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _load_json_dict(
    *,
    path: str,
    default: dict[str, Any],
    label: str,
    merge_with_default: bool,
) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
    except FileNotFoundError:
        return deepcopy(default)
    except JSONDecodeError as exc:
        logger.warning("Invalid %s JSON at %s: %s", label, path, exc)
        raise OllamaBenchmarkDataError(f"invalid_{label}_json") from exc
    except OSError as exc:
        logger.warning("Failed reading %s at %s: %s", label, path, exc)
        raise OllamaBenchmarkDataError(f"unreadable_{label}_json") from exc

    if not isinstance(loaded, dict):
        logger.warning("Expected %s JSON object at %s but got %s", label, path, type(loaded).__name__)
        raise OllamaBenchmarkDataError(f"invalid_{label}_shape")

    if merge_with_default:
        return _deep_merge_dicts(default, loaded)
    return loaded


def merge_ollama_bench_config(current_cfg: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    filtered_updates = {key: value for key, value in updates.items() if key in current_cfg}
    return _deep_merge_dicts(current_cfg, filtered_updates)


SCRUM_ROLE_TEMPLATES = {
    "planner": {
        "description": "Erstellt Projektpläne und zerlegt Aufgaben",
        "task_kind": "planning",
        "test_prompts": [
            "Erstelle einen detaillierten Projektplan für eine E-Commerce-Webanwendung mit Benutzer-Authentifizierung, Produktkatalog und Warenkorb. Berücksichtige Sprint-Struktur und Abhängigkeiten.",
            "Zerlege die Aufgabe 'Online-Bestellsystem implementieren' in kleinere Arbeitspakete mit Schätzungen.",
            "Plane die Migration einer monolithischen Node.js-Anwendung zu Microservices mit Kubernetes.",
        ],
    },
    "researcher": {
        "description": "Recherchiert Technologien und bewertet Optionen",
        "task_kind": "research",
        "test_prompts": [
            "Recherchiere die Vor- und Nachteile von GraphQL vs REST APIs für eine E-Commerce-Plattform.",
            "Welche Security-Best-Practices gelten für JWT-basierte Authentifizierung in SPAs?",
            "Vergleiche PostgreSQL vs MongoDB für ein Content-Management-System mit strukturierten und unstrukturierten Daten.",
        ],
    },
    "coder": {
        "description": "Implementiert Code und Features",
        "task_kind": "coding",
        "test_prompts": [
            "Schreibe eine Python-Funktion, die einen Binary-Search-Tree mit Einufgen, Löschen und Suchen implementiert. Incl. Tests.",
            "Erstelle eine REST-API mit FastAPI für eine Todo-Liste mit CRUD-Operationen, Authentifizierung und PostgreSQL-Anbindung.",
            "Implementiere einen einfachen Rate-Limiter in TypeScript für Express.js mit Token-Bucket-Algorithmus.",
        ],
    },
    "reviewer": {
        "description": "Reviews Code und gibt Feedback",
        "task_kind": "review",
        "test_prompts": [
            "Review folgenden Python-Code auf Security-Probleme:\n\ndef login(username, password):\n    query = f\"SELECT * FROM users WHERE username='{username}' AND password='{password}'\"\n    return db.execute(query)",
            "Analysiere diese Architektur-Entscheidung: Microservices vs Monolith für ein Startup mit 5 Entwicklern. Was sind Vor-/Nachteile?",
            "Review diesen GitHub Actions Workflow auf Performance und Best Practices.",
        ],
    },
    "tester": {
        "description": "Erstellt Tests und verifiziert Qualität",
        "task_kind": "testing",
        "test_prompts": [
            "Erstelle eine umfassende Teststrategie für eine REST-API mit 20 Endpoints. Inkl. Unit-, Integration- und E2E-Tests.",
            "Schreibe pytest-Tests für eine User-Registrierungs-Funktion mit Happy Path, Edge Cases und Fehlerbehandlung.",
            "Erstelle einen Testplan für die Migration einer Datenbank mit Zero-Downtime.",
        ],
    },
    "devops": {
        "description": "Kümmert sich um CI/CD und Infrastruktur",
        "task_kind": "ops",
        "test_prompts": [
            "Erstelle ein optimales Dockerfile für eine Node.js/Express-Anwendung mit Multi-Stage-Build und Security-Best-Practices.",
            "Schreibe ein GitHub Actions CI/CD Pipeline für ein Python-Projekt mit Unit-Tests, Integration-Tests und Deployment.",
            "Entwirf eine Kubernetes-Konfiguration für eine skalierbare Webanwendung mit HPA, Ingress und Persistent Storage.",
        ],
    },
    "architect": {
        "description": "Designt Systemarchitektur und Technologie-Stack",
        "task_kind": "analysis",
        "test_prompts": [
            "Entwirf die Architektur für ein Real-Time-Kollaborations-Tool wie Figma oder Miro. Berücksichtige WebSockets, CRDTs und horizontale Skalierung.",
            "Schlage einen Tech-Stack für ein MVP eines SaaS-Produkts vor mit Budget-Constraints und Zeitanforderungen.",
            "Design ein Event-Driven-Architecture-Pattern für ein FinTech-Backend mit Audit-Trail-Anforderungen.",
        ],
    },
    "scrum_master": {
        "description": "Moderiert Sprint-Aktivitäten und Prozesse",
        "task_kind": "planning",
        "test_prompts": [
            "Formuliere 5 effektive Sprint-Retrospektive-Fragen für ein Team mit Kommunikationsproblemen.",
            "Erstelle einen Sprint-Burndown-Chart-Plan für einen 2-Wochen-Sprint mit 40 Story Points und realistischen Annahmen.",
            "Wie würdest du einem Team helfen, das seit 3 Sprints übermäßig viel technische Schulden angesammelt hat?",
        ],
    },
}

DEFAULT_OLLAMA_BENCH_CONFIG = {
    "enabled": True,
    "provider": "ollama",
    "ollama_url": "http://ollama:11434",
    "timeout": 120,
    "auto_discover_models": True,
    "parameter_variations": {
        "temperature": [0.1, 0.5, 0.8, 1.0],
        "top_p": [0.5, 0.9, 0.95, 1.0],
        "top_k": [20, 40, 80],
    },
    "scoring": {
        "weights": {
            "success_rate": 0.40,
            "quality_rate": 0.35,
            "latency_score": 0.15,
            "cost_score": 0.10,
        },
        "thresholds": {
            "min_samples_per_config": 1,
            "min_success_rate": 0.3,
        },
    },
    "retention": {
        "max_samples_per_model": 500,
        "max_days": 90,
    },
    "external_providers": {"enabled": False, "providers": ["openai", "anthropic"], "api_key_required": True},
    "role_benchmarks": SCRUM_ROLE_TEMPLATES,
}


def ollama_bench_config_path(data_dir: str) -> str:
    return os.path.join(data_dir, "ollama_benchmark_config.json")


def ollama_bench_results_path(data_dir: str) -> str:
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "ollama_benchmark_results.json")


def load_ollama_bench_config(data_dir: str) -> dict[str, Any]:
    path = ollama_bench_config_path(data_dir)
    return _load_json_dict(
        path=path,
        default=DEFAULT_OLLAMA_BENCH_CONFIG,
        label="ollama_benchmark_config",
        merge_with_default=True,
    )


def save_ollama_bench_config(data_dir: str, config: dict[str, Any]) -> None:
    path = ollama_bench_config_path(data_dir)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)


def load_ollama_bench_results(data_dir: str) -> dict[str, Any]:
    path = ollama_bench_results_path(data_dir)
    return _load_json_dict(
        path=path,
        default={"models": {}, "updated_at": None, "last_benchmark_run": None},
        label="ollama_benchmark_results",
        merge_with_default=False,
    )


def save_ollama_bench_results(data_dir: str, data: dict[str, Any]) -> None:
    path = ollama_bench_results_path(data_dir)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def get_scrum_role_templates() -> dict[str, dict[str, Any]]:
    return SCRUM_ROLE_TEMPLATES


def get_role_template_names() -> list[str]:
    return list(SCRUM_ROLE_TEMPLATES.keys())


def discover_ollama_models(ollama_url: str, timeout: int = 10) -> list[dict[str, Any]]:
    normalized = ollama_url.rstrip("/")
    tags_url = f"{normalized}/api/tags"
    try:
        response = requests.get(tags_url, timeout=timeout)
        if response.status_code == 200:
            payload = response.json()
            models = payload.get("models", [])
            return [
                {
                    "name": m.get("name", ""),
                    "model": m.get("name", ""),
                    "size": m.get("size"),
                    "modified_at": m.get("modified_at"),
                    "digest": m.get("digest"),
                }
                for m in models
                if m.get("name")
            ]
    except requests.RequestException as exc:
        logger.warning("Failed discovering Ollama models via %s: %s", tags_url, exc)
    except ValueError as exc:
        logger.warning("Invalid Ollama model discovery payload from %s: %s", tags_url, exc)
    return []


def discover_active_ollama_models(ollama_url: str, timeout: int = 5) -> list[str]:
    normalized = ollama_url.rstrip("/")
    ps_url = f"{normalized}/api/ps"
    try:
        response = requests.get(ps_url, timeout=timeout)
        if response.status_code == 200:
            payload = response.json()
            return [m.get("name", "") for m in payload.get("models", []) if m.get("name")]
    except requests.RequestException as exc:
        logger.warning("Failed discovering active Ollama models via %s: %s", ps_url, exc)
    except ValueError as exc:
        logger.warning("Invalid Ollama active-model payload from %s: %s", ps_url, exc)
    return []


def default_ollama_metric_bucket() -> dict[str, Any]:
    return {
        "total": 0,
        "success": 0,
        "failed": 0,
        "quality_pass": 0,
        "quality_fail": 0,
        "latency_ms_total": 0,
        "tokens_total": 0,
        "cost_units_total": 0.0,
        "last_seen": None,
    }


def score_ollama_bucket(bucket: dict[str, Any], weights: dict[str, float] | None = None) -> dict[str, Any]:
    weights = weights or {"success_rate": 0.40, "quality_rate": 0.35, "latency_score": 0.15, "cost_score": 0.10}
    total = max(0, int(bucket.get("total") or 0))
    success = max(0, int(bucket.get("success") or 0))
    quality_pass = max(0, int(bucket.get("quality_pass") or 0))
    latency_ms_total = max(0, int(bucket.get("latency_ms_total") or 0))
    tokens_total = max(0, int(bucket.get("tokens_total") or 0))
    cost_units_total = max(0.0, float(bucket.get("cost_units_total") or 0.0))
    success_rate = (success / total) if total else 0.0
    quality_rate = (quality_pass / total) if total else 0.0
    avg_latency_ms = (latency_ms_total / total) if total else 0.0
    avg_tokens = (tokens_total / total) if total else 0.0
    avg_cost_units = (cost_units_total / total) if total else 0.0
    latency_score = max(0.0, min(1.0, 1.0 - (avg_latency_ms / 120000.0)))
    cost_score = max(0.0, min(1.0, 1.0 - (avg_cost_units / 0.001)))
    suitability_score = round(
        (
            weights.get("success_rate", 0.40) * success_rate
            + weights.get("quality_rate", 0.35) * quality_rate
            + weights.get("latency_score", 0.15) * latency_score
            + weights.get("cost_score", 0.10) * cost_score
        )
        * 100.0,
        2,
    )
    return {
        "total": total,
        "success_rate": round(success_rate, 4),
        "quality_rate": round(quality_rate, 4),
        "avg_latency_ms": round(avg_latency_ms, 2),
        "avg_tokens": round(avg_tokens, 2),
        "avg_cost_units": round(avg_cost_units, 6),
        "suitability_score": suitability_score,
    }


def record_ollama_benchmark_sample(
    *,
    data_dir: str,
    model: str,
    role_name: str,
    task_kind: str,
    parameters: dict[str, Any],
    success: bool,
    quality_gate_passed: bool,
    latency_ms: int,
    tokens_total: int,
    cost_units: float = 0.0,
    response_text: str | None = None,
) -> dict[str, Any]:
    model = str(model or "").strip()
    role_name = str(role_name or "").strip().lower()
    task_kind = str(task_kind or "analysis").strip().lower()
    if not model:
        return {"recorded": False}
    cfg = load_ollama_bench_config(data_dir)
    retention = cfg.get("retention", {})
    max_samples = int(retention.get("max_samples_per_model", 500))
    max_days = int(retention.get("max_days", 90))
    db = load_ollama_bench_results(data_dir)
    models = db.setdefault("models", {})
    model_key = model
    entry = models.setdefault(
        model_key,
        {
            "model": model,
            "overall": default_ollama_metric_bucket(),
            "roles": {},
            "parameters": {},
        },
    )
    role_bucket = (entry.setdefault("roles", {})).setdefault(role_name, default_ollama_metric_bucket())
    param_key = _parameters_key(parameters)
    param_bucket = (entry.setdefault("parameters", {}).setdefault(param_key, {})).setdefault(
        role_name, default_ollama_metric_bucket()
    )
    now = int(time.time())
    min_ts = now - (max_days * 86400)

    def _apply(target: dict[str, Any], sample_data: dict | None = None) -> None:
        target["total"] = int(target.get("total") or 0) + 1
        target["success"] = int(target.get("success") or 0) + (1 if success else 0)
        target["failed"] = int(target.get("failed") or 0) + (0 if success else 1)
        target["quality_pass"] = int(target.get("quality_pass") or 0) + (1 if quality_gate_passed else 0)
        target["quality_fail"] = int(target.get("quality_fail") or 0) + (0 if quality_gate_passed else 1)
        target["latency_ms_total"] = int(target.get("latency_ms_total") or 0) + max(0, int(latency_ms or 0))
        target["tokens_total"] = int(target.get("tokens_total") or 0) + max(0, int(tokens_total or 0))
        target["cost_units_total"] = float(target.get("cost_units_total") or 0.0) + float(cost_units or 0.0)
        target["last_seen"] = now
        samples = target.setdefault("samples", [])
        if not isinstance(samples, list):
            samples = []
            target["samples"] = samples
        else:
            samples[:] = [s for s in samples if int((s or {}).get("ts") or 0) >= min_ts]
        sample = {
            "ts": now,
            "role_name": role_name,
            "task_kind": task_kind,
            "parameters": dict(parameters),
            "success": bool(success),
            "quality_passed": bool(quality_gate_passed),
            "latency_ms": max(0, int(latency_ms or 0)),
            "tokens_total": max(0, int(tokens_total or 0)),
            "cost_units": max(0.0, float(cost_units or 0.0)),
            "response_length": len(response_text) if response_text else 0,
        }
        samples.append(sample)
        if len(samples) > max_samples:
            del samples[: len(samples) - max_samples]

    _apply(role_bucket)
    _apply(param_bucket)
    _apply(entry.setdefault("overall", default_ollama_metric_bucket()))
    db["updated_at"] = now
    save_ollama_bench_results(data_dir, db)
    return {"recorded": True, "model": model, "role_name": role_name, "db": db}


def _parameters_key(parameters: dict[str, Any]) -> str:
    parts = []
    for k in sorted(parameters.keys()):
        v = parameters[k]
        parts.append(f"{k}={v}")
    return "|".join(parts)


def recommend_ollama_model(
    *,
    data_dir: str,
    role_name: str | None = None,
    task_kind: str | None = None,
    min_samples: int = 1,
    exclude_models: list[str] | None = None,
    preferred_parameters: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    ranked = recommend_ollama_models(
        data_dir=data_dir,
        role_name=role_name,
        task_kind=task_kind,
        min_samples=min_samples,
        limit=1,
        exclude_models=exclude_models,
        preferred_parameters=preferred_parameters,
    )
    if not ranked:
        return None
    best = dict(ranked[0] or {})
    best["selection_source"] = "ollama_benchmark"
    return best


def recommend_ollama_models(
    *,
    data_dir: str,
    role_name: str | None = None,
    task_kind: str | None = None,
    min_samples: int = 1,
    limit: int = 5,
    exclude_models: list[str] | None = None,
    preferred_parameters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    role_match = str(role_name or "").strip().lower()
    excluded = {str(item or "").strip() for item in list(exclude_models or []) if str(item or "").strip()}
    cfg = load_ollama_bench_config(data_dir)
    weights = (cfg.get("scoring", {}) or {}).get("weights", {})
    db = load_ollama_bench_results(data_dir)
    candidates: list[dict[str, Any]] = []

    for model_key, entry in db.get("models", {}).items():
        if not isinstance(entry, dict):
            continue
        model = str(entry.get("model") or "").strip()
        if not model or model in excluded:
            continue
        if role_match:
            role_data = (entry.get("roles") or {}).get(role_match) or {}
        else:
            role_data = entry.get("overall") or {}
        samples = list(role_data.get("samples", [])) if isinstance(role_data, dict) else []
        if not samples:
            continue
        filtered = []
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            if role_match and str(sample.get("role_name") or "").strip().lower() != role_match:
                continue
            if task_kind and str(sample.get("task_kind") or "").strip().lower() != task_kind:
                continue
            if preferred_parameters:
                sample_params = sample.get("parameters", {})
                if not all(sample_params.get(k) == v for k, v in preferred_parameters.items()):
                    continue
            filtered.append(sample)
        if len(filtered) < max(1, int(min_samples or 1)):
            continue
        aggregate = {
            "total": len(filtered),
            "success": sum(1 for s in filtered if bool(s.get("success"))),
            "quality_pass": sum(1 for s in filtered if bool(s.get("quality_passed"))),
            "latency_ms_total": sum(max(0, int(s.get("latency_ms") or 0)) for s in filtered),
            "tokens_total": sum(max(0, int(s.get("tokens_total") or 0)) for s in filtered),
            "cost_units_total": sum(max(0.0, float(s.get("cost_units") or 0.0)) for s in filtered),
        }
        scored = score_ollama_bucket(aggregate, weights)
        param_stats = _aggregate_by_parameters(filtered)
        candidate = {
            "model": model,
            "role_name": role_match or None,
            "task_kind": task_kind,
            "sample_count": aggregate["total"],
            "score": scored,
            "parameter_performance": param_stats,
        }
        candidates.append(candidate)

    candidates.sort(key=lambda item: float(((item.get("score") or {}).get("suitability_score") or 0.0)), reverse=True)
    capped = max(1, min(int(limit or 1), 20))
    return candidates[:capped]


def _aggregate_by_parameters(samples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_params: dict[str, list[dict]] = {}
    for sample in samples:
        params = sample.get("parameters", {})
        key = _parameters_key(params)
        if key not in by_params:
            by_params[key] = []
        by_params[key].append(sample)
    result = {}
    for key, group in by_params.items():
        total = len(group)
        success = sum(1 for s in group if bool(s.get("success")))
        quality_pass = sum(1 for s in group if bool(s.get("quality_passed")))
        avg_latency = sum(s.get("latency_ms", 0) for s in group) / total if total else 0
        result[key] = {
            "parameters": group[0].get("parameters", {}),
            "total": total,
            "success_rate": round(success / total, 4) if total else 0,
            "quality_rate": round(quality_pass / total, 4) if total else 0,
            "avg_latency_ms": round(avg_latency, 2),
        }
    return result


def ollama_benchmark_rows(
    *,
    data_dir: str,
    role_name: str | None = None,
    model_name: str | None = None,
    top_n: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    role_match = str(role_name or "").strip().lower()
    model_match = str(model_name or "").strip().lower()
    cfg = load_ollama_bench_config(data_dir)
    weights = (cfg.get("scoring", {}) or {}).get("weights", {})
    db = load_ollama_bench_results(data_dir)
    rows: list[dict[str, Any]] = []

    for model_key, entry in db.get("models", {}).items():
        if not isinstance(entry, dict):
            continue
        model = str(entry.get("model") or "").strip()
        if model_match and model.lower() != model_match:
            continue
        overall = score_ollama_bucket(entry.get("overall") or {}, weights)
        row = {
            "model": model,
            "overall": overall,
            "roles": {},
            "parameter_count": len(entry.get("parameters", {})),
        }
        for role, role_data in entry.get("roles", {}).items():
            row["roles"][role] = score_ollama_bucket(role_data or {}, weights)
        if role_match and role_match in row["roles"]:
            row["focus"] = row["roles"][role_match]
        else:
            row["focus"] = overall
        row["_sort_score"] = float((row["focus"] or {}).get("suitability_score") or 0.0)
        rows.append(row)

    rows.sort(key=lambda item: item.get("_sort_score") or 0.0, reverse=True)
    if isinstance(top_n, int) and top_n > 0:
        rows = rows[:top_n]
    for row in rows:
        row.pop("_sort_score", None)
    return rows, db


def get_best_parameters_for_model(
    *,
    data_dir: str,
    model: str,
    role_name: str | None = None,
) -> dict[str, Any] | None:
    db = load_ollama_bench_results(data_dir)
    entry = db.get("models", {}).get(model, {})
    parameters = entry.get("parameters", {})
    if not parameters:
        return None
    best_key = None
    best_score = -1
    best_params = None
    for param_key, roles_data in parameters.items():
        target = roles_data.get(role_name) if role_name else None
        if not target:
            continue
        score = score_ollama_bucket(target)
        if score.get("suitability_score", 0) > best_score:
            best_score = score.get("suitability_score", 0)
            best_key = param_key
            best_params = score
    if not best_params:
        return None
    param_entry = parameters.get(best_key, {})
    target_entry = param_entry.get(role_name) if role_name else param_entry
    return {
        "model": model,
        "role_name": role_name,
        "parameters": dict((target_entry or {}).get("samples", [{}])[-1].get("parameters") or {}),
        "score": best_params,
        "sample_count": target_entry.get("total", 0) if target_entry else 0,
    }


def update_ollama_benchmark_run_timestamp(data_dir: str, timestamp: int | None = None) -> None:
    db = load_ollama_bench_results(data_dir)
    db["last_benchmark_run"] = timestamp or int(time.time())
    save_ollama_bench_results(data_dir, db)
