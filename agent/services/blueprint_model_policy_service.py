"""
BlueprintModelPolicyService — AMR-012

Extracts and validates the optional `model_policy` block from a BlueprintRole's
JSON `config` field and converts it into routing rule dicts usable by ModelProfileResolver.

Blueprint role config example:
  {
    "model_policy": {
      "preferred_profile_id": "local-coder",
      "model_role": "coder",
      "allow_cloud": false,
      "required_capabilities": ["supports_json"],
      "fallback_profile_ids": ["local-llama"]
    }
  }
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_ALLOWED_REQUIRED_CAPABILITIES = frozenset({"supports_tools", "supports_json", "supports_streaming"})


@dataclass
class BlueprintModelPolicy:
    blueprint_role_id: str
    preferred_profile_id: str | None = None
    model_role: str = "any"
    allow_cloud: bool | None = None
    required_capabilities: list[str] = field(default_factory=list)
    fallback_profile_ids: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def extract_blueprint_model_policy(
    blueprint_role_id: str,
    role_config: dict[str, Any] | None,
) -> BlueprintModelPolicy | None:
    """
    Extract model_policy from a BlueprintRole.config dict.
    Returns None if no model_policy is configured.
    """
    if not isinstance(role_config, dict):
        return None
    raw = role_config.get("model_policy")
    if not isinstance(raw, dict):
        return None
    if not raw:
        return None

    preferred_profile_id = str(raw.get("preferred_profile_id") or "").strip() or None
    model_role = str(raw.get("model_role") or "any").strip() or "any"
    allow_cloud = raw.get("allow_cloud")
    if allow_cloud is not None:
        allow_cloud = bool(allow_cloud)

    raw_caps = list(raw.get("required_capabilities") or [])
    required_capabilities = [
        str(cap).strip()
        for cap in raw_caps
        if str(cap).strip() in _ALLOWED_REQUIRED_CAPABILITIES
    ]
    unknown_caps = [str(c).strip() for c in raw_caps if str(c).strip() not in _ALLOWED_REQUIRED_CAPABILITIES]
    if unknown_caps:
        logger.warning(
            "blueprint_model_policy: unknown capabilities ignored: %s (role_id=%s)",
            unknown_caps,
            blueprint_role_id,
        )

    fallback_profile_ids = [
        str(pid).strip()
        for pid in list(raw.get("fallback_profile_ids") or [])
        if str(pid).strip()
    ]

    return BlueprintModelPolicy(
        blueprint_role_id=blueprint_role_id,
        preferred_profile_id=preferred_profile_id,
        model_role=model_role,
        allow_cloud=allow_cloud,
        required_capabilities=required_capabilities,
        fallback_profile_ids=fallback_profile_ids,
        raw=raw,
    )


def build_routing_context_kwargs_from_blueprint_policy(
    policy: BlueprintModelPolicy,
) -> dict[str, Any]:
    """
    Convert a BlueprintModelPolicy into kwargs for RoutingContext.
    Only non-None/non-empty fields are included.
    """
    kwargs: dict[str, Any] = {}
    if policy.model_role and policy.model_role != "any":
        kwargs["model_role"] = policy.model_role
    if "supports_tools" in policy.required_capabilities:
        kwargs["requires_tools"] = True
    if "supports_json" in policy.required_capabilities:
        kwargs["requires_json"] = True
    if "supports_streaming" in policy.required_capabilities:
        kwargs["requires_streaming"] = True
    return kwargs


def apply_blueprint_policy_to_routing_rules(
    policy: BlueprintModelPolicy,
    routing_rules_dict: dict[str, Any],
) -> dict[str, Any]:
    """
    Inject blueprint policy as a blueprint-scoped routing rule entry.
    Returns updated routing_rules_dict (does not mutate input).
    """
    if not policy.preferred_profile_id:
        return routing_rules_dict
    rules = list(routing_rules_dict.get("routing_rules") or [])
    rules.append({
        "rule_id": f"blueprint_role:{policy.blueprint_role_id}",
        "profile_id": policy.preferred_profile_id,
        "blueprint_id": policy.blueprint_role_id,
        "enabled": True,
        "reason": f"blueprint_role_model_policy for {policy.blueprint_role_id}",
    })
    return {**routing_rules_dict, "routing_rules": rules}
