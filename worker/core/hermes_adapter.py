from __future__ import annotations

import os
import time
import hashlib
from typing import Any

from agent.services.hermes_worker_profile import get_default_hermes_profile
from worker.core.context_resolver import ContextBlock
from worker.core.diagnostics import AuditEmitter
from worker.core.execution_envelope import ArtifactRef, ExecutionEnvelope, WorkerResult, WorkerResultStatus, make_trace
from worker.core.hermes_adapter_config import HermesAdapterConfig
from worker.core.hermes_context_converter import convert_context_blocks_to_prompt
from worker.core.hermes_http_client import HermesClientConfig, HermesClientError, HermesHttpClient
from worker.core.hermes_output_parser import (
    HermesParseResult,
    parse_hermes_json_output,
    validate_payload_for_mode,
)
from worker.core.hermes_prompting import build_governed_system_prompt


class HermesAdapter:
    """Governed Hermes adapter for proposal/review/summarization modes."""

    id = "hermes"
    _TRANSIENT_ERRORS = {"hermes_timeout", "hermes_rate_limited", "hermes_server_error"}

    def __init__(
        self,
        *,
        config: HermesAdapterConfig | None = None,
        client: HermesHttpClient | None = None,
        audit_emitter: AuditEmitter | None = None,
    ) -> None:
        self.config = config or HermesAdapterConfig()
        self.profile = get_default_hermes_profile()
        self.client = client or HermesHttpClient(
            config=HermesClientConfig(
                base_url=self.config.base_url or "http://127.0.0.1:0",
                timeout_seconds=self.config.timeout_seconds,
                default_model=self.config.default_model or "hermes-default",
            )
        )
        self.audit_emitter = audit_emitter
        self._last_error_code: str = ""

    def health(self) -> dict[str, Any]:
        if not self.config.feature_flag_enabled:
            return self._health_payload("disabled", "disabled_by_feature_flag")
        if not self.config.enabled:
            return self._health_payload("disabled", "disabled_config")
        if not self.config.base_url or not self.config.default_model:
            return self._health_payload("misconfigured", "misconfigured_adapter")
        api_key = os.getenv(self.config.api_key_env or "", "")
        try:
            self.client.health(api_key=api_key)
        except HermesClientError as exc:
            if exc.code == "hermes_unauthorized":
                self._last_error_code = exc.code
                return self._health_payload("unauthorized", exc.code)
            if exc.code in {"hermes_timeout", "hermes_connection_error", "hermes_not_found"}:
                self._last_error_code = exc.code
                return self._health_payload("unavailable", exc.code)
            self._last_error_code = exc.code
            return self._health_payload("degraded", exc.code)
        self._last_error_code = ""
        return self._health_payload("ready", "health_ok")

    def propose(self, envelope: ExecutionEnvelope, *, context_blocks: list[ContextBlock] | None = None) -> WorkerResult:
        return self.plan_only(envelope, context_blocks=context_blocks or [])

    def review(self, envelope: ExecutionEnvelope, *, context_blocks: list[ContextBlock] | None = None) -> WorkerResult:
        if not (
            envelope.has_capability("review")
            or envelope.has_capability("code_review")
            or envelope.has_capability("verify")
        ):
            return WorkerResult.denied(envelope.task_id, "missing_capability", make_trace(envelope))
        return self._execute_mode("review", envelope, context_blocks or [])

    def summarize(self, envelope: ExecutionEnvelope, *, context_blocks: list[ContextBlock] | None = None) -> WorkerResult:
        return self._execute_mode("summarize", envelope, context_blocks or [])

    def patch_propose(self, envelope: ExecutionEnvelope, *, context_blocks: list[ContextBlock] | None = None) -> WorkerResult:
        return self._execute_mode("patch_propose", envelope, context_blocks or [])

    def research_limited(self, envelope: ExecutionEnvelope, *, context_blocks: list[ContextBlock] | None = None) -> WorkerResult:
        return self._execute_mode("research_limited", envelope, context_blocks or [])

    def plan_only(self, envelope: ExecutionEnvelope, *, context_blocks: list[ContextBlock] | None = None) -> WorkerResult:
        return self._execute_mode("plan_only", envelope, context_blocks or [])

    def _execute_mode(self, mode: str, envelope: ExecutionEnvelope, context_blocks: list[ContextBlock]) -> WorkerResult:
        trace = make_trace(envelope)
        self._audit("routing_selected", envelope=envelope, mode=mode)
        if not self.config.feature_flag_enabled:
            return WorkerResult.denied(envelope.task_id, "disabled_by_feature_flag", trace)
        if not self.config.enabled:
            return self._degraded(envelope, trace, "disabled_config")
        if mode in self.config.blocked_task_kinds:
            return WorkerResult.denied(envelope.task_id, "task_kind_blocked", trace)
        if mode == "patch_propose" and not envelope.has_capability("patch_propose"):
            return WorkerResult.denied(envelope.task_id, "missing_capability", trace)
        if mode == "summarize" and not envelope.has_capability("summarize"):
            return WorkerResult.denied(envelope.task_id, "missing_capability", trace)
        if mode == "research_limited" and not envelope.has_capability("research_limited"):
            return WorkerResult.denied(envelope.task_id, "missing_capability", trace)
        if mode in {"plan_only", "summarize"} and not envelope.has_capability("planning"):
            return WorkerResult.denied(envelope.task_id, "missing_capability", trace)
        if mode == "research_limited":
            if envelope.network_scope.allow_all:
                return WorkerResult.denied(envelope.task_id, "network_unrestricted_denied", trace)
            if not context_blocks:
                return self._degraded(envelope, trace, "research_context_missing")

        endpoint_class = _classify_endpoint(self.config.base_url)
        trace.append("hermes_endpoint_classification", reason_code=None, endpoint_classification=endpoint_class)
        if endpoint_class == "cloud" and not envelope.model_policy.cloud_allowed:
            return WorkerResult.denied(envelope.task_id, "cloud_blocked", trace)
        if endpoint_class == "cloud" and any(str(getattr(b, "sensitivity", "")).lower().endswith(("secret", "confidential")) for b in context_blocks):
            return WorkerResult.denied(envelope.task_id, "sensitivity_blocked", trace)

        model_selection = self._select_model(envelope)
        if model_selection.get("blocked"):
            trace.append("hermes_model_blocked", reason_code="model_blocked", requested_model=model_selection.get("requested_model"))
            return WorkerResult.denied(envelope.task_id, "model_blocked", trace)

        converted = convert_context_blocks_to_prompt(
            context_blocks,
            max_context_chars=self.config.max_context_chars,
            allow_sensitive=bool(self.config.cloud_allowed and envelope.model_policy.cloud_allowed),
        )
        if not converted.has_required_context:
            return self._degraded(envelope, trace, "context_missing_or_sensitive")
        if converted.suspicious:
            trace.append("hermes_suspicious_context", reason_code="suspicious_context_blocked", findings=list(converted.suspicious))
        context_hash = _hash_text(user_text := converted.prompt_text)

        schema = _schema_for_mode(mode)
        system_prompt = build_governed_system_prompt(
            envelope=envelope,
            allowed_mode=mode,
            denied_operations=list(envelope.denied_operations),
            output_schema=schema,
        )
        user_prompt = user_text
        prompt_hash = _hash_text(system_prompt + "\n" + user_prompt)
        trace.append(
            "hermes_prompt_built",
            reason_code=None,
            allowed_mode=mode,
            context_included=len(converted.included),
            context_skipped=len(converted.skipped),
            context_truncated=len(converted.truncated),
            requested_model=model_selection["requested_model"],
            effective_model=model_selection["effective_model"],
            context_hash=context_hash,
            prompt_hash=prompt_hash,
        )

        start = time.monotonic()
        parse_retry_used = False
        attempt = 0
        while True:
            attempt += 1
            try:
                self._audit("provider_call", envelope=envelope, mode=mode, endpoint_classification=endpoint_class, model=model_selection["effective_model"])
                response = self.client.chat_completions(
                    api_key=os.getenv(self.config.api_key_env or "", ""),
                    system_message=system_prompt,
                    user_message=user_prompt,
                    model=str(model_selection["effective_model"]),
                )
                parse_result = self._parse_model_response(response)
                if parse_result.ok:
                    payload = parse_result.payload
                    validation_error = validate_payload_for_mode(payload, mode=mode if mode != "plan_only" else "summarize")
                    if validation_error:
                        parse_result = HermesParseResult(ok=False, reason_code=validation_error, raw_snippet=parse_result.raw_snippet)
                    else:
                        duration_ms = int((time.monotonic() - start) * 1000)
                        trace.append(
                            "hermes_result_parsed",
                            reason_code=None,
                            retry_count=attempt - 1,
                            total_duration_ms=duration_ms,
                            parse_retry_used=parse_retry_used,
                            requested_model=model_selection["requested_model"],
                            effective_model=model_selection["effective_model"],
                            response_hash=_hash_text(str(payload)),
                        )
                        return self._success_result(mode, envelope, trace, payload, converted)

                if self.config.strict_json_required and self.config.parse_retry_enabled and not parse_retry_used:
                    parse_retry_used = True
                    user_prompt = _build_parse_retry_prompt(user_prompt, response, schema)
                    continue
                trace.append("hermes_parse_error", reason_code=parse_result.reason_code, parse_retry_used=parse_retry_used)
                self._last_error_code = parse_result.reason_code
                self._audit("policy_denied", envelope=envelope, mode=mode, reason_code=parse_result.reason_code)
                return self._failed(envelope, trace, parse_result.reason_code)
            except HermesClientError as exc:
                transient = exc.code in self._TRANSIENT_ERRORS
                if transient and (attempt - 1) < self.config.max_retries:
                    continue
                duration_ms = int((time.monotonic() - start) * 1000)
                trace.append("hermes_remote_error", reason_code=exc.code, retry_count=attempt - 1, total_duration_ms=duration_ms)
                self._last_error_code = exc.code
                self._audit("provider_call", envelope=envelope, mode=mode, reason_code=exc.code, status="error")
                return self._failed(envelope, trace, exc.code)

    def _select_model(self, envelope: ExecutionEnvelope) -> dict[str, Any]:
        requested = str(envelope.model_policy.preferred_model or "").strip()
        effective = requested or self.config.default_model
        blocked = effective in set(self.config.blocked_models)
        return {"requested_model": requested or None, "effective_model": effective, "blocked": blocked}

    def _parse_model_response(self, payload: dict[str, Any]) -> HermesParseResult:
        content = _extract_text_content(payload)
        return parse_hermes_json_output(content)

    def _success_result(
        self,
        mode: str,
        envelope: ExecutionEnvelope,
        trace: Any,
        payload: dict[str, Any],
        converted: Any,
    ) -> WorkerResult:
        artifact_id = f"hermes-{mode}-{envelope.task_id}"
        kind = "plan_artifact" if mode == "plan_only" else ("review_artifact" if mode == "review" else "verification_artifact")
        summary = str(payload.get("summary") or f"Hermes {mode} output")
        findings = payload.get("findings")
        if mode == "review" and not findings:
            summary = f"{summary} (uncertainty: incomplete_context)"
        artifact = ArtifactRef(
            artifact_id=artifact_id,
            kind=kind,
            provenance=f"{envelope.task_id}:hermes:{mode}",
            summary=summary,
            metadata={
                "source": "hermes",
                "adapter_version": "v1",
                "model": envelope.model_policy.preferred_model or self.config.default_model,
                "content_hash": _hash_text(str(payload)),
                "context_hash": _hash_text(converted.prompt_text),
                "requires_approval_for_apply": bool(payload.get("requires_approval_for_apply", mode == "patch_propose")),
                "touched_files": list(payload.get("touched_files") or []),
                "uncertainty": payload.get("uncertainty"),
            },
        )
        if mode == "patch_propose":
            artifact.kind = "patch_artifact"  # type: ignore[misc]
            artifact.metadata["requires_approval_for_apply"] = True
        elif mode == "summarize":
            artifact.kind = "summary_artifact"  # type: ignore[misc]
        elif mode == "research_limited":
            artifact.kind = "research_artifact"  # type: ignore[misc]
        return WorkerResult(
            task_id=envelope.task_id,
            status=WorkerResultStatus.success,
            summary=summary,
            artifacts=[artifact],
            trace_bundle=trace,
            policy_observations=[
                f"allowed_mode:{mode}",
                f"context_included:{len(converted.included)}",
                f"findings_count:{len(findings) if isinstance(findings, list) else 0}",
            ],
            warnings=["incomplete_context_warning"] if (mode == "review" and not findings) else [],
            no_side_effects_confirmed=True,
        )

    def _failed(self, envelope: ExecutionEnvelope, trace: Any, reason_code: str) -> WorkerResult:
        status = WorkerResultStatus.failed
        if reason_code == "hermes_timeout":
            summary = "Hermes request timed out"
        elif reason_code == "hermes_rate_limited":
            summary = "Hermes rate limited"
        elif reason_code.startswith("parse_error"):
            summary = f"Hermes parse_error: {reason_code}"
        else:
            summary = f"Hermes remote error: {reason_code}"
        return WorkerResult(
            task_id=envelope.task_id,
            status=status,
            summary=summary,
            trace_bundle=trace,
            policy_observations=[reason_code],
            warnings=["hermes_execution_failed"],
            no_side_effects_confirmed=True,
        )

    def _degraded(self, envelope: ExecutionEnvelope, trace: Any, reason_code: str) -> WorkerResult:
        trace.append("hermes_degraded", reason_code=reason_code, adapter_id=self.id)
        return WorkerResult(
            task_id=envelope.task_id,
            status=WorkerResultStatus.degraded,
            summary=f"Hermes adapter degraded: {reason_code}",
            trace_bundle=trace,
            policy_observations=[reason_code],
            warnings=["hermes_degraded"],
            no_side_effects_confirmed=True,
        )

    def _health_payload(self, status: str, reason_code: str) -> dict[str, Any]:
        return {
            "status": status,
            "reason_code": reason_code,
            "adapter_id": self.id,
            "base_url": self.config.base_url,
            "selected_model": self.config.default_model,
            "api_key_value": "[REDACTED]",
        }

    def diagnostics(self) -> dict[str, Any]:
        health = self.health()
        return {
            "enabled": self.config.enabled,
            "feature_flag_enabled": self.config.feature_flag_enabled,
            "health_state": health.get("status"),
            "endpoint_classification": _classify_endpoint(self.config.base_url),
            "selected_default_model": self.config.default_model,
            "allowed_task_kinds": list(self.config.allowed_task_kinds),
            "blocked_task_kinds": list(self.config.blocked_task_kinds),
            "last_error_code": self._last_error_code or health.get("reason_code"),
            "cloud_allowed": self.config.cloud_allowed,
            "reason_code": health.get("reason_code"),
            "api_key_value": "[REDACTED]",
        }

    def _audit(self, event_type: str, *, envelope: ExecutionEnvelope, mode: str, reason_code: str | None = None, **payload: Any) -> None:
        if self.audit_emitter is None:
            return
        self.audit_emitter.emit(
            event_type,
            correlation_id=envelope.audit_correlation_id,
            reason_code=reason_code,
            task_id=envelope.task_id,
            adapter_id=self.id,
            mode=mode,
            **payload,
        )


