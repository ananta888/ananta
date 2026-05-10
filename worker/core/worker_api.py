"""Worker API layer: OpenAI-compatible facade, native RPC, and API exposure policy.

EW-T043: OpenAI-compatible /v1/chat/completions facade.
EW-T044: Ananta-native ExecutionEnvelope RPC endpoint.
EW-T048: API exposure policy + self-loop guard (hop count).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ── API exposure policy (EW-T048) ─────────────────────────────────────────────

class ApiExposureMode(str, Enum):
    disabled = "disabled"
    local_only = "local_only"
    internal = "internal"


@dataclass
class ApiExposurePolicy:
    """Controls whether the worker exposes any API. EW-T048.

    Disabled by default — must be explicitly enabled in config.
    Self-calls tracked via X-Ananta-Instance-ID and hop count.
    """
    mode: ApiExposureMode = ApiExposureMode.disabled
    instance_id: str = ""
    max_hops: int = 3

    def is_enabled(self) -> bool:
        return self.mode != ApiExposureMode.disabled

    def check_request(
        self,
        *,
        hop_count: int = 0,
        caller_instance_id: str = "",
    ) -> tuple[bool, str]:
        """Returns (allowed, reason). Blocks self-loops and hop overflow."""
        if not self.is_enabled():
            return False, "api_exposure_disabled"

        # Self-loop guard
        if self.instance_id and caller_instance_id == self.instance_id:
            return False, "self_loop_detected"

        # Hop count guard
        if hop_count >= self.max_hops:
            return False, "max_hops_exceeded"

        return True, "allow"


# ── OpenAI-compatible facade (EW-T043) ───────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    """Subset of OpenAI /v1/chat/completions request schema. EW-T043."""
    model: str = "ananta-worker"
    messages: list[ChatMessage]
    capability_token: str = ""    # Ananta capability token required
    stream: bool = False
    max_tokens: int | None = None

    @field_validator("messages")
    @classmethod
    def _non_empty(cls, v: list[ChatMessage]) -> list[ChatMessage]:
        if not v:
            raise ValueError("messages must not be empty")
        return v


class ChatFacadeResult(BaseModel):
    allowed: bool
    reason_code: str
    envelope_task_id: str = ""
    error: str = ""


class OpenAIChatFacade:
    """Maps OpenAI-style chat requests to ExecutionEnvelope. EW-T043.

    Requires a capability_token or Hub-issued session context.
    Rejects requests that cannot be mapped to a valid ExecutionEnvelope.
    """

    MAPPABLE_USE_CASES = frozenset({
        "code_review", "bug_fix", "explain_code", "write_tests",
        "summarize", "diagnose", "plan_task",
    })

    def __init__(self, policy: ApiExposurePolicy) -> None:
        self._policy = policy

    def handle(
        self,
        request: ChatCompletionRequest,
        *,
        hop_count: int = 0,
        caller_instance_id: str = "",
    ) -> ChatFacadeResult:
        # Policy check
        allowed, reason = self._policy.check_request(
            hop_count=hop_count, caller_instance_id=caller_instance_id
        )
        if not allowed:
            return ChatFacadeResult(allowed=False, reason_code=reason)

        # Capability token required
        if not request.capability_token.strip():
            return ChatFacadeResult(allowed=False, reason_code="missing_capability_token")

        # Map use-case from last user message
        last_user = next(
            (m.content for m in reversed(request.messages) if m.role == "user"), ""
        )
        use_case = self._classify_use_case(last_user)
        if not use_case:
            return ChatFacadeResult(
                allowed=False, reason_code="unmappable_chat_request",
                error="chat request cannot be mapped to a governed ExecutionEnvelope",
            )

        task_id = f"chat-{int(time.time() * 1000)}"
        return ChatFacadeResult(allowed=True, reason_code="facade_ok", envelope_task_id=task_id)

    def _classify_use_case(self, message: str) -> str | None:
        msg_lower = message.lower()
        if any(kw in msg_lower for kw in ["review", "check"]):
            return "code_review"
        if any(kw in msg_lower for kw in ["fix", "bug", "error"]):
            return "bug_fix"
        if any(kw in msg_lower for kw in ["explain", "what does"]):
            return "explain_code"
        if any(kw in msg_lower for kw in ["test", "spec"]):
            return "write_tests"
        if any(kw in msg_lower for kw in ["plan", "steps"]):
            return "plan_task"
        if any(kw in msg_lower for kw in ["summarize", "summary"]):
            return "summarize"
        return None


# ── Native RPC endpoint (EW-T044) ─────────────────────────────────────────────

class WorkerRPCMode(str, Enum):
    sync = "sync"
    async_job = "async_job"


class WorkerRPCRequest(BaseModel):
    """Ananta-native RPC request. EW-T044."""
    envelope: dict[str, Any]      # serialized ExecutionEnvelope
    mode: WorkerRPCMode = WorkerRPCMode.sync
    job_id: str = ""

    @field_validator("envelope")
    @classmethod
    def _envelope_non_empty(cls, v: dict[str, Any]) -> dict[str, Any]:
        if not v:
            raise ValueError("envelope must not be empty")
        if not v.get("task_id"):
            raise ValueError("envelope.task_id must be non-empty")
        return v


class WorkerRPCResponse(BaseModel):
    accepted: bool
    reason_code: str
    task_id: str = ""
    job_id: str = ""
    result: dict[str, Any] | None = None
    error: str = ""


class WorkerRPCEndpoint:
    """Validates and dispatches ExecutionEnvelope RPC calls. EW-T044.

    Supports sync (immediate result) and async_job (returns job_id, result polled later).
    """

    def __init__(self, policy: ApiExposurePolicy) -> None:
        self._policy = policy
        self._jobs: dict[str, dict[str, Any]] = {}

    def handle(
        self,
        request: WorkerRPCRequest,
        *,
        hop_count: int = 0,
        caller_instance_id: str = "",
    ) -> WorkerRPCResponse:
        # Policy check
        allowed, reason = self._policy.check_request(
            hop_count=hop_count, caller_instance_id=caller_instance_id
        )
        if not allowed:
            return WorkerRPCResponse(accepted=False, reason_code=reason)

        # Schema validation already done by Pydantic
        task_id = str(request.envelope.get("task_id", ""))

        if request.mode == WorkerRPCMode.async_job:
            job_id = request.job_id or f"job-{int(time.time() * 1000)}"
            self._jobs[job_id] = {"task_id": task_id, "status": "queued"}
            return WorkerRPCResponse(
                accepted=True, reason_code="job_queued",
                task_id=task_id, job_id=job_id,
            )

        # Sync mode — envelope is validated; actual execution is caller's responsibility
        return WorkerRPCResponse(
            accepted=True, reason_code="rpc_ok", task_id=task_id,
        )

    def job_status(self, job_id: str) -> dict[str, Any] | None:
        return self._jobs.get(job_id)
