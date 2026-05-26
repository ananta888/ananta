from __future__ import annotations

import json
import re
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

DispatchFn = Callable[[dict[str, Any]], dict[str, Any]]

_FENCE_RX = re.compile(r"```(?:json)?\s*([\s\S]+?)\s*```", re.IGNORECASE)


@dataclass
class WorkerTask:
    request_id: str
    mode: str
    submitted_at: float
    timeout_seconds: float
    future: Future[dict[str, Any]]
    cancellation_reason: str = ""


class AiSnakeWorkerClient:
    def __init__(
        self,
        *,
        dispatch: DispatchFn | None = None,
        timeout_seconds: float = 2.5,
        max_workers: int = 2,
    ) -> None:
        self._dispatch = dispatch or _default_dispatch
        self._executor = ThreadPoolExecutor(max_workers=max(1, int(max_workers)), thread_name_prefix="ai-snake-worker")
        self._timeout_seconds = max(0.2, float(timeout_seconds))
        self._lock = threading.Lock()
        self._active_predict: WorkerTask | None = None
        self._active_explain_chat: WorkerTask | None = None

    def build_request(
        self,
        *,
        mode: str,
        observation_summary: dict[str, Any],
        quick_prediction: dict[str, Any],
        context_envelope_ref: dict[str, Any],
        output_contract: str = "ai_snake_response.v1",
        token_budget: int = 1024,
        max_latency_ms: int = 2000,
    ) -> dict[str, Any]:
        return {
            "request_id": f"ai-snake-{uuid.uuid4()}",
            "mode": str(mode),
            "observation_summary": dict(observation_summary or {}),
            "quick_prediction": dict(quick_prediction or {}),
            "context_envelope_ref": dict(context_envelope_ref or {}),
            "output_contract": str(output_contract),
            "budget": {
                "token_budget": int(max(64, token_budget)),
                "max_latency_ms": int(max(250, max_latency_ms)),
            },
        }

    def submit(self, payload: dict[str, Any]) -> WorkerTask | None:
        mode = str(payload.get("mode") or "")
        timeout_seconds = max(self._timeout_seconds, float(payload.get("budget", {}).get("max_latency_ms", 2000)) / 1000.0)
        task = WorkerTask(
            request_id=str(payload.get("request_id") or f"ai-snake-{uuid.uuid4()}"),
            mode=mode,
            submitted_at=time.time(),
            timeout_seconds=timeout_seconds,
            future=self._executor.submit(self._dispatch, dict(payload)),
        )
        with self._lock:
            if mode == "predict_intent":
                if self._active_predict and not self._active_predict.future.done():
                    task.future.cancel()
                    return None
                self._active_predict = task
            elif mode in {"explain_artifact", "answer_chat"}:
                if self._active_explain_chat and not self._active_explain_chat.future.done():
                    task.future.cancel()
                    return None
                self._active_explain_chat = task
        return task

    def cancel_pending_predict(self, *, reason: str) -> bool:
        with self._lock:
            task = self._active_predict
            if task is None or task.future.done():
                return False
            task.cancellation_reason = str(reason)
            return task.future.cancel()

    def poll(self, task: WorkerTask, *, now: float | None = None) -> dict[str, Any] | None:
        ts = time.time() if now is None else float(now)
        if task.future.done():
            try:
                raw = task.future.result()
            except Exception as exc:
                return {"status": "degraded", "error": str(exc), "request_id": task.request_id}
            parsed = parse_worker_response(raw)
            parsed["request_id"] = task.request_id
            return parsed
        if (ts - task.submitted_at) > task.timeout_seconds:
            task.future.cancel()
            return {"status": "degraded", "error": "timeout", "request_id": task.request_id}
        return None


def parse_worker_response(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"status": "degraded", "error": "invalid_response_type"}

    payload = raw
    if "response_text" in raw and isinstance(raw.get("response_text"), str):
        payload = _parse_json_payload(raw.get("response_text", ""))
        if not isinstance(payload, dict):
            return {"status": "degraded", "error": "invalid_json_response"}

    confidence = payload.get("confidence")
    if not isinstance(confidence, (int, float)) or confidence < 0.0 or confidence > 1.0:
        return {"status": "degraded", "error": "confidence_out_of_range"}

    required = ("predicted_intent", "target_ref", "answer_text", "expires_at")
    for key in required:
        if key not in payload:
            return {"status": "degraded", "error": f"missing_field:{key}"}

    return {
        "status": "ok",
        "predicted_intent": str(payload.get("predicted_intent") or "unknown"),
        "confidence": float(confidence),
        "target_ref": str(payload.get("target_ref") or ""),
        "answer_text": str(payload.get("answer_text") or ""),
        "context_refs": list(payload.get("context_refs") or []),
        "follow_mode_update": str(payload.get("follow_mode_update") or ""),
        "expires_at": float(payload.get("expires_at")),
        "recommended_action": str(payload.get("recommended_action") or ""),
    }


def repair_and_parse_response(raw_text: str, *, repair_fn: Callable[[str], str] | None = None) -> dict[str, Any]:
    parsed = _parse_json_payload(raw_text)
    if isinstance(parsed, dict):
        return parse_worker_response(parsed)
    if repair_fn is None:
        return {"status": "degraded", "error": "invalid_json_response"}
    repaired = repair_fn(raw_text)
    second = _parse_json_payload(repaired)
    if not isinstance(second, dict):
        return {"status": "degraded", "error": "invalid_json_after_repair"}
    return parse_worker_response(second)


def _parse_json_payload(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        match = _FENCE_RX.search(text)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(1).strip())
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


def _default_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    quick = payload.get("quick_prediction") if isinstance(payload.get("quick_prediction"), dict) else {}
    return {
        "predicted_intent": str(quick.get("predicted_intent") or "unknown"),
        "confidence": float(quick.get("confidence") or 0.4),
        "target_ref": str(quick.get("target_ref") or ""),
        "answer_text": "Local fallback worker response.",
        "context_refs": [],
        "follow_mode_update": "lurking_follow",
        "recommended_action": "",
        "expires_at": now + 20.0,
    }
