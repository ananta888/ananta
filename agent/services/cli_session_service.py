from __future__ import annotations

import threading
import time
import uuid
from copy import deepcopy


class CliSessionService:
    """In-memory stateful session store for iterative CLI backends."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}

    def create_session(
        self,
        *,
        backend: str,
        model: str | None = None,
        metadata: dict | None = None,
        task_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict:
        now = time.time()
        session_id = f"cli-{uuid.uuid4()}"
        payload = {
            "id": session_id,
            "backend": str(backend or "").strip().lower(),
            "model": str(model or "").strip() or None,
            "task_id": str(task_id or "").strip() or None,
            "conversation_id": str(conversation_id or "").strip() or None,
            "created_at": now,
            "updated_at": now,
            "status": "active",
            "turn_count": 0,
            "history": [],
            "metadata": dict(metadata or {}),
        }
        with self._lock:
            self._sessions[session_id] = payload
        return deepcopy(payload)

    def list_sessions(self, *, backend: str | None = None, include_history: bool = False, limit: int = 100) -> list[dict]:
        backend_filter = str(backend or "").strip().lower() or None
        max_items = max(1, min(int(limit or 100), 1000))
        with self._lock:
            items = list(self._sessions.values())
        if backend_filter:
            items = [item for item in items if str(item.get("backend") or "").strip().lower() == backend_filter]
        items.sort(key=lambda item: float(item.get("updated_at") or 0), reverse=True)
        trimmed = items[:max_items]
        if include_history:
            return [deepcopy(item) for item in trimmed]
        result: list[dict] = []
        for item in trimmed:
            entry = dict(item)
            entry.pop("history", None)
            result.append(entry)
        return deepcopy(result)

    def get_session(self, session_id: str, *, include_history: bool = True) -> dict | None:
        sid = str(session_id or "").strip()
        if not sid:
            return None
        with self._lock:
            item = self._sessions.get(sid)
            if item is None:
                return None
            if include_history:
                return deepcopy(item)
            entry = dict(item)
            entry.pop("history", None)
            return deepcopy(entry)

    def find_active_session(
        self,
        *,
        backend: str | None = None,
        scope_key: str | None = None,
        scope_kind: str | None = None,
    ) -> dict | None:
        backend_name = str(backend or "").strip().lower() or None
        scope_value = str(scope_key or "").strip() or None
        scope_type = str(scope_kind or "").strip().lower() or None
        with self._lock:
            items = list(self._sessions.values())
        items.sort(key=lambda item: float(item.get("updated_at") or 0), reverse=True)
        for item in items:
            if str(item.get("status") or "").strip().lower() != "active":
                continue
            if backend_name and str(item.get("backend") or "").strip().lower() != backend_name:
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            if scope_value and str(metadata.get("scope_key") or "").strip() != scope_value:
                continue
            if scope_type and str(metadata.get("scope_kind") or "").strip().lower() != scope_type:
                continue
            return deepcopy(item)
        return None

    def close_session(self, session_id: str) -> dict | None:
        sid = str(session_id or "").strip()
        if not sid:
            return None
        with self._lock:
            item = self._sessions.get(sid)
            if item is None:
                return None
            item["status"] = "closed"
            item["updated_at"] = time.time()
            return deepcopy(item)

    def delete_session(self, session_id: str) -> bool:
        sid = str(session_id or "").strip()
        if not sid:
            return False
        with self._lock:
            return self._sessions.pop(sid, None) is not None

    def append_turn(
        self,
        *,
        session_id: str,
        prompt: str,
        output: str,
        model: str | None = None,
        trace_id: str | None = None,
        metadata: dict | None = None,
    ) -> dict | None:
        sid = str(session_id or "").strip()
        if not sid:
            return None
        with self._lock:
            item = self._sessions.get(sid)
            if item is None:
                return None
            now = time.time()
            turn_id = f"turn-{uuid.uuid4()}"
            turn = {
                "id": turn_id,
                "index": int(item.get("turn_count") or 0) + 1,
                "created_at": now,
                "prompt": str(prompt or ""),
                "output": str(output or ""),
                "model": str(model or "").strip() or item.get("model"),
                "trace_id": str(trace_id or "").strip() or None,
                "metadata": dict(metadata or {}),
            }
            history = list(item.get("history") or [])
            history.append(turn)
            item["history"] = history
            item["turn_count"] = len(history)
            item["updated_at"] = now
            if model:
                item["model"] = str(model).strip()
            return deepcopy(turn)

    def update_session(
        self,
        session_id: str,
        *,
        model: str | None = None,
        status: str | None = None,
        metadata_updates: dict | None = None,
    ) -> dict | None:
        sid = str(session_id or "").strip()
        if not sid:
            return None
        with self._lock:
            item = self._sessions.get(sid)
            if item is None:
                return None
            if model is not None:
                item["model"] = str(model).strip() or None
            if status is not None:
                item["status"] = str(status).strip() or item.get("status")
            if metadata_updates:
                merged = dict(item.get("metadata") or {})
                merged.update(dict(metadata_updates))
                item["metadata"] = merged
            item["updated_at"] = time.time()
            return deepcopy(item)

    def build_prompt_with_history(self, *, session_id: str, prompt: str, max_turns: int = 8) -> str | None:
        session = self.get_session(session_id, include_history=True)
        if not session:
            return None
        turns = list(session.get("history") or [])
        keep = max(1, min(int(max_turns or 8), 50))
        turns = turns[-keep:]
        if not turns:
            return str(prompt or "")
        parts = [f"Session-ID: {session.get('id')}", "Kontext aus vorherigen Turns:"]
        for turn in turns:
            idx = int(turn.get("index") or 0)
            user_text = str(turn.get("prompt") or "").strip()
            assistant_text = str(turn.get("output") or "").strip()
            if user_text:
                parts.append(f"Turn {idx} User:\n{user_text}")
            if assistant_text:
                parts.append(f"Turn {idx} Assistant:\n{assistant_text}")
        parts.append("Neuer User-Turn:")
        parts.append(str(prompt or ""))
        return "\n\n".join(parts)

    def prune_sessions(self, *, max_sessions: int = 200) -> dict:
        limit = max(1, min(int(max_sessions or 200), 5000))
        with self._lock:
            items = sorted(self._sessions.items(), key=lambda kv: float((kv[1] or {}).get("updated_at") or 0), reverse=True)
            keep_ids = {sid for sid, _payload in items[:limit]}
            removed = 0
            for sid in list(self._sessions.keys()):
                if sid not in keep_ids:
                    self._sessions.pop(sid, None)
                    removed += 1
            return {"removed": removed, "remaining": len(self._sessions), "max_sessions": limit}

    def snapshot(self) -> dict:
        with self._lock:
            sessions = list(self._sessions.values())
        active = [item for item in sessions if str(item.get("status") or "").strip().lower() == "active"]
        return {
            "total": len(sessions),
            "active": len(active),
            "closed": len(sessions) - len(active),
            "updated_at": time.time(),
        }


cli_session_service = CliSessionService()


def get_cli_session_service() -> CliSessionService:
    return cli_session_service
