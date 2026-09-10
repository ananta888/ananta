"""Bounded model-context projection through the established Hub CAP service."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol

from agent.services.native_context_chunks import native_context_chunk_fields
from ananta_contracts.context_access_policy import (
    ContextAccessPolicy,
    ContextAccessRule,
    ContextBlockAccessDecision,
    Decision,
    DestinationContext,
    ModelScope,
    Sensitivity,
    SourceType,
)
from ananta_contracts.native_context_bundle import MAX_NATIVE_CONTEXT_BYTES, native_context_digest


class NativeContextBlockPolicyPort(Protocol):
    def get_decision(
        self, policy: ContextAccessPolicy, block_metadata: dict[str, Any], destination: DestinationContext,
    ) -> ContextBlockAccessDecision: ...

    def match_source(self, rule: ContextAccessRule, block_metadata: dict[str, Any]) -> bool: ...

    def detect_sensitivity(self, content: str, source_ref: str) -> Sensitivity: ...

    def redact_content(self, content: str) -> str: ...

    def summarize_content(self, content: str) -> str: ...


class NativeContextBlockProjector:
    def __init__(self, policy: NativeContextBlockPolicyPort) -> None:
        self._policy = policy

    def project(
        self, *, chunks: Any, policy_document: Mapping[str, Any], policy_version: int,
        destination: DestinationContext,
    ) -> str:
        policy = _policy(policy_document, version=policy_version)
        if not isinstance(chunks, list) or not 1 <= len(chunks) <= 32:
            raise ValueError("native_context_chunks_required")
        projected: list[dict[str, str]] = []
        for chunk in chunks:
            block = self._block(chunk)
            matched = [rule for rule in policy.rules if self._policy.match_source(rule, block)]
            if any(rule.approval_required for rule in matched):
                raise ValueError("native_context_approval_required")
            # Read/write permission is not implicit permission to send to an LLM.
            if policy.defaults.get("send_allowed") is not True and not any(
                rule.send_allowed is True for rule in matched
            ):
                raise ValueError("native_context_send_grant_required")
            decision = self._policy.get_decision(policy, block, destination)
            if (
                not isinstance(decision, ContextBlockAccessDecision) or not isinstance(decision.decision, Decision)
                or decision.decision not in (Decision.allow, Decision.allow_redacted, Decision.allow_summary_only)
                or decision.approval_requirement or decision.allowed_destination is False
                or decision.denied_destination is True
            ):
                raise ValueError("native_context_policy_denied")
            content = block["content"]
            if decision.decision is Decision.allow_redacted:
                content = self._policy.redact_content(content)
            elif decision.decision is Decision.allow_summary_only:
                content = self._policy.summarize_content(content)
            if not isinstance(content, str):
                raise ValueError("native_context_transformation_invalid")
            projected.append({"source_ref": block["source_ref"], "content": content})
        text = json.dumps(projected, ensure_ascii=True, separators=(",", ":"))
        if len(text.encode("utf-8")) > MAX_NATIVE_CONTEXT_BYTES:
            raise ValueError("native_context_content_invalid")
        return text

    def _block(self, raw: Any) -> dict[str, Any]:
        content, source_ref, source_type, sensitivity = native_context_chunk_fields(raw)
        if (
            not isinstance(content, str) or not content or len(content.encode("utf-8")) > MAX_NATIVE_CONTEXT_BYTES
            or not isinstance(source_ref, str) or not source_ref or len(source_ref) > 1024
            or "://" in source_ref or any(ord(c) < 32 for c in source_ref)
        ):
            raise ValueError("native_context_chunk_invalid")
        detected = self._policy.detect_sensitivity(content, source_ref)
        if detected is Sensitivity.secret or source_type in (SourceType.secret_file, SourceType.env_file):
            sensitivity = Sensitivity.secret
        elif sensitivity is None:
            sensitivity = detected
        return {
            "block_id": str(raw.get("block_id") or source_ref), "source_ref": source_ref,
            "source_type": source_type.value, "sensitivity": sensitivity, "content": content,
            "content_hash": native_context_digest(content),
        }


def _policy(document: Mapping[str, Any], *, version: int) -> ContextAccessPolicy:
    """Normalize persisted CAP enum values, without accepting truthy booleans."""
    rules = document.get("rules")
    defaults = document.get("defaults", {})
    if not isinstance(rules, list) or len(rules) > 128 or not isinstance(defaults, Mapping):
        raise ValueError("native_context_policy_document_invalid")
    if any(type(value) is not bool for value in defaults.values()):
        raise ValueError("native_context_policy_document_invalid")
    parsed = []
    for raw in rules:
        if not isinstance(raw, Mapping):
            raise ValueError("native_context_policy_document_invalid")
        values = dict(raw)
        for name in ("allowed_worker_kinds", "denied_worker_kinds", "allowed_runtime_kinds", "denied_runtime_kinds",
                     "allowed_provider_locations", "denied_provider_locations", "reason_tags"):
            if name in values and (
                not isinstance(values[name], list) or len(values[name]) > 128
                or any(not isinstance(value, str) or not value or len(value) > 256 for value in values[name])
            ):
                raise ValueError("native_context_policy_document_invalid")
        if values.get("source_match") is not None and not isinstance(values["source_match"], str):
            raise ValueError("native_context_policy_document_invalid")
        for name in ("read_allowed", "write_allowed", "send_allowed", "cloud_allowed", "external_worker_allowed",
                     "redaction_required", "summarization_allowed", "approval_required"):
            if values.get(name) is not None and type(values[name]) is not bool:
                raise ValueError("native_context_policy_document_invalid")
        try:
            for name, enum in (("source_types", SourceType), ("allowed_model_scopes", ModelScope),
                               ("denied_model_scopes", ModelScope)):
                if name in values:
                    if not isinstance(values[name], list):
                        raise ValueError("invalid_list")
                    values[name] = [enum(value) for value in values[name]]
            if values.get("sensitivity") is not None:
                values["sensitivity"] = Sensitivity(values["sensitivity"])
            parsed.append(ContextAccessRule(**values))
        except (TypeError, ValueError) as exc:
            raise ValueError("native_context_policy_document_invalid") from exc
    return ContextAccessPolicy(str(document.get("policy_id") or ""), version, str(document.get("scope") or "project"),
                               rules=parsed, defaults=dict(defaults))
