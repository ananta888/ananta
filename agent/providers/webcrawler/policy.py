"""Hub-side action classification for the external Webcrawler adapter."""

from __future__ import annotations

from dataclasses import dataclass

from .config import AnantaWebcrawlerProviderConfig

_READ_ACTIONS = {"list_profiles", "get_profile_status", "run_profile", "fetch", "search"}
_SESSION_ACTIONS = {"login", "click", "replay", "session_refresh"}
_WRITE_ACTIONS = {"form_submit", "submit", "order", "book", "post", "delete", "publish_profile", "record_flow"}


@dataclass(frozen=True, slots=True)
class WebcrawlerPolicyDecision:
    allowed: bool
    action: str
    risk_class: str
    reason_code: str
    authorization_required: bool

    def public(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "action": self.action,
            "risk_class": self.risk_class,
            "reason_code": self.reason_code,
            "authorization_required": self.authorization_required,
            "policy_version": "ananta-webcrawler-policy-v1",
        }


class WebcrawlerActionPolicy:
    def __init__(self, config: AnantaWebcrawlerProviderConfig) -> None:
        self._config = config

    def decide(self, action: str, *, authorization_granted: bool = False) -> WebcrawlerPolicyDecision:
        normalized = str(action or "").strip().lower()
        if normalized in _READ_ACTIONS:
            return WebcrawlerPolicyDecision(True, normalized, "low", "webcrawler_read_allowed", False)
        if normalized == "validate_profile":
            allowed = self._config.profile_mutation_enabled and authorization_granted
            return WebcrawlerPolicyDecision(
                allowed,
                normalized,
                "high",
                "webcrawler_policy_authorized" if allowed else "webcrawler_profile_mutation_blocked",
                True,
            )
        if normalized == "record_flow":
            allowed = self._config.recording_enabled and authorization_granted
            return WebcrawlerPolicyDecision(
                allowed,
                normalized,
                "high",
                "webcrawler_policy_authorized" if allowed else "webcrawler_recording_blocked",
                True,
            )
        if normalized in _SESSION_ACTIONS:
            allowed = self._config.policy_mode == "controlled" and authorization_granted
            return WebcrawlerPolicyDecision(
                allowed,
                normalized,
                "high",
                "webcrawler_policy_authorized" if allowed else "webcrawler_session_action_blocked",
                True,
            )
        if normalized in _WRITE_ACTIONS:
            allowed = (
                self._config.policy_mode == "controlled"
                and self._config.profile_mutation_enabled
                and authorization_granted
            )
            return WebcrawlerPolicyDecision(
                allowed,
                normalized,
                "critical",
                "webcrawler_policy_authorized" if allowed else "webcrawler_write_action_blocked",
                True,
            )
        return WebcrawlerPolicyDecision(False, normalized, "critical", "webcrawler_action_unknown", True)