def _extract_text_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message")
            if isinstance(msg, dict):
                return str(msg.get("content") or "")
    return str(payload.get("content") or "")


def _build_parse_retry_prompt(original_prompt: str, previous_response: dict[str, Any], schema: dict[str, Any]) -> str:
    snippet = _extract_text_content(previous_response)[:800]
    return (
        f"{original_prompt}\n\n"
        "Previous response was malformed JSON. Return exactly one JSON object matching this schema.\n"
        f"Schema: {schema}\n"
        f"Previous sanitized response: {snippet}"
    )


def _schema_for_mode(mode: str) -> dict[str, Any]:
    base = {
        "type": "object",
        "required": [
            "status",
            "artifact_type",
            "summary",
            "findings",
            "risks",
            "suggested_tests",
            "confidence",
            "requires_approval_for_apply",
            "no_side_effects_claimed",
        ],
    }
    if mode == "patch_propose":
        base["required"] = list(base["required"]) + ["touched_files"]
    if mode == "research_limited":
        base["required"] = list(base["required"]) + ["claims"]
    return base


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _classify_endpoint(base_url: str) -> str:
    u = str(base_url or "").strip().lower()
    if "localhost" in u or "127.0.0.1" in u or u.startswith("http://10.") or u.startswith("http://192.168.") or u.startswith("http://172.16."):
        return "local" if ("localhost" in u or "127.0.0.1" in u) else "private_network"
    return "cloud"
