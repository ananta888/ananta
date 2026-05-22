from __future__ import annotations

import os
from typing import Any

from flask import current_app, has_app_context

from agent.llm_benchmarks import load_benchmarks
from agent.model_selection import normalize_legacy_model_name


def _resolve_data_dir() -> str:
    if has_app_context():
        return str(current_app.config.get("DATA_DIR") or "data")
    return "data"


def _resolve_provider_model(*, effective_config: dict[str, Any] | None) -> tuple[str, str]:
    cfg = dict(effective_config or {})
    provider = str(cfg.get("default_provider") or cfg.get("provider") or "").strip().lower() or "lmstudio"
    model = normalize_legacy_model_name(
        str(cfg.get("default_model") or cfg.get("model") or "").strip() or "auto",
        provider=provider,
    ) or "auto"
    return provider, model


def _calibrated_timeout_from_benchmarks(
    *,
    data_dir: str,
    provider: str,
    model: str,
    task_kind: str,
    floor_seconds: int,
    ceiling_seconds: int,
) -> int | None:
    path = os.path.join(data_dir, "llm_model_benchmarks.json")
    if not os.path.exists(path):
        return None
    db = load_benchmarks(data_dir)
    model_key = f"{provider}:{model}"
    entry = ((db.get("models") or {}).get(model_key) or {})
    task_bucket = ((entry.get("task_kinds") or {}).get(task_kind) or {})
    samples = [s for s in list(task_bucket.get("samples") or []) if isinstance(s, dict)]
    if len(samples) < 3:
        return None
    latencies = sorted(max(0, int(s.get("latency_ms") or 0)) for s in samples)
    if not latencies:
        return None
    # Robust local calibration: p95 latency * 2.5 + 8s floor buffer.
    idx = max(0, min(len(latencies) - 1, int(round((len(latencies) - 1) * 0.95))))
    p95_ms = latencies[idx]
    calibrated = int((p95_ms / 1000.0) * 2.5) + 8
    return max(floor_seconds, min(calibrated, ceiling_seconds))


def resolve_propose_llm_timeout_seconds(*, effective_config: dict[str, Any] | None, task_kind: str | None) -> int:
    cfg = dict(effective_config or {})
    kind = str(task_kind or "").strip().lower()
    task_kind_policies = cfg.get("task_kind_execution_policies") if isinstance(cfg.get("task_kind_execution_policies"), dict) else {}
    task_kind_cfg = task_kind_policies.get(kind) if isinstance(task_kind_policies.get(kind), dict) else {}

    # Prefer explicit LLM timeout override, then propose timeout and command budgets.
    candidates = [
        cfg.get("llm_invoke_timeout_seconds"),
        cfg.get("task_propose_timeout_seconds"),
        cfg.get("command_timeout"),
        task_kind_cfg.get("command_timeout"),
    ]
    parsed: list[int] = []
    for value in candidates:
        try:
            if value is None:
                continue
            parsed.append(int(value))
        except (TypeError, ValueError):
            continue
    base_timeout = 120 if not parsed else max(parsed)
    floor = max(30, min(base_timeout, 1200))

    provider, model = _resolve_provider_model(effective_config=cfg)
    calibrated = _calibrated_timeout_from_benchmarks(
        data_dir=_resolve_data_dir(),
        provider=provider,
        model=model,
        task_kind=(kind or "analysis"),
        floor_seconds=floor,
        ceiling_seconds=1200,
    )
    if calibrated is not None:
        return calibrated
    return floor
