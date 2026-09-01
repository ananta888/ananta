"""Strict, secret-reference-only configuration for ananta-webcrawler."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

_MODES = {"disabled", "external_url", "managed_process", "managed_docker_compose"}
_ROLES = {"backend_provider", "tool_provider"}
_POLICY_MODES = {"read_only", "strict", "controlled"}
_FALLBACK_POLICIES = {"disabled", "semantic_match_only"}
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_SERVICE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_DEFAULT_ROUTING_TAGS = frozenset({"web", "browser", "replay", "website_ai", "external_api_wrapper"})
_CONFIG_KEYS = frozenset(
    {
        "enabled",
        "mode",
        "base_url",
        "api_key_env",
        "roles",
        "policy_mode",
        "fallback_policy",
        "healthcheck_path",
        "allowed_profiles",
        "blocked_profiles",
        "routing_tags",
        "repo_path",
        "startup_command",
        "docker_compose_file",
        "docker_compose_service",
        "managed_lifecycle_enabled",
        "recording_enabled",
        "profile_mutation_enabled",
        "request_timeout_seconds",
        "startup_timeout_seconds",
        "health_poll_seconds",
        "supports_streaming",
        "supports_models_endpoint",
        "model_semantics",
    }
)


class WebcrawlerConfigError(ValueError):
    """Configuration is ambiguous, unsafe, or incomplete."""


def _string_set(value: object, reason: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise WebcrawlerConfigError(reason)
    values = frozenset(str(item).strip() for item in value if str(item).strip())
    if len(values) > 256 or any(len(item) > 128 for item in values):
        raise WebcrawlerConfigError(reason)
    return values


def _bounded_number(value: object, *, default: float, minimum: float, maximum: float, reason: str) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WebcrawlerConfigError(reason)
    result = float(value)
    if not minimum <= result <= maximum:
        raise WebcrawlerConfigError(reason)
    return result


def _boolean(value: object, *, default: bool, reason: str) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise WebcrawlerConfigError(reason)
    return value


def _base_url(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise WebcrawlerConfigError("webcrawler_base_url_invalid")
    if parsed.query or parsed.fragment:
        raise WebcrawlerConfigError("webcrawler_base_url_invalid")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


@dataclass(frozen=True, slots=True)
class AnantaWebcrawlerProviderConfig:
    enabled: bool = False
    mode: str = "disabled"
    base_url: str | None = None
    api_key_env: str | None = None
    roles: frozenset[str] = frozenset()
    policy_mode: str = "strict"
    fallback_policy: str = "semantic_match_only"
    healthcheck_path: str = "/models"
    allowed_profiles: frozenset[str] = frozenset()
    blocked_profiles: frozenset[str] = frozenset()
    routing_tags: frozenset[str] = _DEFAULT_ROUTING_TAGS
    repo_path: str | None = None
    startup_command: tuple[str, ...] = ()
    docker_compose_file: str | None = None
    docker_compose_service: str | None = None
    managed_lifecycle_enabled: bool = False
    recording_enabled: bool = False
    profile_mutation_enabled: bool = False
    supports_streaming: bool = True
    supports_models_endpoint: bool = True
    model_semantics: str = "profile_name"
    request_timeout_seconds: float = 30.0
    startup_timeout_seconds: float = 60.0
    health_poll_seconds: float = 0.5

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "AnantaWebcrawlerProviderConfig":
        raw = dict(value or {})
        if set(raw) - _CONFIG_KEYS:
            raise WebcrawlerConfigError("webcrawler_config_unknown_field")
        enabled = raw.get("enabled", False)
        if not isinstance(enabled, bool):
            raise WebcrawlerConfigError("webcrawler_enabled_invalid")
        mode = str(raw.get("mode") or ("external_url" if enabled and raw.get("base_url") else "disabled")).strip()
        if mode not in _MODES:
            raise WebcrawlerConfigError("webcrawler_mode_invalid")
        if not enabled:
            mode = "disabled"

        roles = _string_set(raw.get("roles", []), "webcrawler_roles_invalid")
        if not roles <= _ROLES:
            raise WebcrawlerConfigError("webcrawler_roles_invalid")
        if enabled and not roles:
            raise WebcrawlerConfigError("webcrawler_roles_required")
        policy_mode = str(raw.get("policy_mode") or "strict").strip()
        if policy_mode not in _POLICY_MODES:
            raise WebcrawlerConfigError("webcrawler_policy_mode_invalid")
        fallback = str(raw.get("fallback_policy") or "semantic_match_only").strip()
        if fallback not in _FALLBACK_POLICIES:
            raise WebcrawlerConfigError("webcrawler_fallback_policy_invalid")

        base_url = _base_url(raw.get("base_url"))
        healthcheck_path = str(raw.get("healthcheck_path") or "/models").strip()
        if (
            not healthcheck_path.startswith("/")
            or "?" in healthcheck_path
            or "#" in healthcheck_path
            or ".." in healthcheck_path.split("/")
        ):
            raise WebcrawlerConfigError("webcrawler_healthcheck_path_invalid")
        api_key_env = str(raw.get("api_key_env") or "").strip() or None
        if api_key_env and not _ENV_NAME.fullmatch(api_key_env):
            raise WebcrawlerConfigError("webcrawler_api_key_env_invalid")

        allowed = _string_set(raw.get("allowed_profiles"), "webcrawler_allowed_profiles_invalid")
        blocked = _string_set(raw.get("blocked_profiles"), "webcrawler_blocked_profiles_invalid")
        if allowed & blocked:
            raise WebcrawlerConfigError("webcrawler_profile_policy_conflict")
        routing_tags = _string_set(raw.get("routing_tags"), "webcrawler_routing_tags_invalid") or _DEFAULT_ROUTING_TAGS

        repo_path = str(raw.get("repo_path") or "").strip() or None
        command_value = raw.get("startup_command") or []
        if isinstance(command_value, str) or not isinstance(command_value, (list, tuple)):
            raise WebcrawlerConfigError("webcrawler_startup_command_must_be_argv")
        command = tuple(str(item).strip() for item in command_value if str(item).strip())
        compose_file = str(raw.get("docker_compose_file") or "").strip() or None
        compose_service = str(raw.get("docker_compose_service") or "").strip() or None
        lifecycle = raw.get("managed_lifecycle_enabled", False)
        if not isinstance(lifecycle, bool):
            raise WebcrawlerConfigError("webcrawler_managed_lifecycle_invalid")

        if mode != "disabled" and base_url is None:
            raise WebcrawlerConfigError("webcrawler_base_url_required")
        if mode == "managed_process" and (not repo_path or not command or not lifecycle):
            raise WebcrawlerConfigError("webcrawler_managed_process_config_incomplete")
        if mode == "managed_docker_compose" and (not compose_file or not compose_service or not lifecycle):
            raise WebcrawlerConfigError("webcrawler_compose_config_incomplete")
        if compose_service and not _SERVICE_NAME.fullmatch(compose_service):
            raise WebcrawlerConfigError("webcrawler_compose_service_invalid")
        if repo_path and not Path(repo_path).is_absolute():
            raise WebcrawlerConfigError("webcrawler_repo_path_must_be_absolute")
        if compose_file and not Path(compose_file).is_absolute():
            raise WebcrawlerConfigError("webcrawler_compose_file_must_be_absolute")
        supports_streaming = _boolean(
            raw.get("supports_streaming"),
            default=True,
            reason="webcrawler_supports_streaming_invalid",
        )
        supports_models = _boolean(
            raw.get("supports_models_endpoint"),
            default=True,
            reason="webcrawler_supports_models_endpoint_invalid",
        )
        model_semantics = str(raw.get("model_semantics") or "profile_name").strip()
        if enabled and (
            not supports_streaming
            or not supports_models
            or model_semantics != "profile_name"
        ):
            raise WebcrawlerConfigError("webcrawler_openai_contract_invalid")

        return cls(
            enabled=enabled,
            mode=mode,
            base_url=base_url,
            api_key_env=api_key_env,
            roles=roles,
            policy_mode=policy_mode,
            fallback_policy=fallback,
            healthcheck_path=healthcheck_path,
            allowed_profiles=allowed,
            blocked_profiles=blocked,
            routing_tags=routing_tags,
            repo_path=repo_path,
            startup_command=command,
            docker_compose_file=compose_file,
            docker_compose_service=compose_service,
            managed_lifecycle_enabled=lifecycle,
            recording_enabled=_boolean(
                raw.get("recording_enabled"),
                default=False,
                reason="webcrawler_recording_enabled_invalid",
            ),
            profile_mutation_enabled=_boolean(
                raw.get("profile_mutation_enabled"),
                default=False,
                reason="webcrawler_profile_mutation_enabled_invalid",
            ),
            supports_streaming=supports_streaming,
            supports_models_endpoint=supports_models,
            model_semantics=model_semantics,
            request_timeout_seconds=_bounded_number(
                raw.get("request_timeout_seconds"),
                default=30.0,
                minimum=0.1,
                maximum=300.0,
                reason="webcrawler_request_timeout_invalid",
            ),
            startup_timeout_seconds=_bounded_number(
                raw.get("startup_timeout_seconds"),
                default=60.0,
                minimum=1.0,
                maximum=900.0,
                reason="webcrawler_startup_timeout_invalid",
            ),
            health_poll_seconds=_bounded_number(
                raw.get("health_poll_seconds"),
                default=0.5,
                minimum=0.05,
                maximum=10.0,
                reason="webcrawler_health_poll_invalid",
            ),
        )

    def endpoint(self, path: str) -> str:
        if not self.base_url:
            raise WebcrawlerConfigError("webcrawler_base_url_required")
        normalized = str(path or "").strip()
        if not normalized.startswith("/") or "?" in normalized or "#" in normalized:
            raise WebcrawlerConfigError("webcrawler_endpoint_path_invalid")
        return f"{self.base_url}{normalized}"

    def profile_allowed(self, profile: str) -> bool:
        name = str(profile or "").strip()
        return bool(
            name and name not in self.blocked_profiles and (not self.allowed_profiles or name in self.allowed_profiles)
        )
