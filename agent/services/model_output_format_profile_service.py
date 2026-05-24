from __future__ import annotations

from typing import Any

from agent.services.model_response_behavior_profile_service import get_model_response_behavior_profile_service
from agent.services.planning_model_profile_service import get_planning_model_profile_service

_FORMAT_ALIASES = {
    "strict_json": "json",
    "fenced_json": "json",
    "json": "json",
    "markdown": "markdown",
    "markdown_sections": "markdown",
    "yaml": "yaml",
}
_ALLOWED_FORMATS = {"json", "markdown", "yaml"}


class ModelOutputFormatProfileService:
    """Resolves model-adaptive planning output format with policy-safe precedence."""

    @staticmethod
    def _normalize_format(value: Any) -> str | None:
        normalized = str(value or "").strip().lower()
        mapped = _FORMAT_ALIASES.get(normalized, normalized)
        return mapped if mapped in _ALLOWED_FORMATS else None

    def resolve(
        self,
        *,
        planning_policy: dict[str, Any] | None,
        provider: str | None,
        model_name: str | None,
        runtime_profile_name: str | None = None,
        run_telemetry: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        policy = dict(planning_policy or {})
        telemetry = dict(run_telemetry or {})
        reason_codes: list[str] = []

        accepted_raw = list(policy.get("accepted_output_formats") or [])
        accepted_formats: list[str] = []
        for item in accepted_raw:
            normalized = self._normalize_format(item)
            if normalized and normalized not in accepted_formats:
                accepted_formats.append(normalized)
        if accepted_formats:
            reason_codes.append("accepted_output_formats_policy")

        runtime_profiles = dict(policy.get("runtime_profiles") or {})
        runtime_profile = dict(runtime_profiles.get(str(runtime_profile_name or "").strip(), {}) or {})
        runtime_fmt = self._normalize_format(runtime_profile.get("preferred_output_format"))

        policy_fmt = self._normalize_format(policy.get("preferred_output_format"))
        profile = get_planning_model_profile_service().resolve_profile(provider=provider, model_name=model_name)
        profile_fmt = self._normalize_format(profile.get("preferred_output_format"))

        behavior = get_model_response_behavior_profile_service().resolve(provider=provider, model_name=model_name)
        behavior_fmt = self._normalize_format(behavior.get("preferred_output_format"))
        prompt_style = str(behavior.get("preferred_prompt_style") or "").strip().lower()
        if not behavior_fmt and prompt_style in {"markdown_friendly", "stepwise_then_json"}:
            behavior_fmt = "markdown"

        candidates = [
            ("policy", policy_fmt),
            ("runtime_profile", runtime_fmt),
            ("planning_model_profile", profile_fmt),
            ("behavior_profile", behavior_fmt),
            ("default", "json"),
        ]

        selected_source = "default"
        selected_format = "json"
        for source, candidate in candidates:
            if not candidate:
                continue
            if accepted_formats and candidate not in accepted_formats:
                continue
            selected_source = source
            selected_format = candidate
            reason_codes.append(f"selected:{source}")
            break

        parse_success = float(telemetry.get("parse_success") or 0.0)
        schema_violation = float(telemetry.get("schema_violation") or 0.0)
        constraint_loss = float(telemetry.get("constraint_loss") or 0.0)
        repair_attempts = int(telemetry.get("repair_attempts") or 0)
        if selected_format == "json" and (constraint_loss > 0.0 or schema_violation > 0.0):
            if "markdown" in accepted_formats or not accepted_formats:
                selected_format = "markdown"
                selected_source = "telemetry_fallback"
                reason_codes.append("constraint_loss_demotes_json")
        if selected_format == "json" and parse_success < 0.5 and repair_attempts >= 2:
            if "markdown" in accepted_formats or not accepted_formats:
                selected_format = "markdown"
                selected_source = "telemetry_fallback"
                reason_codes.append("parse_instability_demotes_json")

        return {
            "preferred_output_format": selected_format,
            "source": selected_source,
            "accepted_output_formats": accepted_formats,
            "reason_codes": reason_codes,
        }


_SERVICE = ModelOutputFormatProfileService()


def get_model_output_format_profile_service() -> ModelOutputFormatProfileService:
    return _SERVICE
