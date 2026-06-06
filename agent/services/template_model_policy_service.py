"""
TemplateModelPolicyService — AMR-013

Resolves the effective model_profile_id for a template-scoped invocation.

Resolution priority (highest first):
  1. Per-template entry in `template_model_overrides` config key
     (already supported legacy string format "provider::model" or new "profile_id")
  2. Per-template `model_policy` block in new-style `template_model_policies` config key
  3. None — caller falls back to other resolver ranks

Config example (AGENT_CONFIG):
  template_model_overrides:
    "my-template": "local-coder"   # new-style: direct profile_id

  template_model_policies:
    "my-template":
      preferred_profile_id: "local-coder"
      model_role: "coder"
      allow_cloud: false
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TemplateModelPolicy:
    template_id: str
    preferred_profile_id: str | None
    model_role: str = "any"
    allow_cloud: bool | None = None
    source: str = "config"


class TemplateModelPolicyService:
    """
    Reads template model policy from AGENT_CONFIG.

    Can be used standalone (pass config dict) or via `build_from_app_config()`.
    """

    def __init__(self, agent_config: dict[str, Any] | None = None):
        self._cfg = dict(agent_config or {})

    def resolve(self, template_id: str) -> TemplateModelPolicy | None:
        """Return policy for template_id, or None if not configured."""
        if not template_id:
            return None

        # 1. legacy template_model_overrides (string override)
        overrides = self._cfg.get("template_model_overrides") or {}
        if isinstance(overrides, dict):
            raw = overrides.get(template_id)
            if raw and isinstance(raw, str):
                pid = str(raw).strip()
                if pid:
                    return TemplateModelPolicy(
                        template_id=template_id,
                        preferred_profile_id=pid,
                        source="template_model_overrides",
                    )

        # 2. new-style template_model_policies
        policies = self._cfg.get("template_model_policies") or {}
        if isinstance(policies, dict):
            raw_policy = policies.get(template_id)
            if isinstance(raw_policy, dict) and raw_policy:
                pid = str(raw_policy.get("preferred_profile_id") or "").strip() or None
                model_role = str(raw_policy.get("model_role") or "any").strip() or "any"
                allow_cloud = raw_policy.get("allow_cloud")
                if allow_cloud is not None:
                    allow_cloud = bool(allow_cloud)
                return TemplateModelPolicy(
                    template_id=template_id,
                    preferred_profile_id=pid,
                    model_role=model_role,
                    allow_cloud=allow_cloud,
                    source="template_model_policies",
                )

        return None

    def all_policies(self) -> list[TemplateModelPolicy]:
        """Return all configured template policies."""
        template_ids: set[str] = set()
        for key in ("template_model_overrides", "template_model_policies"):
            block = self._cfg.get(key) or {}
            if isinstance(block, dict):
                template_ids.update(block.keys())
        return [p for tid in sorted(template_ids) if (p := self.resolve(tid)) is not None]

    @classmethod
    def build_from_app_config(cls, agent_config: dict[str, Any]) -> "TemplateModelPolicyService":
        return cls(agent_config=agent_config)
