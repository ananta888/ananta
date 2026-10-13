"""Explicit Hub-owned headless model profile and current repository scope."""

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from agent.services.codecompass_retrieval_capability_service import (
    resolve_request_capability,
    verify_retrieval_capability,
)
from agent.services.meet_companion_task_tools import digest
from agent.services.meet_contract import MeetError
from agent.services.source_control_access_policy import HubSourcePrincipal


@dataclass(frozen=True)
class CompanionModelPermit:
    fingerprint: str
    revision: str
    environment_digest: str


class CompanionModelPolicyPort(Protocol):
    def authorize(self, scope, backend, model): ...


class HubCompanionModelPolicy:
    def __init__(self, app):
        self.app = app

    def projection(self, scope):
        profile = deepcopy(self.app.config.get("MEET_COMPANION_ASSIGNED_MODEL_PROFILE"))
        if not isinstance(profile, dict):
            raise MeetError("meet_companion_model_profile_denied", 403)
        self.authorize(scope, profile.get("backend"), profile.get("model"))
        return {key: profile[key] for key in ("backend", "model", "environment_digest")}

    def authorize(self, scope, backend, model):
        profile = self.app.config.get("MEET_COMPANION_ASSIGNED_MODEL_PROFILE")
        if (not isinstance(profile, dict)
                or set(profile) != {"backend", "model", "environment_digest", "auto_execute"}
                or profile["auto_execute"] is not True or not isinstance(backend, str)
                or backend not in {"ollama", "openai"}
                or backend != profile["backend"] or model != profile["model"]
                or not isinstance(model, str) or not 1 <= len(model) <= 160
                or not isinstance(profile["environment_digest"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", profile["environment_digest"])):
            raise MeetError("meet_companion_model_profile_denied", 403)
        try:
            principal = HubSourcePrincipal(scope.machine_subject, scope.tenant_id,
                                           scope.project_id, frozenset({"agent"}))
            capability = verify_retrieval_capability(resolve_request_capability(
                application=self.app, principal=principal, requested_scope={}))
            if not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", capability["revision"]):
                raise ValueError()
        except (TypeError, ValueError):
            raise MeetError("meet_companion_model_scope_denied", 403) from None
        stable = {key: value for key, value in capability.items()
                  if key not in {"issued_at_epoch", "expires_at_epoch", "capability_digest"}}
        return CompanionModelPermit(digest({"profile": profile, "scope": stable}), capability["revision"],
                                     profile["environment_digest"])
