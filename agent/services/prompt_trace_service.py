"""PromptTrace data model, storage, and service. PTI-001, PTI-002."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_TRACE_FILE = "prompt_traces.jsonl"


# ── Data Model ────────────────────────────────────────────────────────────────

@dataclass
class PromptTrace:
    trace_id: str
    request_id: str | None
    idempotency_key: str | None
    goal_id: str | None
    task_id: str | None
    worker_id: str | None
    source_component: str
    provider: str | None
    transport_provider: str | None
    model: str | None
    endpoint_kind: str
    request_kind: str
    # Prompt fields
    final_prompt_redacted: str | None
    final_prompt_raw_ref: str | None
    messages_redacted: list[dict] | None
    messages_raw_ref: str | None
    prompt_hash_sha256: str | None
    raw_available: bool
    # Provenance
    template_chain: list[dict]
    overlay_chain: list[dict]
    model_profile: str | None
    optimizer_steps: list[dict]
    context_sources: list[dict]
    tool_definitions_hash: str | None
    selected_tools: list[str]
    # Runtime
    created_at: float
    started_at: float | None
    ended_at: float | None
    latency_ms: int | None
    success: bool | None
    error_type: str | None
    error_message: str | None
    usage: dict[str, Any]
    response_hash_sha256: str | None
    # Security
    redaction_policy_id: str | None
    redaction_applied: bool
    secrets_detected: int
    raw_access_policy: str
    sensitivity_level: str | None
    llm_scope: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "goal_id": self.goal_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "source_component": self.source_component,
            "provider": self.provider,
            "transport_provider": self.transport_provider,
            "model": self.model,
            "endpoint_kind": self.endpoint_kind,
            "request_kind": self.request_kind,
            "final_prompt_redacted": self.final_prompt_redacted,
            "final_prompt_raw_ref": self.final_prompt_raw_ref,
            "messages_redacted": self.messages_redacted,
            "messages_raw_ref": self.messages_raw_ref,
            "prompt_hash_sha256": self.prompt_hash_sha256,
            "raw_available": self.raw_available,
            "template_chain": self.template_chain,
            "overlay_chain": self.overlay_chain,
            "model_profile": self.model_profile,
            "optimizer_steps": self.optimizer_steps,
            "context_sources": self.context_sources,
            "tool_definitions_hash": self.tool_definitions_hash,
            "selected_tools": self.selected_tools,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "latency_ms": self.latency_ms,
            "success": self.success,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "usage": self.usage,
            "response_hash_sha256": self.response_hash_sha256,
            "redaction_policy_id": self.redaction_policy_id,
            "redaction_applied": self.redaction_applied,
            "secrets_detected": self.secrets_detected,
            "raw_access_policy": self.raw_access_policy,
            "sensitivity_level": self.sensitivity_level,
            "llm_scope": self.llm_scope,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PromptTrace":
        return cls(
            trace_id=d.get("trace_id") or str(uuid.uuid4()),
            request_id=d.get("request_id"),
            idempotency_key=d.get("idempotency_key"),
            goal_id=d.get("goal_id"),
            task_id=d.get("task_id"),
            worker_id=d.get("worker_id"),
            source_component=d.get("source_component") or "unknown",
            provider=d.get("provider"),
            transport_provider=d.get("transport_provider"),
            model=d.get("model"),
            endpoint_kind=d.get("endpoint_kind") or "chat_completions",
            request_kind=d.get("request_kind") or "generate",
            final_prompt_redacted=d.get("final_prompt_redacted"),
            final_prompt_raw_ref=d.get("final_prompt_raw_ref"),
            messages_redacted=d.get("messages_redacted"),
            messages_raw_ref=d.get("messages_raw_ref"),
            prompt_hash_sha256=d.get("prompt_hash_sha256"),
            raw_available=bool(d.get("raw_available", False)),
            template_chain=list(d.get("template_chain") or []),
            overlay_chain=list(d.get("overlay_chain") or []),
            model_profile=d.get("model_profile"),
            optimizer_steps=list(d.get("optimizer_steps") or []),
            context_sources=list(d.get("context_sources") or []),
            tool_definitions_hash=d.get("tool_definitions_hash"),
            selected_tools=list(d.get("selected_tools") or []),
            created_at=float(d.get("created_at") or time.time()),
            started_at=d.get("started_at"),
            ended_at=d.get("ended_at"),
            latency_ms=d.get("latency_ms"),
            success=d.get("success"),
            error_type=d.get("error_type"),
            error_message=d.get("error_message"),
            usage=dict(d.get("usage") or {}),
            response_hash_sha256=d.get("response_hash_sha256"),
            redaction_policy_id=d.get("redaction_policy_id"),
            redaction_applied=bool(d.get("redaction_applied", False)),
            secrets_detected=int(d.get("secrets_detected") or 0),
            raw_access_policy=d.get("raw_access_policy") or "deny",
            sensitivity_level=d.get("sensitivity_level"),
            llm_scope=d.get("llm_scope"),
        )


def prompt_hash(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def response_hash(text: str | None) -> str | None:
    return prompt_hash(text)


# ── Storage ───────────────────────────────────────────────────────────────────

class PromptTraceStorage:
    """JSONL-backed storage for PromptTrace records."""

    def __init__(self, data_dir: str | None = None) -> None:
        self._data_dir = data_dir

    def _get_path(self) -> str:
        d = self._data_dir
        if not d:
            from agent.utils import get_data_dir
            d = get_data_dir()
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, _TRACE_FILE)

    def append(self, trace: PromptTrace) -> None:
        path = self._get_path()
        try:
            import portalocker
            with portalocker.Lock(path, mode="a", encoding="utf-8", timeout=5) as f:
                f.write(json.dumps(trace.to_dict(), ensure_ascii=True) + "\n")
        except ImportError:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(trace.to_dict(), ensure_ascii=True) + "\n")
        except Exception as exc:
            logger.error("Failed to write prompt trace: %s", exc)

    def _iter_lines(self) -> list[dict]:
        path = self._get_path()
        if not os.path.exists(path):
            return []
        records: list[dict] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass  # skip corrupted lines
        except Exception as exc:
            logger.error("Failed to read prompt traces: %s", exc)
        return records

    def list(self, limit: int = 50, **filters) -> list[PromptTrace]:
        records = self._iter_lines()
        # Apply filters
        provider = filters.get("provider")
        model = filters.get("model")
        goal_id = filters.get("goal_id")
        task_id = filters.get("task_id")
        worker_id = filters.get("worker_id")
        success = filters.get("success")
        since = filters.get("since")

        out: list[dict] = []
        for r in records:
            if provider and r.get("provider") != provider:
                continue
            if model and r.get("model") != model:
                continue
            if goal_id and r.get("goal_id") != goal_id:
                continue
            if task_id and r.get("task_id") != task_id:
                continue
            if worker_id and r.get("worker_id") != worker_id:
                continue
            if success is not None and r.get("success") != success:
                continue
            if since is not None and (r.get("created_at") or 0) < since:
                continue
            out.append(r)

        # newest first
        out.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
        return [PromptTrace.from_dict(r) for r in out[:limit]]

    def get_by_trace_id(self, trace_id: str) -> PromptTrace | None:
        for r in self._iter_lines():
            if r.get("trace_id") == trace_id:
                return PromptTrace.from_dict(r)
        return None

    def get_by_request_id(self, request_id: str) -> PromptTrace | None:
        for r in self._iter_lines():
            if r.get("request_id") == request_id:
                return PromptTrace.from_dict(r)
        return None

    def find_by_goal_id(self, goal_id: str, limit: int = 100) -> list[PromptTrace]:
        return self.list(limit=limit, goal_id=goal_id)

    def find_by_task_id(self, task_id: str, limit: int = 100) -> list[PromptTrace]:
        return self.list(limit=limit, task_id=task_id)

    def delete_by_goal_id(self, goal_id: str) -> int:
        goal = str(goal_id or "").strip()
        if not goal:
            return 0
        path = self._get_path()
        if not os.path.exists(path):
            return 0
        deleted = 0
        kept_lines: list[str] = []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        payload = json.loads(stripped)
                    except json.JSONDecodeError:
                        kept_lines.append(line)
                        continue
                    if str(payload.get("goal_id") or "").strip() == goal:
                        deleted += 1
                        continue
                    kept_lines.append(line)
            with open(path, "w", encoding="utf-8") as handle:
                handle.writelines(kept_lines)
        except Exception as exc:
            logger.error("Failed to delete prompt traces for goal_id=%s: %s", goal, exc)
            return 0
        return deleted


# ── Service ───────────────────────────────────────────────────────────────────

class PromptTraceService:
    """Creates, finalizes, and stores PromptTrace records."""

    def __init__(self, storage: PromptTraceStorage | None = None) -> None:
        self._storage = storage or PromptTraceStorage()

    def _is_enabled(self) -> bool:
        try:
            from agent.config import settings
            return bool(getattr(settings, "prompt_trace_enabled", True))
        except Exception:
            return True

    def _store_raw(self) -> bool:
        try:
            from agent.config import settings
            return bool(getattr(settings, "prompt_trace_store_raw_prompts", False))
        except Exception:
            return False

    def _max_raw_chars(self) -> int:
        try:
            from agent.config import settings
            return int(getattr(settings, "prompt_trace_max_raw_chars", 8000))
        except Exception:
            return 8000

    def create_trace(
        self,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
        goal_id: str | None = None,
        task_id: str | None = None,
        worker_id: str | None = None,
        source_component: str = "llm_integration",
        provider: str | None = None,
        transport_provider: str | None = None,
        model: str | None = None,
        endpoint_kind: str = "chat_completions",
        request_kind: str = "generate",
        prompt: str | None = None,
        messages: list[dict] | None = None,
        template_chain: list[dict] | None = None,
        overlay_chain: list[dict] | None = None,
        model_profile: str | None = None,
        optimizer_steps: list[dict] | None = None,
        context_sources: list[dict] | None = None,
        tools: list | None = None,
        llm_scope: str | None = None,
        sensitivity_level: str | None = None,
    ) -> PromptTrace:
        from agent.services.prompt_redaction_service import get_redaction_service

        trace_id = str(uuid.uuid4())
        now = time.time()

        redaction_svc = get_redaction_service()

        # Redact prompt
        prompt_redacted = None
        if prompt:
            result = redaction_svc.redact(prompt)
            prompt_redacted = result.redacted_text
            secrets_found = result.secrets_detected
        else:
            secrets_found = 0

        # Redact messages
        messages_redacted = None
        if messages:
            redacted_msgs = []
            for msg in messages:
                r = redaction_svc.redact(str(msg.get("content") or ""))
                secrets_found += r.secrets_detected
                redacted_msgs.append({"role": msg.get("role", "user"), "content": r.redacted_text})
            messages_redacted = redacted_msgs

        # Hash
        p_hash = prompt_hash(prompt) if prompt else None
        if not p_hash and messages:
            combined = "\n".join(m.get("content") or "" for m in messages)
            p_hash = prompt_hash(combined)

        # Tool definitions hash
        tool_hash = None
        tool_names: list[str] = []
        if tools:
            tool_names = [t.get("name") or t.get("function", {}).get("name") or "" for t in tools if isinstance(t, dict)]
            tool_hash = hashlib.sha256(json.dumps(tools, sort_keys=True, ensure_ascii=True).encode()).hexdigest()

        trace = PromptTrace(
            trace_id=trace_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            goal_id=goal_id,
            task_id=task_id,
            worker_id=worker_id,
            source_component=source_component,
            provider=provider,
            transport_provider=transport_provider,
            model=model,
            endpoint_kind=endpoint_kind,
            request_kind=request_kind,
            final_prompt_redacted=prompt_redacted,
            final_prompt_raw_ref=None,
            messages_redacted=messages_redacted,
            messages_raw_ref=None,
            prompt_hash_sha256=p_hash,
            raw_available=False,
            template_chain=list(template_chain or []),
            overlay_chain=list(overlay_chain or []),
            model_profile=model_profile,
            optimizer_steps=list(optimizer_steps or []),
            context_sources=list(context_sources or []),
            tool_definitions_hash=tool_hash,
            selected_tools=tool_names,
            created_at=now,
            started_at=now,
            ended_at=None,
            latency_ms=None,
            success=None,
            error_type=None,
            error_message=None,
            usage={},
            response_hash_sha256=None,
            redaction_policy_id="default",
            redaction_applied=True,
            secrets_detected=secrets_found,
            raw_access_policy="deny",
            sensitivity_level=sensitivity_level,
            llm_scope=llm_scope,
        )
        return trace

    def finalize_trace(
        self,
        trace: PromptTrace,
        *,
        success: bool,
        response_text: str | None = None,
        usage: dict | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> PromptTrace:
        ended_at = time.time()
        latency = max(0, int((ended_at - (trace.started_at or ended_at)) * 1000))
        r_hash = response_hash(response_text) if response_text else None

        updated = PromptTrace(
            trace_id=trace.trace_id,
            request_id=trace.request_id,
            idempotency_key=trace.idempotency_key,
            goal_id=trace.goal_id,
            task_id=trace.task_id,
            worker_id=trace.worker_id,
            source_component=trace.source_component,
            provider=trace.provider,
            transport_provider=trace.transport_provider,
            model=trace.model,
            endpoint_kind=trace.endpoint_kind,
            request_kind=trace.request_kind,
            final_prompt_redacted=trace.final_prompt_redacted,
            final_prompt_raw_ref=trace.final_prompt_raw_ref,
            messages_redacted=trace.messages_redacted,
            messages_raw_ref=trace.messages_raw_ref,
            prompt_hash_sha256=trace.prompt_hash_sha256,
            raw_available=trace.raw_available,
            template_chain=trace.template_chain,
            overlay_chain=trace.overlay_chain,
            model_profile=trace.model_profile,
            optimizer_steps=trace.optimizer_steps,
            context_sources=trace.context_sources,
            tool_definitions_hash=trace.tool_definitions_hash,
            selected_tools=trace.selected_tools,
            created_at=trace.created_at,
            started_at=trace.started_at,
            ended_at=ended_at,
            latency_ms=latency,
            success=success,
            error_type=error_type,
            error_message=error_message,
            usage=dict(usage or {}),
            response_hash_sha256=r_hash,
            redaction_policy_id=trace.redaction_policy_id,
            redaction_applied=trace.redaction_applied,
            secrets_detected=trace.secrets_detected,
            raw_access_policy=trace.raw_access_policy,
            sensitivity_level=trace.sensitivity_level,
            llm_scope=trace.llm_scope,
        )
        return updated

    def store(self, trace: PromptTrace) -> None:
        if not self._is_enabled():
            return
        try:
            self._storage.append(trace)
        except Exception as exc:
            logger.error("PromptTraceService.store failed: %s", exc)

    def list_traces(self, limit: int = 50, **filters) -> list[PromptTrace]:
        try:
            return self._storage.list(limit=limit, **filters)
        except Exception as exc:
            logger.error("PromptTraceService.list_traces failed: %s", exc)
            return []

    def get_trace(self, trace_id: str) -> PromptTrace | None:
        try:
            return self._storage.get_by_trace_id(trace_id)
        except Exception as exc:
            logger.error("PromptTraceService.get_trace failed: %s", exc)
            return None

    def find_by_goal_id(self, goal_id: str, limit: int = 100) -> list[PromptTrace]:
        try:
            return self._storage.find_by_goal_id(goal_id, limit=limit)
        except Exception as exc:
            logger.error("PromptTraceService.find_by_goal_id failed: %s", exc)
            return []

    def delete_by_goal_id(self, goal_id: str) -> int:
        try:
            return int(self._storage.delete_by_goal_id(goal_id))
        except Exception as exc:
            logger.error("PromptTraceService.delete_by_goal_id failed: %s", exc)
            return 0

    @property
    def storage(self) -> PromptTraceStorage:
        return self._storage


_SERVICE: PromptTraceService | None = None


def get_prompt_trace_service() -> PromptTraceService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = PromptTraceService()
    return _SERVICE
