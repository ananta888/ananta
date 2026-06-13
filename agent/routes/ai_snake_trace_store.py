"""AI-Snake Chat Trace Store — in-memory storage for reply-generation trace events."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from typing import Any

# ── Redaction ─────────────────────────────────────────────────────────────────

# Each tuple: (pattern, replacement) where replacement keeps non-secret prefix
_REDACT_SUBS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'(Bearer\s+)[A-Za-z0-9\-._~+/]+=*', re.IGNORECASE), r'\1[REDACTED]'),
    (re.compile(r'(api[_-]?key["\s]*[=:]\s*)[\w\-]{8,}', re.IGNORECASE), r'\1[REDACTED]'),
    (re.compile(r'(password["\s]*[=:]\s*)[^\s,}"\':]{4,}', re.IGNORECASE), r'\1[REDACTED]'),
    (re.compile(r'(secret["\s]*[=:]\s*)[^\s,}"\':]{4,}', re.IGNORECASE), r'\1[REDACTED]'),
    (re.compile(r'(private[_\s]key["\s]*[=:]\s*)[^\s,}"\':]{4,}', re.IGNORECASE), r'\1[REDACTED]'),
    (re.compile(r'(cookie["\s]*[=:]\s*)[^\s,}"\':]{4,}', re.IGNORECASE), r'\1[REDACTED]'),
    (re.compile(r'(X-Ananta-User-Authorization["\s:]+)\S+', re.IGNORECASE), r'\1[REDACTED]'),
]


def _redact_str(text: str, applied: list[bool]) -> str:
    out = text
    for pat, repl in _REDACT_SUBS:
        new = pat.sub(repl, out)
        if new != out:
            applied[0] = True
        out = new
    return out


def redact_value(value: Any, max_chars: int = 4000) -> tuple[Any, bool]:
    """Redact secrets from a value and truncate long strings. Returns (value, redaction_applied)."""
    applied = [False]
    if isinstance(value, str):
        out = _redact_str(value, applied)
        if len(out) > max_chars:
            out = out[:max_chars] + f"… [{len(value)} Zeichen]"
        return out, applied[0]
    if isinstance(value, (dict, list)):
        try:
            serialized = json.dumps(value, ensure_ascii=False, default=str)
            redacted = _redact_str(serialized, applied)
            if len(redacted) > max_chars:
                redacted = redacted[:max_chars] + f"… [{len(serialized)} Zeichen]"
                return redacted, True
            return json.loads(redacted), applied[0]
        except Exception:
            return str(value)[:max_chars], False
    return value, False


# ── TraceStore ────────────────────────────────────────────────────────────────


class TraceStore:
    """Thread-safe in-memory store for AI-Snake trace events."""

    def __init__(
        self,
        max_traces: int = 50,
        max_events_per_trace: int = 500,
        ttl_seconds: int = 86400,
        max_preview_chars: int = 4000,
    ) -> None:
        self._lock = threading.Lock()
        self._traces: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self.max_traces = max_traces
        self.max_events_per_trace = max_events_per_trace
        self.ttl_seconds = ttl_seconds
        self.max_preview_chars = max_preview_chars

    def new_trace(
        self,
        *,
        snake_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        trace_id = str(uuid.uuid4())
        now = time.time()
        with self._lock:
            self._evict_old()
            self._traces[trace_id] = {
                "trace_id": trace_id,
                "snake_id": snake_id,
                "session_id": session_id,
                "status": "running",
                "created_at": now,
                "updated_at": now,
                "finished_at": None,
                "events": [],
            }
            self._order.append(trace_id)
        return trace_id

    def add_event(self, trace_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                return
            events: list[dict[str, Any]] = trace["events"]
            if len(events) >= self.max_events_per_trace:
                return
            event["seq"] = len(events)
            events.append(event)
            trace["updated_at"] = time.time()

    def complete_trace(self, trace_id: str, *, status: str = "completed") -> None:
        with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                return
            trace["status"] = status
            trace["finished_at"] = time.time()
            trace["updated_at"] = trace["finished_at"]

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                return None
            return self._public_trace_locked(trace)

    def list_traces(self, *, snake_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            traces = list(self._traces.values())
            result = [self._public_trace_locked(t) for t in traces]
        if snake_id:
            result = [t for t in result if t.get("snake_id") == snake_id]
        result.sort(key=lambda t: float(t.get("created_at", 0)), reverse=True)
        return result[:limit]

    def get_events(self, trace_id: str, *, since_seq: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                return []
            return [e for e in trace["events"] if int(e.get("seq", 0)) >= since_seq]

    def get_latest_seq(self, trace_id: str) -> int:
        with self._lock:
            trace = self._traces.get(trace_id)
            if not trace or not trace["events"]:
                return -1
            return int(trace["events"][-1].get("seq", 0))

    def _public_trace_locked(self, trace: dict[str, Any]) -> dict[str, Any]:
        """Build public trace dict — must only be called while holding self._lock."""
        events = trace.get("events", [])
        latest_seq = int(events[-1].get("seq", 0)) if events else -1
        return {
            "trace_id": trace["trace_id"],
            "snake_id": trace.get("snake_id"),
            "session_id": trace.get("session_id"),
            "status": trace.get("status", "unknown"),
            "created_at": trace.get("created_at"),
            "updated_at": trace.get("updated_at"),
            "finished_at": trace.get("finished_at"),
            "event_count": len(events),
            "latest_seq": latest_seq,
        }

    def _evict_old(self) -> None:
        now = time.time()
        expired = [
            tid for tid, t in self._traces.items()
            if now - float(t.get("updated_at", 0)) > self.ttl_seconds
        ]
        for tid in expired:
            self._traces.pop(tid, None)
            if tid in self._order:
                self._order.remove(tid)
        while len(self._traces) >= self.max_traces and self._order:
            oldest = self._order.pop(0)
            self._traces.pop(oldest, None)


# ── TraceRecorder ─────────────────────────────────────────────────────────────


class TraceRecorder:
    """Writes structured trace events to a TraceStore for one reply run."""

    def __init__(self, store: TraceStore, trace_id: str, *, max_preview_chars: int = 4000) -> None:
        self._store = store
        self._trace_id = trace_id
        self._max_preview_chars = max_preview_chars

    @property
    def trace_id(self) -> str:
        return self._trace_id

    def event(
        self,
        phase: str,
        title: str,
        *,
        status: str = "completed",
        summary: str = "",
        details: dict[str, Any] | None = None,
        input_preview: Any = None,
        output_preview: Any = None,
        started_at: float | None = None,
        duration_ms: float | None = None,
        error: str | None = None,
        snake_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        now = time.time()
        redaction_applied = False

        safe_details: dict[str, Any] = {}
        if details:
            v, r = redact_value(details, self._max_preview_chars)
            safe_details = v if isinstance(v, dict) else {"_raw": v}
            redaction_applied = redaction_applied or r

        safe_input: Any = None
        if input_preview is not None:
            safe_input, r = redact_value(input_preview, self._max_preview_chars)
            redaction_applied = redaction_applied or r

        safe_output: Any = None
        if output_preview is not None:
            safe_output, r = redact_value(output_preview, self._max_preview_chars)
            redaction_applied = redaction_applied or r

        safe_error: str | None = None
        if error:
            err_val, r = redact_value(error[:600], self._max_preview_chars)
            safe_error = str(err_val)
            redaction_applied = redaction_applied or r

        evt: dict[str, Any] = {
            "trace_id": self._trace_id,
            "event_id": str(uuid.uuid4()),
            "snake_id": snake_id,
            "session_id": session_id,
            "parent_event_id": None,
            "seq": 0,
            "phase": phase,
            "title": title,
            "status": status,
            "started_at": started_at or now,
            "finished_at": now,
            "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
            "summary": str(summary or "")[:500],
            "details": safe_details,
            "input_preview": safe_input,
            "output_preview": safe_output,
            "raw_available": False,
            "redaction_applied": redaction_applied,
            "error": safe_error,
        }
        self._store.add_event(self._trace_id, evt)


# ── Singleton ─────────────────────────────────────────────────────────────────

_store_instance: TraceStore | None = None
_store_lock = threading.Lock()


def get_trace_store() -> TraceStore:
    global _store_instance
    if _store_instance is None:
        with _store_lock:
            if _store_instance is None:
                _store_instance = _make_store()
    return _store_instance


def _make_store() -> TraceStore:
    try:
        from agent.routes.ai_snake_config import _current_config
        cfg = _current_config()
        return TraceStore(
            max_traces=int(cfg.get("ai_snake_trace_max_traces") or 50),
            max_events_per_trace=int(cfg.get("ai_snake_trace_max_events_per_trace") or 500),
            ttl_seconds=int(cfg.get("ai_snake_trace_ttl_seconds") or 86400),
            max_preview_chars=int(cfg.get("ai_snake_trace_max_preview_chars") or 4000),
        )
    except Exception:
        return TraceStore()


def reset_store_for_testing() -> None:
    """Replace the singleton with a fresh instance (test helper)."""
    global _store_instance
    with _store_lock:
        _store_instance = TraceStore(max_traces=10, max_events_per_trace=50, ttl_seconds=3600)
