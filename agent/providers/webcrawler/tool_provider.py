"""ToolResult adapter for the external Webcrawler provider."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

from agent.services.tools._evidence import build_evidence_entry, build_tool_result

from .backend_provider import AnantaWebcrawlerBackendProvider, WebcrawlerProviderError
from .config import AnantaWebcrawlerProviderConfig
from .policy import WebcrawlerActionPolicy

_TOOLS = {
    "webcrawler.list_profiles": "list_profiles",
    "webcrawler.run_profile": "run_profile",
    "webcrawler.get_profile_status": "get_profile_status",
    "webcrawler.record_flow": "record_flow",
    "webcrawler.validate_profile": "validate_profile",
    "webcrawler.publish_profile": "publish_profile",
}
_ADAPTER_PATHS = {
    "record_flow": "/adapter/record-flow",
    "validate_profile": "/adapter/validate-profile",
    "publish_profile": "/adapter/publish-profile",
}


class AnantaWebcrawlerToolProvider:
    def __init__(
        self,
        config: AnantaWebcrawlerProviderConfig,
        backend: AnantaWebcrawlerBackendProvider,
        policy: WebcrawlerActionPolicy | None = None,
        *,
        monotonic=time.monotonic,
    ) -> None:
        self._config = config
        self._backend = backend
        self._policy = policy or WebcrawlerActionPolicy(config)
        self._monotonic = monotonic

    def catalog(self) -> list[dict[str, Any]]:
        if not self._config.enabled or "tool_provider" not in self._config.roles:
            return []
        rows = [
            {"name": "webcrawler.list_profiles", "risk_class": "low"},
            {"name": "webcrawler.run_profile", "risk_class": "low"},
            {"name": "webcrawler.get_profile_status", "risk_class": "low"},
        ]
        if self._config.recording_enabled:
            rows.append({"name": "webcrawler.record_flow", "risk_class": "high"})
        if self._config.profile_mutation_enabled:
            rows.extend(
                [
                    {"name": "webcrawler.validate_profile", "risk_class": "high"},
                    {"name": "webcrawler.publish_profile", "risk_class": "critical"},
                ]
            )
        return rows

    def run(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        tool_call_id: str,
        authorization_granted: bool = False,
    ) -> dict[str, Any]:
        action = _TOOLS.get(str(tool_name or "").strip())
        if action is None:
            return build_tool_result(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                status="error",
                error="unknown_webcrawler_tool",
            )
        if not self._config.enabled or "tool_provider" not in self._config.roles:
            return build_tool_result(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                status="policy_blocked",
                error=(
                    "webcrawler_disabled"
                    if not self._config.enabled
                    else "webcrawler_tool_role_disabled"
                ),
            )
        policy_action = action
        if action == "run_profile":
            policy_action = str(arguments.get("action") or "run_profile").strip().lower()
        decision = self._policy.decide(policy_action, authorization_granted=authorization_granted)
        if not decision.allowed:
            return build_tool_result(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                status="policy_blocked",
                risk_class=decision.risk_class,
                error=decision.reason_code,
                policy_decision=decision.public(),
                data=self._audit(policy_action, None, 0, False),
            )

        started = self._monotonic()
        profile = str(arguments.get("profile") or "").strip() or None
        try:
            result = self._execute(action, profile=profile, arguments=arguments)
        except WebcrawlerProviderError as exc:
            duration_ms = max(0, int((self._monotonic() - started) * 1000))
            return build_tool_result(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                status="error",
                risk_class=decision.risk_class,
                error=exc.reason_code,
                policy_decision=decision.public(),
                data=self._audit(policy_action, profile, duration_ms, False),
            )
        duration_ms = max(0, int((self._monotonic() - started) * 1000))
        evidence = []
        for tool_result in result.pop("tool_results", []):
            entry, _ = build_evidence_entry(
                kind="webcrawler_tool_result",
                path=profile,
                source="ananta_webcrawler_openai",
                excerpt=json.dumps(tool_result, sort_keys=True, ensure_ascii=False),
                max_excerpt_chars=2000,
            )
            evidence.append(entry)
        return build_tool_result(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            status="ok",
            risk_class=decision.risk_class,
            evidence=evidence,
            data={**result, **self._audit(policy_action, profile, duration_ms, True)},
            policy_decision=decision.public(),
            max_total_chars=8000,
        )

    def _execute(
        self,
        action: str,
        *,
        profile: str | None,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        if action == "list_profiles":
            return {"profiles": self._backend.list_profiles()}
        if action == "get_profile_status":
            if not profile:
                raise WebcrawlerProviderError("webcrawler_profile_required")
            match = next(
                (item for item in self._backend.list_profiles() if item["id"] == profile),
                None,
            )
            if match is None:
                raise WebcrawlerProviderError("webcrawler_profile_not_found")
            return {"profile_status": match, "health": self._backend.health().status}
        if action == "run_profile":
            if not profile:
                raise WebcrawlerProviderError("webcrawler_profile_required")
            messages = arguments.get("messages")
            if not isinstance(messages, list):
                prompt = str(arguments.get("prompt") or "").strip()
                if not prompt:
                    raise WebcrawlerProviderError("webcrawler_prompt_required")
                messages = [{"role": "user", "content": prompt}]
            completion = self._backend.complete(profile=profile, messages=messages)
            return {
                "profile": profile,
                "content": completion.content,
                "diagnostics": completion.diagnostics,
                "tool_results": list(completion.tool_results),
            }
        if not profile:
            raise WebcrawlerProviderError("webcrawler_profile_required")
        adapter_path = _ADAPTER_PATHS[action]
        request_payload = (
            dict(arguments.get("request") or {})
            if isinstance(arguments.get("request"), Mapping)
            else {}
        )
        try:
            request_size = len(
                json.dumps(request_payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            )
        except (TypeError, ValueError, RecursionError) as exc:
            raise WebcrawlerProviderError("webcrawler_adapter_request_invalid") from exc
        if request_size > 1_000_000:
            raise WebcrawlerProviderError("webcrawler_adapter_request_too_large")
        payload = {
            "profile": profile,
            "request": request_payload,
        }
        return {"adapter_result": self._backend.adapter_action(path=adapter_path, payload=payload)}

    def _audit(
        self,
        action: str,
        profile: str | None,
        duration_ms: int,
        success: bool,
    ) -> dict[str, Any]:
        return {
            "audit": {
                "profile": profile,
                "endpoint": self._config.base_url,
                "mode": self._config.mode,
                "action": action,
                "duration_ms": duration_ms,
                "success": success,
            }
        }
