"""Semantic-match-only routing for the external Webcrawler backend."""

from __future__ import annotations

from dataclasses import dataclass

from .config import AnantaWebcrawlerProviderConfig


@dataclass(frozen=True, slots=True)
class WebcrawlerRoutingDecision:
    selected: bool
    reason_code: str
    provider_id: str | None
    profile: str | None
    explicit: bool

    def public(self) -> dict[str, object]:
        return {
            "selected": self.selected,
            "reason_code": self.reason_code,
            "provider_id": self.provider_id,
            "profile": self.profile,
            "explicit": self.explicit,
            "routing_policy": "semantic_match_only",
        }


def route_webcrawler_task(
    config: AnantaWebcrawlerProviderConfig,
    *,
    task_kind: str,
    requested_provider: str | None = None,
    requested_profile: str | None = None,
) -> WebcrawlerRoutingDecision:
    provider_id = "ananta_webcrawler_openai"
    if not config.enabled or config.mode == "disabled" or "backend_provider" not in config.roles:
        return WebcrawlerRoutingDecision(False, "webcrawler_provider_disabled", None, None, False)
    explicit = str(requested_provider or "").strip().lower() == provider_id
    if explicit:
        if not requested_profile or not config.profile_allowed(requested_profile):
            return WebcrawlerRoutingDecision(False, "webcrawler_profile_policy_blocked", None, None, True)
        return WebcrawlerRoutingDecision(True, "webcrawler_explicit_selection", provider_id, requested_profile, True)
    normalized_kind = str(task_kind or "").strip().lower()
    if config.fallback_policy != "semantic_match_only" or normalized_kind not in config.routing_tags:
        return WebcrawlerRoutingDecision(False, "webcrawler_semantic_mismatch", None, None, False)
    if requested_profile and not config.profile_allowed(requested_profile):
        return WebcrawlerRoutingDecision(False, "webcrawler_profile_policy_blocked", None, None, False)
    return WebcrawlerRoutingDecision(
        True,
        "webcrawler_semantic_match",
        provider_id,
        requested_profile,
        False,
    )
