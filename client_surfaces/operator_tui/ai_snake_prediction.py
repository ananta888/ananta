from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from client_surfaces.operator_tui.ai_snake_observation import ObservationEvent


@dataclass(frozen=True)
class QuickPrediction:
    predicted_intent: str
    target_ref: str
    confidence: float
    evidence_event_ids: list[str]
    expires_at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "predicted_intent": self.predicted_intent,
            "target_ref": self.target_ref,
            "confidence": self.confidence,
            "evidence_event_ids": list(self.evidence_event_ids),
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class GateDecision:
    allow_worker_request: bool
    reason: str
    skipped_worker_requests: int


def build_prediction_trace(
    *,
    mode: str,
    prediction: QuickPrediction,
    context_hash: str,
    used_refs: list[dict[str, Any]] | list[str],
    provider_ref: str,
    cache_hit: bool,
    skipped_reason: str,
) -> dict[str, Any]:
    """Create a compact trace record without raw chat/notes content."""
    refs: list[str] = []
    for item in list(used_refs or [])[:12]:
        if isinstance(item, dict):
            ref = str(item.get("ref") or "").strip()
        else:
            ref = str(item).strip()
        if ref:
            refs.append(ref)
    trace_id = f"pred-{int(time.time() * 1000)}-{abs(hash((mode, prediction.predicted_intent, context_hash))) % 10000:04d}"
    return {
        "prediction_id": trace_id,
        "mode": str(mode or "predict_intent"),
        "confidence": float(prediction.confidence),
        "context_hash": str(context_hash or "missing"),
        "used_refs": refs,
        "provider_ref": str(provider_ref or "local_quick"),
        "cache_state": "hit" if cache_hit else "miss",
        "skipped_reason": str(skipped_reason or ""),
    }


class PredictionGate:
    def __init__(self, *, min_interval_seconds: float = 3.0, min_confidence: float = 0.35, stable_ms: int = 500) -> None:
        self.min_interval_seconds = max(0.5, float(min_interval_seconds))
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self.stable_ms = max(100, int(stable_ms))
        self.last_request_at = 0.0
        self.last_signature = ""
        self.last_signature_seen_at = 0.0
        self.skipped_worker_requests = 0

    def evaluate(
        self,
        *,
        prediction: QuickPrediction,
        signature: str,
        now: float | None = None,
        selected_artifact: bool = False,
        force_for_chat: bool = False,
    ) -> GateDecision:
        ts = time.time() if now is None else float(now)
        if not force_for_chat and prediction.confidence < self.min_confidence:
            self.skipped_worker_requests += 1
            return GateDecision(False, "confidence_below_threshold", self.skipped_worker_requests)

        if signature != self.last_signature:
            self.last_signature = signature
            self.last_signature_seen_at = ts

        stable_for_ms = int(max(0.0, ts - self.last_signature_seen_at) * 1000.0)
        if not selected_artifact and stable_for_ms < self.stable_ms and not force_for_chat:
            self.skipped_worker_requests += 1
            return GateDecision(False, "prediction_not_stable", self.skipped_worker_requests)

        if (ts - self.last_request_at) < self.min_interval_seconds and not force_for_chat:
            self.skipped_worker_requests += 1
            return GateDecision(False, "rate_limited", self.skipped_worker_requests)

        self.last_request_at = ts
        return GateDecision(True, "allowed", self.skipped_worker_requests)


def quick_predict(events: list[ObservationEvent], *, now: float | None = None, ttl_seconds: int = 20) -> QuickPrediction:
    ts = time.time() if now is None else float(now)
    ttl = max(5, min(120, int(ttl_seconds)))
    if not events:
        return QuickPrediction("unknown", "", 0.2, [], ts + ttl)

    section = ""
    channel = ""
    artifact_ref = ""
    movement = ""
    evidence_ids: list[str] = []

    for event in reversed(events):
        if event.kind == "section" and not section:
            section = event.normalized_value
            evidence_ids.append(event.event_id)
        elif event.kind == "chat_channel" and not channel:
            channel = event.normalized_value
            evidence_ids.append(event.event_id)
        elif event.kind in {"artifact", "target_ref"} and not artifact_ref:
            artifact_ref = event.normalized_value
            evidence_ids.append(event.event_id)
        elif event.kind == "movement" and not movement:
            movement = event.normalized_value
            evidence_ids.append(event.event_id)
        if len(evidence_ids) >= 6:
            break

    intent = "unknown"
    target_ref = ""
    confidence = 0.35

    if artifact_ref or "artifact" in section:
        intent = "artifact_explain"
        target_ref = artifact_ref or "artifact:list"
        confidence = 0.68 if artifact_ref else 0.55
    elif "notes" in section:
        intent = "notes"
        target_ref = "notes:self"
        confidence = 0.58
    elif "config" in section or "settings" in section:
        intent = "config"
        target_ref = section or "settings"
        confidence = 0.52
    elif channel.startswith("ai:") or "chat" in section:
        intent = "chat"
        target_ref = channel or "ai:tutor"
        confidence = 0.6
    elif movement in {"left", "right", "up", "down"}:
        intent = "navigate"
        target_ref = movement
        confidence = 0.42

    if len(evidence_ids) <= 1:
        confidence = min(confidence, 0.69)
    if len(evidence_ids) >= 4 and intent != "unknown":
        confidence = min(0.9, confidence + 0.1)

    return QuickPrediction(
        predicted_intent=intent,
        target_ref=target_ref,
        confidence=round(max(0.0, min(1.0, confidence)), 3),
        evidence_event_ids=evidence_ids,
        expires_at=ts + ttl,
    )
